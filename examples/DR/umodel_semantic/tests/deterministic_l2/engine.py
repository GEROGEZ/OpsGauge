"""Compile private observation contracts and verify public answer certificates.

This verifies observable claims, not an agent's private algorithm. A citation is
not proof of a network read. Missing certificates remain unobservable. No trace
keywords, manual verdicts, trial IDs, model calls, eval, or exec are used.
"""
import collections
import datetime as dt
import math
import re

from . import VERSION

CHECKS = [
    ('scope_context', 5), ('candidate_coverage', 10),
    ('phase_condition', 10), ('phase_boundaries', 10),
    ('signal_usage', 5), ('signal_limit', 5),
    ('lifecycle_bounds', 5), ('lifecycle_filter', 10),
    ('usage_ranking', 10), ('limit_ranking', 10),
    ('peak_time_scale', 5), ('reference_integrity', 5),
    ('claim_support', 5), ('decision_closure', 5),
]


def instant(value):
    value = value.replace('Z', '+00:00')
    if re.search(r'[+-]\d{2}$', value):
        value += ':00'
    stamp = dt.datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError('timezone_required')
    return stamp.timestamp()


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def close(a, b):
    return number(a) and number(b) and math.isclose(a, b, rel_tol=1e-4, abs_tol=1e-8)


def same_time(a, b):
    try:
        return instant(a) == instant(b)
    except (AttributeError, ValueError, TypeError):
        return False


def same_row(a, b):
    return (isinstance(a, dict) and a.get('instance') == b['instance']
            and close(a.get('value'), b['value']) and same_time(a.get('time'), b['time']))


def typed_row(row):
    if not isinstance(row, dict) or not isinstance(row.get('instance'), str) or not number(row.get('value')):
        return False
    try:
        instant(row['time'])
        return True
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def typed_period(period):
    if not isinstance(period, dict):
        return False
    try:
        return instant(period['first_met_sample']) <= instant(period['last_met_sample'])
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def period_equal(a, b):
    return isinstance(a, dict) and all(same_time(a.get(k), b[k]) for k in
                                      ('first_met_sample', 'last_met_sample'))


