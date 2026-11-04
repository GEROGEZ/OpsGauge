"""Typed observation subgraph + executable family, with one predicate contrast.

No instance names, incident IDs, expected winners, or expected times in logic.
The input is a frozen, evaluator-only observation slice, NOT agent-visible data.
"""
import collections
import datetime as dt
import math
import re


def timestamp(value):
    instant = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.tzinfo is None:
        raise ValueError('timezone required')
    return instant.timestamp()


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone(dt.timedelta(hours=8))).isoformat()


def build_graph(source, rule):
    binding = source['binding']
    deps = [e for e in source['entities'] if e['entity_type'] == 'k8s.deployment'
            and e['entity_name'] == binding['deployment']]
    if len(deps) != 1:
        raise ValueError('deployment identity not uniquely resolved')
    dep = deps[0]
    if not any(r['modality'] == 'metric' and dep['entity_id'] in (r['entity_ids'] or [])
               and (r['dimensions'] or {}).get('namespace') == binding['namespace']
               for r in source['records']):
        raise ValueError('deployment has no observations in requested namespace')
    entities = {e['entity_id']: e for e in source['entities']}
    pods = {e['entity_name']: e for e in entities.values() if e['entity_type'] == 'k8s.pod'}
    memberships = collections.defaultdict(set)
    for r in source['records']:
        attrs, dims = r['attributes'] or {}, r['dimensions'] or {}
        if r['modality'] == 'topology' and r['signal'] == 'contains' and attrs.get('source_entity_id') == dep['entity_id']:
            target = entities.get(attrs.get('target_entity_id'))
            if target and target['entity_type'] == 'k8s.pod':
                memberships[target['entity_name']].add(r['record_ref'])
        if (r['modality'] == 'metric' and dims.get('namespace') == binding['namespace']
                and dims.get('created_by_kind') == 'ReplicaSet'
                and re.fullmatch(re.escape(binding['deployment']) + '-[a-z0-9]+', dims.get('created_by_name') or '')):
            memberships[dims['pod']].add(r['record_ref'])
    nodes = [{'id': dep['entity_id'], 'type': 'OperationalEntity', 'properties': dep}]
    edges, series, events = [], {}, []
    for name in sorted(memberships):
        if name not in pods:
            raise ValueError('missing pod entity identity: ' + name)
        nodes.append(dict(id=pods[name]['entity_id'], type='OperationalEntity', properties=pods[name]))
        edges.append(dict(source=dep['entity_id'], relation='contains', target=pods[name]['entity_id'],
                          provenance_refs=sorted(memberships[name])))
    phase_signals = {rule['phase']['available_signal'], rule['phase']['desired_signal']}
    for r in source['records']:
        attrs, dims = r['attributes'] or {}, r['dimensions'] or {}
        if r['modality'] == 'metric':
            is_phase = r['signal'] in phase_signals
            if is_phase:
                if (dims.get('namespace') != binding['namespace'] or dims.get('deployment') != binding['deployment']
                        or dep['entity_id'] not in (r['entity_ids'] or [])):
                    continue
                owner, name = dep['entity_id'], binding['deployment']
            else:
                name = dims.get('pod')
                if name not in memberships or dims.get('namespace') != binding['namespace']:
                    continue
                owner = pods[name]['entity_id']
            if r['value_json'] is None:
                continue  # missing is never zero
            if type(r['value_json']) not in (float, int) or not math.isfinite(r['value_json']):
                raise ValueError('nonfinite/non-numeric observation')
            key = (owner, r['signal'])
            if key not in series:
                sid = 'series:' + owner + ':' + r['signal']
                series[key] = dict(id=sid, type='ObservationSeries', owner=owner, name=name,
                                   signal=r['signal'], unit=r['unit'], points={})
                edges.append(dict(source=owner, relation='hasSeries', target=sid))
            s = series[key]
            if s['unit'] != r['unit']:
                raise ValueError('conflicting units for same instance/signal')
            t = timestamp(r['observed_at'])
            old = s['points'].get(t)
            if old and old['value'] != r['value_json']:
                raise ValueError('conflicting label groups require group-specific adapter')
            point = s['points'].setdefault(t, dict(time=iso(t), value=r['value_json'], sources=[]))
            point['sources'].append(dict(record_ref=r['record_ref'], dimensions=dims))
        elif r['modality'] == 'event':
            name = attrs.get('object_name')
            if (name not in memberships or attrs.get('object_kind') != 'Pod'
                    or attrs.get('namespace') != binding['namespace']):
                continue
            for key in ('start', 'stop'):
                if attrs.get('message') == rule['runtime'][key + '_message'].format(**binding):
                    e = dict(id=r['record_ref'], type='RuntimeEvent', event=key,
                             owner=pods[name]['entity_id'], name=name, time=r['observed_at'],
                             provenance_refs=[r['record_ref']])
                    events.append(e)
                    edges.append(dict(source=e['owner'], relation='hasRuntimeEvent', target=e['id']))
    for s in series.values():
        s['points'] = [s['points'][t] for t in sorted(s['points'])]
    nodes += list(series.values()) + events
    return dict(schema_version='l2_temporal_observation_subgraph_v1', binding=binding,
                parent_episode_id=source['original_episode']['episode_id'],
                enrichment_source='raw replay telemetry; original RCA graph is not replaced',
                nodes=nodes, edges=edges)


