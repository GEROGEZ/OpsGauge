"""Read-only adapters for printed computation artifacts, not natural-language reasoning.

No code evaluation: the grammar reads successful tool stdout (JSON, labelled
phase grids, lifecycle rows, per-instance peak tables). Unknown layouts earn no
credit. Snapshot values are used only to verify, never to fill missing output.
"""
import ast
import datetime as dt
import json
import re

from .engine import close, instant, iso, same_row, period_equal, typed_row, verify

SCORING_VERSION = 'l2_observable_work_v2_json_pointer_v1'
TIME = r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)'
NUM = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
OWNER = r'[A-Za-z0-9_.-]+-[A-Za-z0-9]+-[A-Za-z0-9]+'


def successful_outputs(path):
    if not path.is_file():
        return []
    events = []
    for index, line in enumerate(path.read_text().splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            if line.lstrip().startswith('{'):
                raise ValueError('corrupt_trace_jsonl')
            continue
        if not isinstance(event, dict):
            continue
        item = event.get('item') or {}
        if (event.get('type') == 'item.completed' and item.get('type') == 'command_execution'
                and item.get('exit_code') == 0):
            if 'Traceback (most recent call last)' in item.get('aggregated_output', ''):
                continue  # A shell pipeline can hide a failing inner command.
            events.append(dict(event_index=index, item_id=item.get('id'),
                               output=item.get('aggregated_output', '')))
    return events


def clock(value, binding):
    """Clock-only tables use the question's explicit timezone, not GT dates."""
    if re.fullmatch(r'\d\d:\d\d:\d\d', value):
        start = dt.datetime.fromisoformat(binding['start'].replace('Z', '+00:00'))
        end = dt.datetime.fromisoformat(binding['end'].replace('Z', '+00:00')).astimezone(start.tzinfo)
        if start.date() != end.date():
            raise ValueError('clock_only_cross_day_ambiguous')
        value = start.date().isoformat() + 'T' + value + start.strftime('%z')[:3] + ':' + start.strftime('%z')[3:]
    return iso(instant(value))


def period(left, right, binding):
    return dict(first_met_sample=clock(left, binding), last_met_sample=clock(right, binding))


def literal_objects(text):
    """Only a complete printed literal; never a substring of an executable command."""
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None


def printed_observations(event, contract):
    """Typed API series and explicitly signal-labelled timestamp/value tables."""
    decoder = json.JSONDecoder()
    text = event['output']
    objects = []
    index = 0
    while index < len(text):
        if text[index] not in '{[':
            index += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, index)
        except ValueError:
            index += 1
            continue
        objects.append(obj)
        index = end

    def walk(obj):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from walk(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk(value)

    rows = []
    for obj in objects:
        for value in walk(obj):
            if not isinstance(value.get('metric_name'), str) or not isinstance(value.get('points'), list):
                continue
            for point in value['points']:
                if isinstance(point, dict) and isinstance(point.get('timestamp'), str):
                    rows.append(dict(signal=value['metric_name'], entity_id=value.get('entity_id'),
                                     instance=(value.get('dimensions') or {}).get('pod'),
                                     time=point['timestamp'], value=point.get('value'),
                                     anchor=event['event_index']))
    # Only an explicit full signal name establishes column meaning.
    signal = None
    owner = None
    signals = {f['signal'] for f in contract['facts'].values() if f['modality']=='metric'}
    for line in text.splitlines():
        stripped = line.strip()
        header = re.match(r'^\s*[=#\-]+\s+([A-Za-z_][A-Za-z_0-9]*)\b', line)
        if header:
            signal = header.group(1) if header.group(1) in signals else None
            owner = None
        identity = re.fullmatch(r'(' + OWNER + r')\s+[a-fA-F0-9]{20,40}', stripped)
        if identity:
            owner = identity.group(1)
        point = re.fullmatch(r'(' + TIME + r')\s+(' + NUM + r')', stripped)
        peak = re.fullmatch(r'(' + OWNER + r')\s+(' + NUM + r')\s+\S+\s+(' + TIME + r')\s+(?:metric-[A-Za-z0-9_-]+)',stripped)
        if signal and point:
            rows.append(dict(signal=signal, instance=owner or contract['binding']['deployment'],
                             time=point.group(1), value=float(point.group(2)), anchor=event['event_index']))
        if signal and peak:
            rows.append(dict(signal=signal, instance=peak.group(1),
                             time=peak.group(3),value=float(peak.group(2)),anchor=event['event_index']))
    return rows


def parse_output(event, contract):
    binding = contract['binding']
    branches = []
    current = dict(period=None, declared_period=None, rows={}, lifecycle={}, anchor=event['event_index'],
                   branch=None, explicit_winners={}, duplicate_rows=False)
    branches.append(current)
    signal = None
    owner = None
    pending = {}
    grids = []
    header = None
    lifecycle_maps = {}
    for line_no, line in enumerate(event['output'].splitlines(), 1):
        text = line.strip()
        if text.startswith('########## mode='):
            current = dict(period=None, declared_period=None, rows={}, lifecycle={}, anchor=event['event_index'],
                           branch=text, explicit_winners={}, duplicate_rows=False)
            branches.append(current)
            signal = owner = None
            pending = {}
        tokens = text.lower().split()
        aliases = {'des': 'desired', 'desired': 'desired', 'avl': 'available', 'avail': 'available',
                   'available': 'available', 'rdy': 'ready', 'ready': 'ready'}
        if tokens and tokens[0] in ['ts', 'timestamp']:
            cols = [aliases.get(t, t) for t in tokens[1:]]
            if 'desired' in cols and 'available' in cols:
                header = cols
        timestamp = re.match(r'(' + TIME + r'|\d\d:\d\d:\d\d)\s+(.*)', text)
        if timestamp:
            tail = timestamp.group(2)
            named = dict(re.findall(r'(desired|des|available|avail|avl|ready|rdy)\s*[= ]\s*(' + NUM + r')', tail))
            normalized = {aliases[k]: float(v) for k, v in named.items()}
            if not normalized and header:
                values = tail.split()
                try:
                    normalized = {name: float(values[index]) for index, name in enumerate(header)
                                  if name in ['desired', 'available', 'ready']}
                except (IndexError, ValueError):
                    normalized = {}
            if 'desired' in normalized and 'available' in normalized:
                grids.append(dict(time=clock(timestamp.group(1), binding), **normalized,
                                  anchor=event['event_index'], line=line_no))
        match = re.search(r'((?:' + TIME + r')|(?:\d\d:\d\d:\d\d))\s*(?:->|\.\.)\s*(' + TIME + r'|\d\d:\d\d:\d\d)', text)
        if match and any(tag in text.lower() for tag in ['selected', 'last run', 'samples']):
            parsed = period(match.group(1), match.group(2), binding)
            if 'selected' in text.lower() or 'last run' in text.lower():
                current['declared_period'] = parsed
                if current['period'] is None:
                    current['period'] = parsed
            else:
                # A reported run list supplies calculation context in temporal order.
                # It is not a chosen final answer and never closes a decision alone.
                current['period'] = parsed
        for field, details in contract['outputs'].items():
            if re.fullmatch(r'[=#\-\s]*' + re.escape(details['signal']) + r'(?:\s+max)?', text):
                signal = field
                owner = None
                pending = {}
        pod = re.fullmatch(r'pod=(' + OWNER + ')', text)
        if pod:
            owner = pod.group(1)
            pending = {}
        life = re.search(r'(' + OWNER + r')\s+started=(\S+)\s+stopped=(\S+)', text)
        if life:
            current['lifecycle'][life.group(1)] = dict(start=life.group(2), stop=life.group(3), line=line_no)
        block_life = re.search(r'started=(\S+).*?\|\s*stop=(\S+)', text)
        if owner and block_life:
            current['lifecycle'][owner] = dict(start=block_life.group(1), stop=block_life.group(2), line=line_no)
        mapping = re.fullmatch(r'(started(?: in-window)?|stopped):\s*(\{.*\})',text)
        if mapping:
            values = literal_objects(mapping.group(2))
            if isinstance(values,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in values.items()):
                lifecycle_maps['start' if mapping.group(1).startswith('started') else 'stop'] = values
        count = re.search(r'in-period=(\d+)\s+valid=(\d+)', text)
        if owner and count:
            pending['count'] = int(count.group(2))
        block_peak = re.search(r'PEAK\(valid\)=(' + NUM + r')\s*@\s*(' + TIME + ')', text)
        inline = re.fullmatch(r'(' + OWNER + r')\s+(?:n=(\d+)\s+)?peak=(' + NUM + r')\s*(?:@|at)\s*(' + TIME + r')(?:\s+eligible=(\d+)/\d+\s+unit=\S+)?', text)
        if signal and block_peak and owner:
            row = dict(instance=owner, value=float(block_peak.group(1)), time=clock(block_peak.group(2), binding),
                       count=pending.get('count'), line=line_no)
        elif signal and inline:
            row = dict(instance=inline.group(1), value=float(inline.group(3)), time=clock(inline.group(4), binding),
                       count=int(inline.group(2) or inline.group(5)) if inline.group(2) or inline.group(5) else None,
                       line=line_no)
        else:
            row = None
        if row:
            table = current['rows'].setdefault(signal, {})
            if row['instance'] in table:
                current['duplicate_rows'] = True
            table[row['instance']] = row
        winner = re.fullmatch(r'WINNER\s*->\s*(\(.*\))', text)
        if signal and winner:
            value = literal_objects(winner.group(1))
            if isinstance(value, tuple) and len(value) == 3:
                current['explicit_winners'][signal] = dict(instance=value[0], value=value[1], time=value[2])
    if set(lifecycle_maps)=={'start','stop'}:
        for name in set(lifecycle_maps['start'])|set(lifecycle_maps['stop']):
            current['lifecycle'][name] = dict(start=lifecycle_maps['start'].get(name,'None'),
                                             stop=lifecycle_maps['stop'].get(name,'None'))
    return [b for b in branches if b['rows'] or b['lifecycle']], grids


def evaluate_trace(contract, path, report, final_answer=None, candidate_event=None):
    """Overlay only newly demonstrated work, while preserving authoritative errors."""
    events = successful_outputs(path)
    computations = []
    grids = {}
    lifecycle = {}
    refs = set()
    observed = []
    seen_lifecycle = set()
    seen_lifecycle_refs = set()
    fact_refs = contract['facts'].keys()
    for event in events:
        branches, grid = parse_output(event, contract)
        observed.extend(printed_observations(event, contract))
        for row in grid:
            grids[instant(row['time'])] = row
        for branch in branches:
            lifecycle.update(branch['lifecycle'])
        # Exact references establish provenance availability, NOT that a filter
        # was executed. Derived references are not guessed or prefix-resolved.
        refs.update(set(re.findall(r'(?:metric|event|topology)-[A-Za-z0-9_-]+', event['output'])) & fact_refs)
        for line in event['output'].splitlines():
            for owner,bounds in contract['lifecycle'].items():
                if owner not in line:
                    continue
                for kind in ['start','stop']:
                    for ref in bounds[kind]:
                        fact=contract['facts'][ref]
                        if ref in line:
                            stamps=re.findall(TIME,line)
                            if any(instant(stamp)==instant(fact['time']) for stamp in stamps):
                                seen_lifecycle.add((owner,kind))
                                seen_lifecycle_refs.add(ref)
        tables = [b for b in branches if b['rows']]
        if tables:
            computations.append((event, tables))
    changes = {}

    def claim(key, status, reason, anchors):
        changes[key] = dict(status=status, reason=reason, evidence_events=sorted(set(anchors)))

    expected_grid = {instant(row['time']): row for row in contract['phase_rows']}
    good_grid = bool(expected_grid) and all(
        stamp in grids and close(grids[stamp]['desired'], contract['facts'][row['ref_groups'][0][0]]['value'])
        and close(grids[stamp]['available'], contract['facts'][row['ref_groups'][1][0]]['value'])
        for stamp, row in expected_grid.items())
    # Ref-group order is defined by compile_contract: desired then available.
    if good_grid:
        claim('reference_integrity', 'verified', '工具输出完整副本采样表逐点匹配冻结观测。',
              [r['anchor'] for r in grids.values()])
    selected = computations[-1] if computations else None
    current_candidate = None
    ambiguity = False
    if selected:
        event, branches = selected
        if candidate_event is not None and candidate_event > event['event_index']:
            # A newer typed candidate controls the current decision. Old tables
            # remain visible evidence, not an opportunity for best-of selection.
            for b in branches:
                if final_answer is not None and not period_equal(final_answer.get('selected_period'), b['period']):
                    b['declared_period'] = final_answer.get('selected_period')
        anchors = [event['event_index']]
        printed_owners={name for b in branches for rows in b['rows'].values() for name in rows}
        if printed_owners and printed_owners <= set(contract['candidate_instances']):
            claim('scope_context','verified','计算表中的候选对象均属于本题冻结实体范围。',anchors)
        branch_candidates = []
        for b in branches:
            if b['period'] is None or b['duplicate_rows']:
                continue
            c = dict(selected_period=b['period'])
            for field in contract['outputs']:
                rows = list(b['rows'].get(field,{}).values())
                top = sorted(rows, key=lambda r: (-r['value'], r['instance']))
                if not top or len(top) > 1 and top[0]['value'] == top[1]['value']:
                    break
                c[field] = {key: top[0][key] for key in ['instance','value','time']}
            if all(field in c for field in contract['outputs']):
                branch_candidates.append(c)
        if branch_candidates:
            first = branch_candidates[0]
            ambiguity = len(branch_candidates) != len(branches) or any(
                not period_equal(first['selected_period'], c['selected_period'])
                or any(not same_row(first[field], c[field]) for field in contract['outputs'])
                for c in branch_candidates[1:])
            if not ambiguity:
                current_candidate = first
        phase_good = current_candidate is not None and period_equal(current_candidate['selected_period'], contract['selected_period'])
        phase_conflict = any(b['declared_period'] and b['period'] and not period_equal(b['declared_period'],b['period']) for b in branches)
        if current_candidate and good_grid and phase_good:
            claim('phase_condition','verified','完整副本表及实际计算阶段与连续配齐阶段一致。', anchors)
            claim('phase_boundaries', 'contradicted' if phase_conflict else 'verified',
                  '打印的SELECTED边界与同次运行列表、计算表上下文冲突。' if phase_conflict else '计算表阶段边界与独立重算一致。', anchors)
        if final_answer is None and (ambiguity or phase_conflict):
            claim('decision_closure','unobservable','同次输出存在多个阶段/答案分支或边界冲突；不挑最接近GT的一支。',anchors)
        complete_tables = []
        for field,item in contract['outputs'].items():
            signal_key = 'signal_usage' if field == 'cpu_usage' else 'signal_limit'
            rank_key = 'usage_ranking' if field == 'cpu_usage' else 'limit_ranking'
            expected = {row['instance']:row for row in item['rankings']}
            support_all, tables_all = True, True
            for b in branches:
                rows = b['rows'].get(field,{})
                support_all &= bool(rows)
                for owner,row in rows.items():
                    support_all &= any(fact['modality']=='metric' and fact['signal']==item['signal']
                                       and fact.get('instance')==owner and close(fact['value'],row['value'])
                                       and instant(fact['time'])==instant(row['time']) for fact in contract['facts'].values())
                tables_all &= (not b['duplicate_rows'] and set(rows)==set(expected) and all(
                    same_row(rows[owner], wanted) and rows[owner]['count']==len(wanted['sample_ref_groups'])
                    for owner,wanted in expected.items()))
            complete_tables.append(tables_all)
            if support_all:
                claim(signal_key,'verified','带信号名的计算表：对象、峰值、时间逐行匹配该口径原始记录。',anchors)
            if tables_all and phase_good:
                claim(rank_key,'verified','完整逐实例计算表的候选集合、有效采样数及峰值逐项通过独立复算。',anchors)
        if all(complete_tables) and phase_good:
            claim('candidate_coverage','verified','双指标计算表覆盖全部合法候选，每个对象的有效点数与峰值均匹配。',anchors)
            owners = set().union(*(set(b['rows'].get(f,{})) for b in branches for f in contract['outputs']))
            life_ok = True
            for owner in owners:
                row = lifecycle.get(owner)
                bounds = contract['lifecycle'][owner]
                groups = contract.get('lifecycle_ref_groups', {}).get(owner)
                if groups is not None and any(len({contract['facts'][r]['time'] for r in bounds[k]}) > 1 for k in ['start','stop']):
                    life_ok &= bool(groups) and all(set(group) & seen_lifecycle_refs for group in groups)
                    continue
                if not row:
                    life_ok = False
                    continue
                for name in ['start','stop']:
                    raw = row[name]
                    expected = bounds[name]
                    if not expected:
                        life_ok &= raw.upper() in ['NONE','NEVER','-']
                    else:
                        try:
                            life_ok &= instant(clock(raw,contract['binding']))==instant(contract['facts'][expected[0]]['time'])
                        except ValueError:
                            life_ok = False
            if life_ok:
                claim('lifecycle_bounds','verified','计算表候选的启动/停止时间逐项匹配业务容器事件，非GT补填。',anchors)
                claim('lifecycle_filter','verified','已输出有效采样数和过滤后峰值；按输出阶段和启停边界独立复算一致。',anchors)
            if all(key in changes for key in ['signal_usage','signal_limit']):
                claim('peak_time_scale','verified','两个计算表峰值及带时区时点均由真实观测支持。',anchors)
            if life_ok and good_grid and not phase_conflict:
                claim('claim_support','verified','阶段、候选集合、有效点数、启停边界和峰值输出形成一致的可复算产物。',anchors)
            if not phase_conflict and all(b['explicit_winners'] for b in branches):
                if all(same_row(b['explicit_winners'].get(f),current_candidate[f]) for b in branches for f in contract['outputs']):
                    claim('decision_closure','verified','打印的唯一WINNER与同次计算表一致。',anchors)
    candidate = final_answer or current_candidate
    if final_answer is None and current_candidate is not None:
        intermediate = verify(contract,current_candidate,submitted=False)
        for check in intermediate['checks']:
            if check['status']=='contradicted':
                claim(check['id'],'contradicted','最新唯一计算候选：'+check['reason'],[selected[0]['event_index']])
    if candidate and any(typed_row(candidate.get(field)) for field in contract['outputs']):
        if all(candidate[field]['instance'] in contract['candidate_instances'] for field in contract['outputs'] if typed_row(candidate.get(field))):
            claim('scope_context','verified','已形成候选对象属于任务范围。', [selected[0]['event_index']] if selected else [])
    if candidate:
        for field,item in contract['outputs'].items():
            value = candidate.get(field)
            if not typed_row(value):
                continue
            matches = []
            for row in observed:
                if row['signal'] != item['signal'] or not same_row(dict(instance=row.get('instance'), value=row['value'],time=row['time']),value):
                    continue
                if any(f['modality']=='metric' and f['signal']==row['signal'] and f.get('instance')==value['instance']
                       and close(f['value'],row['value']) and instant(f['time'])==instant(row['time'])
                       for f in contract['facts'].values()):
                    matches.append(row['anchor'])
            if matches:
                claim('signal_usage' if field=='cpu_usage' else 'signal_limit','verified',
                      '候选与成功工具输出中有明确信号口径的观测一致，并与冻结记录匹配。',matches)
    # Lifecycle lookup is a separately completed milestone, even if a later
    # ranking/stage decision was wrong or never submitted.
    required_owners = {r['instance'] for item in contract['outputs'].values() for r in item['rankings']}
    needed_lifecycle={(owner,kind) for owner in required_owners for kind in ['start','stop'] if contract['lifecycle'][owner][kind]}
    interval_groups = [group for owner in required_owners
                       for group in contract.get('lifecycle_ref_groups', {}).get(owner, [])]
    intervals_seen = not interval_groups or all(set(group) & seen_lifecycle_refs for group in interval_groups)
    if needed_lifecycle and needed_lifecycle <= seen_lifecycle and intervals_seen:
        claim('lifecycle_bounds','verified','工具逐项打印候选启停事件的完整引用、实体及时间，与冻结生命周期记录一致。',
              [e['event_index'] for e in events if any(ref in e['output'] for owner,kind in needed_lifecycle for ref in contract['lifecycle'][owner][kind])])
    if required_owners and all(owner in lifecycle for owner in required_owners):
        valid_life = True
        for owner in required_owners:
            if any(len({contract['facts'][r]['time'] for r in contract['lifecycle'][owner][k]}) > 1 for k in ['start','stop']):
                valid_life &= bool(interval_groups) and intervals_seen
                continue
            for name in ['start','stop']:
                source = lifecycle[owner][name]
                expected = contract['lifecycle'][owner][name]
                try:
                    valid_life &= (source.upper() in ['NONE','NEVER','-'] if not expected else
                                   instant(clock(source,contract['binding']))==instant(contract['facts'][expected[0]]['time']))
                except (ValueError, AttributeError):
                    valid_life = False
        if valid_life:
            claim('lifecycle_bounds','verified','工具已打印全部有效候选的业务容器启停边界，逐项匹配原始事件。',
                  [e['event_index'] for e in events if any(owner in e['output'] and 'started=' in e['output'] for owner in required_owners)])
    if refs:
        claim('reference_integrity','verified','成功工具输出中的完整原始引用可以解析到本任务冻结记录；不凭派生引用猜测。',
              [e['event_index'] for e in events if any(ref in e['output'] for ref in refs)])
    # Existing final-answer contradictions are never erased by an earlier table.
    for check in report['checks']:
        change = changes.get(check['id'])
        if change and (check['status']=='unobservable' or change['status']=='contradicted'
                       or check['id']=='decision_closure' and final_answer is None and (ambiguity or selected and any(
                           b['declared_period'] and b['period'] and not period_equal(b['declared_period'], b['period']) for b in selected[1]))):
            check.update(change)
            check['awarded'] = check['maximum'] if check['status']=='verified' else 0
            check['pending'] = check['maximum'] if check['status']=='unobservable' else 0
    report['process_score'] = sum(c['awarded'] for c in report['checks'])
    report['pending_points'] = sum(c['pending'] for c in report['checks'])
    report['process_score_interval'] = [report['process_score'],report['process_score']+report['pending_points']]
    report['process_status'] = 'complete' if not report['pending_points'] else 'partial' if report['process_score'] else 'unavailable'
    report['process_comparable'] = report['valid_for_comparison'] and not report['pending_points']
    report['scoring_version'] = SCORING_VERSION
    report['trace_adapter'] = dict(version='printed_artifacts_v1', successful_tool_events=len(events),
                                  computation_events=len(computations), last_computation_event=selected[0]['event_index'] if selected else None,
                                  candidate_ambiguous=ambiguity, complete_phase_grid=good_grid,
                                  unsupported_text_not_semantically_judged=True)
    report['aggregate_score'] = round(0.8*report['process_score']+0.2*report['result']['score'],4) if report['valid_for_comparison'] else None
    report['aggregate_comparable'] = report['valid_for_comparison'] and report['pending_points']==0
    report['aggregate_score_kind'] = ('invalid_execution' if not report['valid_for_comparison'] else
                                      'verified_lower_bound' if report['pending_points'] else 'complete')
    report['aggregate_policy'] = dict(work_weight=0.8, submitted_result_weight=0.2,
                                      missing_evidence='no_credit_not_demonstrated_error', version=SCORING_VERSION)
    return report