def compile_contract(source, rule, frozen_truth):
    """Compile the family contract from an evaluator-only raw scene slice.

    Unsupported conflicting labels, repeated business-container lifecycles,
    unknown identity, ties and empty phases fail rather than guess a truth.
    """
    binding = source['binding']
    if set(rule['outputs']) != {'cpu_usage', 'cpu_limit_pressure'}:
        raise ValueError('unsupported_output_schema_requires_family_adapter')
    if not rule['runtime']['require_observed_start'] or not rule['runtime']['stop_exclusive']:
        raise ValueError('unsupported_runtime_semantics')
    begin, end = instant(binding['start']), instant(binding['end'])
    cadence = rule['phase']['cadence_seconds']
    if not number(cadence) or cadence <= 0 or begin > end:
        raise ValueError('invalid_grid')
    entities = {e['entity_id']: e for e in source['entities']}
    deployments = [e for e in entities.values() if e['entity_type'] == 'k8s.deployment'
                   and e['entity_name'] == binding['deployment']]
    if len(deployments) != 1:
        raise ValueError('ambiguous_deployment')
    deployment_id = deployments[0]['entity_id']
    membership = set()
    for r in source['records']:
        if r.get('bundle_id') is not None and r.get('bundle_id') != binding.get('bundle_id'):
            raise ValueError('cross_scene_record')
        a, dims = r.get('attributes') or {}, r.get('dimensions') or {}
        if (r['modality'] == 'topology' and r['signal'] == 'contains'
                and a.get('source_entity_id') == deployment_id):
            entity = entities.get(a.get('target_entity_id'))
            if entity and entity['entity_type'] == 'k8s.pod':
                membership.add(entity['entity_name'])
        if (r['modality'] == 'metric' and dims.get('namespace') == binding['namespace']
                and dims.get('created_by_kind') == 'ReplicaSet'
                and dims.get('created_by_name', '').rsplit('-', 1)[0] == binding['deployment']):
            membership.add(dims.get('pod'))
    if not membership or None in membership:
        raise ValueError('missing_membership')
    phase_signals = [rule['phase']['desired_signal'], rule['phase']['available_signal']]
    all_signals = set(phase_signals + list(rule['outputs'].values()))
    facts, grouped = {}, collections.defaultdict(dict)
    lifecycle = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in source['records']:
        if r.get('synthetic') or r.get('source_kind', 'raw_telemetry') != 'raw_telemetry':
            raise ValueError('non_raw_source')
        a, dims = r.get('attributes') or {}, r.get('dimensions') or {}
        t = instant(r['observed_at'])
        if not begin <= t <= end:
            continue
        fact = dict(record_ref=r['record_ref'], modality=r['modality'], signal=r['signal'],
                    time=iso(t), value=r['value_json'], unit=r.get('unit', ''),
                    entity_ids=r.get('entity_ids') or [], dimensions=dims)
        fallback_owner = dims.get('pod') or a.get('object_name') or a.get('entity_name')
        if not fallback_owner and deployment_id in fact['entity_ids']:
            fallback_owner = binding['deployment']
        if fallback_owner:
            fact['instance'] = fallback_owner
        if fact['record_ref'] in facts and facts[fact['record_ref']] != fact:
            raise ValueError('conflicting_reference')
        # Preserve reference resolvability for auxiliary public signals too.
        # Not being a scored signal never makes an otherwise real citation fake.
        facts[fact['record_ref']] = fact
        if r['modality'] == 'metric' and r['signal'] in all_signals:
            if not number(r['value_json']):
                raise ValueError('nonfinite_observation')
            is_phase = r['signal'] in phase_signals
            if is_phase:
                if deployment_id not in fact['entity_ids'] or dims.get('namespace') != binding['namespace']:
                    continue
                owner = binding['deployment']
            else:
                owner = dims.get('pod') or a.get('entity_name')
                if owner not in membership or dims.get('namespace') != binding['namespace']:
                    continue
            fact['instance'] = owner
            key = (owner, r['signal'])
            old = grouped[key].get(t)
            if old and (old['value'] != fact['value'] or old['unit'] != fact['unit']):
                raise ValueError('conflicting_label_groups')
            if old:
                old['refs'].append(r['record_ref'])
            else:
                grouped[key][t] = dict(value=fact['value'], unit=fact['unit'], refs=[r['record_ref']])
        elif r['modality'] == 'event':
            owner = a.get('object_name')
            if owner not in membership or a.get('namespace') != binding['namespace']:
                continue
            fact['instance'] = owner
            fact['event_type'] = a.get('event_type') or r['signal']
            message = a.get('message', '')
            for label, template in [('start', rule['runtime']['start_message']),
                                    ('stop', rule['runtime']['stop_message'])]:
                if message == template.format(deployment=binding['deployment']):
                    lifecycle[owner][label].append(fact['record_ref'])
        else:
            continue
        facts[fact['record_ref']] = fact
    life = {}
    for owner in membership:
        bounds = {}
        for label in ['start', 'stop']:
            refs = lifecycle[owner].get(label, [])
            times = {facts[r]['time'] for r in refs}
            if len(times) > 1:
                raise ValueError('repeated_lifecycle_requires_interval_adapter')
            bounds[label] = refs
        if bounds['start'] and bounds['stop'] and instant(facts[bounds['stop'][0]]['time']) < instant(facts[bounds['start'][0]]['time']):
            raise ValueError('inverted_lifecycle')
        life[owner] = bounds
    desired, available = [grouped[(binding['deployment'], s)] for s in phase_signals]
    grid = [begin + i * cadence for i in range(int((end - begin) // cadence) + 1)]
    if set(desired) != set(grid) or set(available) != set(grid):
        raise ValueError('incomplete_phase_grid')
    segments, current, phase_rows = [], [], []
    for t in grid:
        good = desired[t]['value'] > 0 and available[t]['value'] >= desired[t]['value']
        phase_rows.append(dict(time=iso(t), qualifies=good,
                               ref_groups=[desired[t]['refs'], available[t]['refs']]))
        if good:
            current.append(t)
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    if not segments:
        raise ValueError('no_qualifying_phase')
    if rule['phase']['selection'] != 'last_contiguous_met':
        raise ValueError('unsupported_phase_selection')
    selected = segments[-1]
    period = dict(first_met_sample=iso(selected[0]), last_met_sample=iso(selected[-1]))
    outputs = {}
    for field, signal in rule['outputs'].items():
        table = []
        all_groups = []
        for owner in sorted(membership):
            bounds = life[owner]
            start = instant(facts[bounds['start'][0]]['time']) if bounds['start'] else None
            stop = instant(facts[bounds['stop'][0]]['time']) if bounds['stop'] else None
            points = [(t, point) for t, point in grouped[(owner, signal)].items()
                      if start is not None and selected[0] <= t <= selected[-1]
                      and start <= t and (stop is None or t < stop)]
            if not points:
                continue
            units = {p['unit'] for _, p in points}
            if len(units) != 1:
                raise ValueError('conflicting_units')
            # One reference per equivalent raw-label observation suffices.
            required = [p['refs'] for _, p in sorted(points)]
            all_groups.extend(required)
            peak = max(p['value'] for _, p in points)
            peak_points = [(t, p) for t, p in points if p['value'] == peak]
            table.append(dict(instance=owner, value=peak, time=iso(min(t for t, _ in peak_points)),
                              peak_times=[iso(t) for t, _ in peak_points],
                              unit=next(iter(units)), sample_ref_groups=required))
        table.sort(key=lambda row: (-row['value'], row['instance']))
        if not table or (len(table) > 1 and table[0]['value'] == table[1]['value']):
            raise ValueError('empty_or_tied_winner')
        outputs[field] = dict(signal=signal, winner=table[0], rankings=table, sample_ref_groups=all_groups)
    if not period_equal(period, frozen_truth['selected_run']):
        raise ValueError('recomputed_phase_disagrees_with_frozen_gt')
    for field, item in outputs.items():
        if not same_row(item['winner'], frozen_truth['answer'][item['signal']]):
            raise ValueError('recomputed_peak_disagrees_with_frozen_gt')
    return dict(schema_version=VERSION, family_id=rule['family_id'], visibility='evaluator_only',
                binding=binding, phase_predicate='available >= desired > 0',
                candidate_instances=sorted(membership),
                phase_selection='last_contiguous_met', phase_rows=phase_rows,
                selected_period=period, outputs=outputs, lifecycle=life, facts=facts,
                checks=[dict(id=key, points=points) for key, points in CHECKS])


def result_score(candidate, contract):
    """Original peak-pair answer weighting, kept separate from process score."""
    if not isinstance(candidate, dict):
        return dict(score=0, strict_pass=False, components={}, submitted=False)
    period = period_equal(candidate.get('selected_period'), contract['selected_period'])
    components = dict(period_correct=period)
    total = 20 * period
    for field, item in contract['outputs'].items():
        row = candidate.get(field) or {}
        row = row if isinstance(row, dict) else {}
        expected = item['winner']
        entity = isinstance(row, dict) and row.get('instance') == expected['instance']
        value = entity and close(row.get('value'), expected['value'])
        time = entity and same_time(row.get('time'), expected['time'])
        components[field] = dict(entity_correct=entity, value_correct=value, time_correct=time)
        total += 20 * entity + 10 * value + 10 * time
    return dict(score=total, strict_pass=period and all(all(components[f].values()) for f in contract['outputs']),
                components=components, submitted=True)


def observation_aliases(contract, observations):
    """Resolve series receipts by identity, labels, time, exact value and unit.

    A derived identifier alone proves nothing: every attached observation must
    match original facts. This never treats an agent-supplied verdict as truth.
    """
    if not isinstance(observations, list):
        raise ValueError('invalid_observation_receipts')
    aliases = {}
    for obs in observations:
        if not isinstance(obs, dict) or not isinstance(obs.get('record_ref'), str):
            raise ValueError('invalid_observation_receipt')
        signal, entity = obs.get('metric_name'), obs.get('entity_id')
        dimensions, unit, points = obs.get('dimensions'), obs.get('unit'), obs.get('points')
        if not isinstance(dimensions, dict) or not isinstance(points, list) or not points:
            raise ValueError('invalid_series_receipt')
        matched = set()
        for point in points:
            if not isinstance(point, dict) or not number(point.get('value')):
                raise ValueError('invalid_series_point')
            stamp = instant(point['timestamp'])
            candidates = []
            for ref, fact in contract['facts'].items():
                if (fact['modality'] != 'metric' or fact['signal'] != signal or entity not in fact['entity_ids']
                        or fact['unit'] != unit or instant(fact['time']) != stamp
                        or fact['value'] != point['value']):
                    continue
                dims = {k:v for k,v in fact['dimensions'].items() if k != '__name__'}
                if all(dimensions.get(k) == value for k,value in dims.items()):
                    candidates.append(ref)
            if not candidates:
                raise ValueError('observation_receipt_disagrees_with_snapshot')
            matched.update(candidates)
        name = obs['record_ref']
        if name in aliases and aliases[name] != matched:
            raise ValueError('conflicting_receipt_alias')
        if name in contract['facts'] and matched != {name}:
            raise ValueError('receipt_cannot_override_raw_reference')
        aliases[name] = matched
    return aliases


def claim_reference_groups(claims, candidate, output_fields):
    """Associate existing scored leaf pointers with their containing object.

    This changes reference routing only. Coverage, raw-value, lifecycle and
    coherence checks still decide credit. Unknown paths, prefix lookalikes and
    extra answer fields cannot establish a scored parent claim.
    """
    if not isinstance(claims, list):
        raise ValueError('invalid_claims')
    allowed = {'selected_period': {'first_met_sample', 'last_met_sample'}}
    allowed.update({field: {'instance', 'value', 'time'} for field in output_fields})
    groups = collections.defaultdict(set)
    for claim in claims:
        if (not isinstance(claim, dict) or not isinstance(claim.get('target'), str)
                or not isinstance(claim.get('refs'), list)
                or not all(isinstance(r, str) for r in claim['refs'])):
            raise ValueError('invalid_claim')
        target = claim['target']
        groups[target].update(claim['refs'])
        if not target.startswith('/') or re.search(r'~(?![01])', target):
            continue
        parts = [p.replace('~1', '/').replace('~0', '~') for p in target[1:].split('/')]
        if len(parts) != 2 or parts[0] not in allowed or parts[1] not in allowed[parts[0]]:
            continue
        parent = candidate.get(parts[0]) if isinstance(candidate, dict) else None
        if isinstance(parent, dict) and parts[1] in parent:
            groups['/' + parts[0]].update(claim['refs'])
    return groups


def verify(contract, candidate=None, certificate=None, execution_status='completed', submitted=False):
    """Pure verification: no I/O, no credentials, no trace-semantic judgments.

    certificate contains candidate (possibly partial), claims of the form
    {target: '/selected_period' or '/<answer field>', refs: [...]}, and optional
    source_refs. Assertions are checked against original observations. This is
    proof of an artifact's support, not proof of how an agent thought or read.
    """
    facts = contract['facts']
    if certificate is not None and (not isinstance(certificate, dict)
                                   or certificate.get('schema_version') != 'l2_work_evidence_v1'):
        raise ValueError('unsupported_certificate_schema')
    certificate = certificate or {}
    cert_candidate = certificate.get('candidate')
    # A final answer is authoritative. Do not silently choose an earlier better
    # certificate, or merge conflicting partial answers.
    coherent = True
    if candidate is not None and cert_candidate is not None:
        if not isinstance(cert_candidate, dict):
            raise ValueError('invalid_certificate_candidate')
        if 'selected_period' in cert_candidate:
            coherent = period_equal(candidate.get('selected_period'), cert_candidate['selected_period'])
        for field in contract['outputs']:
            if field in cert_candidate:
                coherent = coherent and same_row(candidate.get(field), cert_candidate[field])
    current = candidate if candidate is not None else cert_candidate
    if current is not None and not isinstance(current, dict):
        current = None
    claims = certificate.get('claims', [])
    groups = claim_reference_groups(claims, current, contract['outputs'])
    if current:
        # Legacy artifacts have a flat citation list. It may establish direct
        # support, but cannot imply an exhaustive comparison or a full phase scan.
        flat = current.get('evidence_refs', [])
        if not isinstance(flat, list) or not all(isinstance(r, str) for r in flat):
            raise ValueError('invalid_answer_references')
    else:
        flat = []
    aliases = observation_aliases(contract, certificate.get('observations', []))

    def expanded(items):
        return set().union(*(aliases.get(r, {r}) for r in items)) if items else set()

    groups = collections.defaultdict(set, {target:expanded(items) for target,items in groups.items()})
    for field in contract['outputs']:
        root = '/' + field
        explicit = expanded({ref for claim in claims if claim['target'] == root for ref in claim['refs']})
        # Instance leaves can legitimately cite lifecycle/topology events. They
        # remain in leaf groups for integrity/lifecycle verification, but must
        # not become purported metric comparison rows by inheritance.
        groups[root] = explicit | {r for r in groups[root] - explicit
                                   if r not in facts or facts[r]['modality'] == 'metric'}
    refs = expanded(flat)
    for items in groups.values():
        refs.update(items)
    source_refs = certificate.get('source_refs', [])
    if not isinstance(source_refs, list) or not all(isinstance(r, str) for r in source_refs):
        raise ValueError('invalid_source_references')
    refs.update(expanded(source_refs))
    known = refs & facts.keys()
    unknown = refs - facts.keys()
    # Derived series references are allowed but require a query-receipt resolver;
    # never falsely call an unresolved derived ref a forged reference.
    unresolved = {r for r in unknown if r.startswith('metric-derived-')}
    invalid = unknown - unresolved
    checks = {}

    def set_check(key, status, reason, evidence=()):
        checks[key] = dict(status=status, reason=reason, refs=sorted(set(evidence)))

    def supported(row, signal, used):
        return isinstance(row, dict) and any(
            facts[r]['modality'] == 'metric' and facts[r]['signal'] == signal
            and facts[r]['dimensions'].get('namespace') == contract['binding']['namespace']
            and facts[r]['instance'] == row.get('instance')
            and close(facts[r]['value'], row.get('value'))
            and same_time(facts[r]['time'], row.get('time')) for r in used & facts.keys())

    def covered(required, available):
        return bool(required) and all(set(group) & available for group in required)

    # Default: absent proof is unobservable, not a demonstrated wrong action.
    for key, _ in CHECKS:
        set_check(key, 'unobservable', '缺少可机器核验的结构化执行产物；不从自由文本或私有思考推断。')
    if invalid:
        set_check('reference_integrity', 'contradicted', '引用不属于本任务原始快照。', invalid)
    elif unresolved:
        set_check('reference_integrity', 'unobservable', '派生引用缺少可核验的原始记录映射；不判作伪造。', unresolved)
    elif known:
        set_check('reference_integrity', 'verified', '引用均可解析到本任务的原始观测；不声称已经证明内部使用过程。', known)
    if current and known:
        owners = set(contract['candidate_instances'])
        selected_owners = {current[f].get('instance') for f in contract['outputs']
                           if isinstance(current.get(f), dict)}
        if selected_owners and selected_owners <= owners:
            set_check('scope_context', 'verified', '已形成的对象及引用均落在冻结任务范围；这不等于查询日志审计。', known)
        elif selected_owners:
            set_check('scope_context', 'contradicted', '候选对象不属于本任务实体范围。')
    period = current.get('selected_period') if current else None
    phase_match = period_equal(period, contract['selected_period'])
    phase_refs = groups['/selected_period'] & known
    phase_groups = [group for row in contract['phase_rows'] for group in row['ref_groups']]
    phase_proof = coherent and covered(phase_groups, phase_refs)
    if typed_period(period):
        if not phase_match:
            for key in ['phase_condition', 'phase_boundaries']:
                set_check(key, 'contradicted', '已形成阶段与任务要求的连续配齐区间重算结果不一致。', phase_refs)
        elif phase_proof:
            for key in ['phase_condition', 'phase_boundaries']:
                set_check(key, 'verified', '完整采样网格的引用支持配齐谓词、连续性及任务指定的阶段选择。', phase_refs)
    all_comparisons = True
    all_peaks = True
    required_life = []
    life_owners = set()
    for field, item in contract['outputs'].items():
        row = current.get(field) if current else None
        used = groups['/' + field] & known
        field_support = used if groups['/' + field] else known
        direct = supported(row, item['signal'], field_support)
        signal_key = 'signal_usage' if field == 'cpu_usage' else 'signal_limit'
        if direct:
            set_check(signal_key, 'verified', '候选值、对象、时点由该口径原始观测支持。', used | known)
        elif groups['/' + field] and any(facts[r]['modality'] == 'metric' for r in used) and not any(
                facts[r]['modality'] == 'metric' and facts[r]['signal'] == item['signal'] for r in used):
            set_check(signal_key, 'contradicted', '该字段声明的指标证据没有使用要求的信号；数值相同也不能替代口径。', used)
        if not row or not supported(row, item['signal'], field_support):
            all_peaks = False
        match = same_row(row, item['winner'])
        rank_key = 'usage_ranking' if field == 'cpu_usage' else 'limit_ranking'
        if typed_row(row) and not match:
            set_check(rank_key, 'contradicted', '当前对象、峰值或时间与独立重算结果不同。', used)
        comparison = coherent and covered(item['sample_ref_groups'], used)
        all_comparisons = all_comparisons and comparison
        if match and phase_match and comparison:
            set_check(rank_key, 'verified', '证据覆盖全部合法比较采样；独立重算冠军、值、时点一致。', used)
        if isinstance(row, dict) and row.get('instance') in contract['lifecycle']:
            life_owners.add(row['instance'])
    comparison_owners = {facts[r]['instance'] for field in contract['outputs']
                         for r in groups['/' + field] & known}
    for owner in life_owners | comparison_owners:
        bounds = contract['lifecycle'][owner]
        if owner in contract.get('lifecycle_ref_groups', {}):
            required_life.extend(contract['lifecycle_ref_groups'][owner])
            continue
        required_life.extend([bounds['start']])
        if bounds['stop']:
            required_life.append(bounds['stop'])
    life_proof = coherent and covered(required_life, known)
    if life_proof:
        set_check('lifecycle_bounds', 'verified', '当前候选启停边界有本任务业务容器事件引用。', known)
    # Filtering and coverage require actual per-field evidence sets, not a correct
    # final answer or a list of arbitrary event references.
    if coherent and all_comparisons:
        set_check('candidate_coverage', 'verified', '各字段证据集合覆盖所有有效候选采样；重复标签可用等价引用。', known)
        comparison_refs = set().union(*(groups['/' + f] & known for f in contract['outputs']))
        invalid_comparison = set()
        for field, item in contract['outputs'].items():
            valid_refs = {r for group in item['sample_ref_groups'] for r in group}
            invalid_comparison.update((groups['/' + field] & known) - valid_refs)
        if typed_period(period):
            left, right = instant(period['first_met_sample']), instant(period['last_met_sample'])
            invalid_comparison.update(r for r in comparison_refs if not left <= instant(facts[r]['time']) <= right)
        if life_proof and typed_period(period) and not invalid_comparison:
            set_check('lifecycle_filter', 'verified', '比较集合只包含启动后、停止前且在正确阶段内的样本。', comparison_refs)
        elif invalid_comparison:
            set_check('lifecycle_filter', 'contradicted', '声称参与比较的集合包含错误口径、阶段外或无效生命周期采样。', invalid_comparison)
    if current and all(typed_row(current.get(f)) for f in contract['outputs']) and typed_period(period):
        if phase_match and all(same_row(current[f], item['winner']) for f, item in contract['outputs'].items()):
            if all_peaks:
                set_check('peak_time_scale', 'verified', '两项峰值及带时区时点均正确，且有对应原始观测支撑。', known)
            if phase_proof and all_comparisons and life_proof:
                set_check('claim_support', 'verified', '阶段、完整比较集合、启停事件及当前候选形成一致可验证证据。', known)
        else:
            set_check('peak_time_scale', 'contradicted', '当前阶段或至少一项峰值/时点与契约不一致。', known)
            set_check('claim_support', 'contradicted', '当前候选不能由同一正确阶段的合法比较集合支持。', known)
        if period and coherent:
            set_check('decision_closure', 'verified', '存在唯一一致的结构化当前候选；此项不重复要求结果正确。')
    if not coherent:
        set_check('decision_closure', 'contradicted', '最终答案与中间证据证书候选冲突，不挑有利分支。')
        for key in ['phase_condition', 'phase_boundaries', 'candidate_coverage', 'lifecycle_filter',
                    'usage_ranking', 'limit_ranking', 'claim_support']:
            if checks[key]['status'] == 'verified':
                set_check(key, 'unobservable', '证书与最终答案冲突，不能拼接证据授分。')
    rows = []
    for key, points in CHECKS:
        item = checks[key]
        rows.append(dict(id=key, maximum=points, awarded=points if item['status'] == 'verified' else 0,
                         pending=points if item['status'] == 'unobservable' else 0, **item))
    valid_execution = execution_status in ['completed', 'AgentTimeoutError']
    awarded = sum(c['awarded'] for c in rows)
    pending = sum(c['pending'] for c in rows)
    return dict(schema_version=VERSION, evaluation_mode='deterministic_certificate_verification',
                claim_pointer_policy='scored_leaf_to_parent_v1',
                model_calls=0, manual_verdicts=0, result=result_score(candidate if submitted else None, contract),
                process_score=awarded, pending_points=pending,
                process_status='unavailable' if pending == 100 else 'partial' if pending else 'complete',
                process_score_interval=[awarded, awarded+pending],
                process_score_semantics='verified_observable_artifact_support_not_private_reasoning',
                execution_status=execution_status, valid_for_comparison=valid_execution,
                process_comparable=valid_execution and pending == 0,
                aggregate_score=None, checks=rows,
                certificate_present=bool(certificate), certificate_coherent=coherent,
                unknown_derived_references=sorted(unresolved), invalid_references=sorted(invalid))