def contiguous(grid, flags, cadence):
    runs, current = [], []
    for t in grid:
        if current and (not flags[t] or t - current[-1] != cadence):
            runs.append(current)
            current = []
        if flags[t]:
            current.append(t)
    if current:
        runs.append(current)
    return runs


def evaluate(graph, rule, phase_mode='deployment_available_gte_desired'):
    if phase_mode not in ('deployment_available_gte_desired', rule['contrast']['operator']):
        raise ValueError('unknown phase predicate')
    binding = graph['binding']
    nodes = {n['id']: n for n in graph['nodes']}
    if len(nodes) != len(graph['nodes']):
        raise ValueError('duplicate graph node identity')
    deployments = [n for n in nodes.values() if n['type'] == 'OperationalEntity'
                   and n['properties']['entity_type'] == 'k8s.deployment'
                   and n['properties']['entity_name'] == binding['deployment']]
    if len(deployments) != 1:
        raise ValueError('unique deployment graph node required')
    deployment_id = deployments[0]['id']
    links = set()
    for edge in graph['edges']:
        if edge['source'] not in nodes or edge['target'] not in nodes:
            raise ValueError('dangling evidence graph relation')
        links.add((edge['source'], edge['relation'], edge['target']))
    members = {target for source, relation, target in links
               if source == deployment_id and relation == 'contains'
               and nodes[target]['type'] == 'OperationalEntity'
               and nodes[target]['properties']['entity_type'] == 'k8s.pod'}
    owners = members | {deployment_id}
    series = {(n['name'], n['signal']): n for n in nodes.values()
              if n['type'] == 'ObservationSeries' and n['owner'] in owners
              and (n['owner'], 'hasSeries', n['id']) in links}
    maps = {k: {timestamp(p['time']): p for p in s['points']} for k, s in series.items()}
    life = collections.defaultdict(lambda: dict(start=[], stop=[]))
    for n in nodes.values():
        if (n['type'] == 'RuntimeEvent' and n['owner'] in members
                and (n['owner'], 'hasRuntimeEvent', n['id']) in links):
            life[n['name']][n['event']].append(n)
    for name, bounds in life.items():
        if any(len({timestamp(e['time']) for e in bounds[k]}) > 1 for k in bounds):
            raise ValueError('multiple runtime intervals require another adapter: ' + name)
    def alive(name, t):
        b = life.get(name)
        return bool(b and b['start'] and timestamp(b['start'][0]['time']) <= t
                    and (not b['stop'] or t < timestamp(b['stop'][0]['time'])))
    cadence = rule['phase']['cadence_seconds']
    lo, hi = timestamp(binding['start']), timestamp(binding['end'])
    if (hi-lo) % cadence:
        raise ValueError('binding must define an aligned observation grid')
    grid = [lo + i * cadence for i in range(int((hi-lo)/cadence)+1)]
    try:
        available = maps[binding['deployment'], rule['phase']['available_signal']]
        desired = maps[binding['deployment'], rule['phase']['desired_signal']]
    except KeyError as exc:
        raise ValueError('missing linked deployment series') from exc
    if set(grid) != set(available) or set(grid) != set(desired):
        raise ValueError('missing/extra phase samples; never interpolate or silently bridge')
    pod_names = sorted({s['name'] for s in series.values() if s['signal'] in rule['outputs'].values()})
    rows, flags = [], {}
    for t in grid:
        running = [p for p in pod_names if alive(p, t)]
        missing = {sig: [p for p in running if t not in maps.get((p, sig), {})]
                   for sig in rule['outputs'].values()}
        deployment_met = available[t]['value'] >= desired[t]['value'] > 0
        cpu_met = bool(running) and not any(missing.values())
        flags[t] = deployment_met if phase_mode == 'deployment_available_gte_desired' else cpu_met
        rows.append(dict(time=iso(t), available=available[t], desired=desired[t],
                         running_instances=running, missing_samples=missing,
                         deployment_met=deployment_met, cpu_sample_complete=cpu_met))
    runs = contiguous(grid, flags, cadence)
    if not runs:
        raise ValueError('no matching phase')
    selected = runs[-1]
    rankings, result = {}, dict(selected_period=dict(first_met_sample=iso(selected[0]), last_met_sample=iso(selected[-1])))
    for field, sig in rule['outputs'].items():
        board = []
        for name in pod_names:
            valid = [(t, p) for t, p in maps.get((name, sig), {}).items() if t in selected and alive(name, t)]
            if not valid:
                continue
            t, point = min(valid, key=lambda x: (-x[1]['value'], x[0]))
            board.append(dict(instance=name, value=point['value'], time=iso(t),
                              unit=series[name, sig]['unit'], sources=point['sources'],
                              runtime_evidence=life[name]))
        board.sort(key=lambda r: (-r['value'], r['instance']))
        if not board or len(board) > 1 and board[0]['value'] == board[1]['value']:
            raise ValueError('single-winner contract requires unique maximum')
        rankings[field] = board
        result[field] = {k: board[0][k] for k in ('instance', 'value', 'time')}
    return dict(phase_mode=phase_mode, answer=result, rankings=rankings,
                phase_runs=[dict(first=iso(r[0]), last=iso(r[-1]), samples=len(r)) for r in runs],
                phase_evidence=rows)


def compile_family(source, rule):
    if (rule['schema_version'] != 'l2_temporal_rank_family_v1'
            or rule['phase']['selection'] != 'last_contiguous_met'
            or not rule['runtime']['require_observed_start']
            or not rule['runtime']['stop_exclusive']
            or not rule['contrast']['require_nonempty_running_set']):
        raise ValueError('unsupported family semantics; never silently ignore configuration')
    graph = build_graph(source, rule)
    correct = evaluate(graph, rule)
    contrast = evaluate(graph, rule, rule['contrast']['operator'])
    correct_fields = rule['outputs']
    changes = {f: correct['answer'][f] != contrast['answer'][f] for f in correct_fields}
    task_program = [
        dict(id='resolve_scope', operation='resolve', depends_on=[]),
        dict(id='select_phase', operation='last_contiguous_met', depends_on=['resolve_scope'],
             predicate='deployment_available_gte_desired'),
        dict(id='runtime_join', operation='start_inclusive_stop_exclusive', depends_on=['resolve_scope', 'select_phase']),
        dict(id='dual_peak', operation='per_signal_argmax', depends_on=['runtime_join'], signals=rule['outputs']),
    ]
    witnesses = [r for r in correct['phase_evidence'] if r['deployment_met'] != r['cpu_sample_complete']]
    return dict(schema_version='l2_executable_family_instance_v1', family_id=rule['family_id'],
                graph=graph, task_program=task_program,
                contrast_mutation=dict(target='select_phase', from_predicate='deployment_available_gte_desired',
                                       to_predicate=rule['contrast']['operator'], other_operators_unchanged=True),
                correct=correct, contrast=contrast,
                divergence=dict(period_changed=correct['answer']['selected_period'] != contrast['answer']['selected_period'],
                                output_changes=changes, predicate_witnesses=witnesses),
                empirical_difficulty='not_inferred_from_structural_divergence')
