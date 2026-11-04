"""Versioned sampling, event and task adapters; v1 is left untouched.

All branching is on declared task parameters and evidence semantics, never
case IDs, expected winners, GT boundaries, or model outcomes.
"""
import collections
import copy
import re

from family import build_graph as legacy_observation_graph, contiguous, iso, timestamp


def normalize_event(row, members, namespace, container, scope):
    a = row.get('attributes') or {}
    if a.get('namespace') != namespace:
        return None
    kind = a.get('event_type') or row['signal']
    message = a.get('message') or ''
    pod = a.get('object_name')
    if kind == 'SuccessfulDelete':
        match = re.fullmatch(r'Deleted pod:\s*(\S+)\s*', message)
        if match:
            pod = match[1]
        elif a.get('object_kind') != 'Pod':
            return None
        state, basis = 'inactive', 'pod_deletion'
    else:
        if a.get('object_kind') != 'Pod':
            return None
        explicit = a.get('container_name') or a.get('container')
        if kind == 'Started':
            match = re.fullmatch(r'Started container\s+(\S+)', message)
            explicit = explicit or (match[1] if match else None)
            if explicit and explicit != container:
                return None  # named init/sidecar is not the requested runtime
            generic = message.strip().lower() in ('container started', 'started container')
            if explicit == container:
                state, basis = 'active', 'named_container_started'
            elif generic:
                state = 'active' if scope == 'observed_instance' else 'unknown'
                basis = 'generic_pod_container_started_identity_unspecified'
            else:
                state, basis = 'unknown', 'unrecognized_start_payload'
        elif kind == 'Killing':
            match = re.fullmatch(r'Stopping container\s+(\S+)', message)
            restart = re.fullmatch(r'Container (\S+) failed .+will be restarted', message)
            explicit = explicit or (match[1] if match else restart[1] if restart else None)
            if explicit and explicit != container:
                return None
            if explicit == container and (match or restart):
                state, basis = 'inactive', 'restart_stop' if restart else 'named_container_stop'
            else:
                state, basis = 'unknown', 'unrecognized_stop_payload'
        else:
            return None  # Scheduled/Created/Ready do not substitute for Started
    if pod not in members:
        return None
    return dict(id=row['record_ref'], type='RuntimeTransition', owner=members[pod], name=pod,
                time=row['observed_at'], state=state, basis=basis,
                provenance_refs=[row['record_ref']], raw_event_type=kind)


def build_graph(source, rule, task):
    # Reuse v1's entity/series identity, label conflict checks and provenance.
    g = legacy_observation_graph(source, rule)
    old_events = {n['id'] for n in g['nodes'] if n['type'] == 'RuntimeEvent'}
    g['nodes'] = [n for n in g['nodes'] if n['id'] not in old_events]
    g['edges'] = [e for e in g['edges'] if e['target'] not in old_events]
    members = {n['properties']['entity_name']: n['id'] for n in g['nodes']
               if n['type'] == 'OperationalEntity' and n['properties']['entity_type'] == 'k8s.pod'}
    b = source['binding']
    normalized, unknowns = {}, []
    for r in source['records']:
        if r['modality'] != 'event':
            continue
        event = normalize_event(r, members, b['namespace'], task['container_name'], task['runtime_scope'])
        if event:
            if event['id'] in normalized and normalized[event['id']] != event:
                raise ValueError('conflicting normalized event identity')
            normalized[event['id']] = event
            if event['state'] == 'unknown':
                unknowns.append(event['id'])
    for e in normalized.values():
        g['nodes'].append(e)
        g['edges'].append(dict(source=e['owner'], relation='hasRuntimeEvent', target=e['id']))
    g['schema_version'] = 'l2_temporal_observation_subgraph_v2'
    g['adapter'] = dict(runtime_scope=task['runtime_scope'], unknown_event_refs=unknowns,
                        container_identity_not_inferred_from_generic_message=True)
    return g


def context(graph, rule):
    nodes = {n['id']: n for n in graph['nodes']}
    if len(nodes) != len(graph['nodes']):
        raise ValueError('duplicate graph node identity')
    dep = [n for n in nodes.values() if n['type'] == 'OperationalEntity'
           and n['properties']['entity_type'] == 'k8s.deployment']
    if len(dep) != 1:
        raise ValueError('unique deployment required')
    links = set()
    for e in graph['edges']:
        if e['source'] not in nodes or e['target'] not in nodes:
            raise ValueError('dangling graph edge')
        links.add((e['source'], e['relation'], e['target']))
    members = {t for s, r, t in links if s == dep[0]['id'] and r == 'contains'}
    maps, units = {}, {}
    for n in nodes.values():
        if (n['type'] == 'ObservationSeries' and n['owner'] in members | {dep[0]['id']}
                and (n['owner'], 'hasSeries', n['id']) in links):
            maps[n['name'], n['signal']] = {timestamp(p['time']): p for p in n['points']}
            units[n['name'], n['signal']] = n['unit']
    events = collections.defaultdict(list)
    for n in nodes.values():
        if (n['type'] == 'RuntimeTransition' and n['owner'] in members
                and (n['owner'], 'hasRuntimeEvent', n['id']) in links):
            events[n['name']].append(n)
    for evs in events.values():
        evs.sort(key=lambda e: (timestamp(e['time']), e['id']))
    return maps, units, events


def runtime_at(events, t):
    prior = [e for e in events if timestamp(e['time']) <= t]
    if not prior:
        return 'unknown'  # left-censored; absence of Started is not stopped
    latest = max(timestamp(e['time']) for e in prior)
    states = {e['state'] for e in prior if timestamp(e['time']) == latest}
    return next(iter(states)) if len(states) == 1 else 'unknown'


def runtime_intervals(events, lo, hi):
    boundaries = sorted({lo, hi} | {timestamp(e['time']) for e in events if lo <= timestamp(e['time']) <= hi})
    return dict(intervals=[dict(start=iso(a), end_exclusive=iso(b), state=runtime_at(events, a))
                           for a, b in zip(boundaries, boundaries[1:])],
                state_at_inclusive_window_end=runtime_at(events, hi))


def sample_complete(states, missing):
    active = [p for p, state in states.items() if state == 'active']
    unknown = [p for p, state in states.items() if state == 'unknown']
    if any(missing[p] for p in active):
        return False
    if active:
        return None if any(missing[p] for p in unknown) else True
    # No definite active instance: either no nonempty set can be complete,
    # or the nonempty-running condition itself is uncertain.
    return None if any(not missing[p] for p in unknown) else False


def pick_run(runs, operator):
    if not runs:
        return None
    if operator == 'longest_met':
        return min(runs, key=lambda r: (-len(r), r[0]))
    return runs[-1]


def rank_rows(board, task):
    ranked = sorted(board, key=lambda r: (-r['value'], r['instance']))
    if not ranked:
        return [], 'no_valid_observations'
    if task['query_kind'] == 'peak_pair':
        if len(ranked) > 1 and ranked[0]['value'] == ranked[1]['value']:
            return [], 'single_winner_tie'
        return [{k: ranked[0][k] for k in ('instance', 'value', 'time')}], None
    rows = []
    for r in ranked:
        rank = 1 + sum(x['value'] > r['value'] for x in ranked)
        if rank <= task['top_k']:
            rows.append({**{k: r[k] for k in ('instance', 'value', 'time', 'unit')}, 'rank': rank})
    return rows, None


def evaluate(graph, rule, task, contrast=False):
    maps, units, events = context(graph, rule)
    b = graph['binding']
    dep, lo, hi = b['deployment'], timestamp(b['start']), timestamp(b['end'])
    a = maps.get((dep, rule['phase']['available_signal']), {})
    d = maps.get((dep, rule['phase']['desired_signal']), {})
    grid = sorted(t for t in set(a) | set(d) if lo <= t <= hi)
    if not grid:
        return dict(status='unsupported', reason='no_replica_samples')
    cadence = rule['phase']['cadence_seconds']
    if any((t-grid[0]) % cadence for t in grid):
        return dict(status='unsupported', reason='irregular_replica_cadence')
    names = sorted({name for name, sig in maps if sig in rule['outputs'].values()})
    states = {t: {p: runtime_at(events[p], t) for p in names} for t in grid}
    if task['cohort'] == 'started_in_question_window':
        names = [p for p in names if any(e['state'] == 'active' and lo <= timestamp(e['time']) <= hi for e in events[p])]
    rows, predicates = [], {}
    for t in grid:
        present = t in a and t in d
        met = (a[t]['value'] >= d[t]['value'] > 0) if present else None
        if present and (a[t]['value'] < 0 or d[t]['value'] <= 0):
            met = None
        missing = {p: [sig for sig in rule['outputs'].values() if t not in maps.get((p, sig), {})] for p in names}
        all_missing = {p: [sig for sig in rule['outputs'].values()
                     if t not in maps.get((p, sig), {})] for p in states[t]}
        conservative_complete = sample_complete(states[t], all_missing)
        # The archived wrong program constructs its eligible set ONLY from
        # observed starts. Preserve that explicit closed-world algorithm as
        # the contrast, not as a claim that unknown objects are truly stopped.
        observed_active = [p for p, state in states[t].items() if state == 'active']
        complete = bool(observed_active) and all(not all_missing[p] for p in observed_active)
        selected_predicate = complete if contrast else met
        if task['phase_operator'] == 'last_unmet' and selected_predicate is not None:
            selected_predicate = not selected_predicate
        predicates[t] = selected_predicate
        rows.append(dict(time=iso(t), replica_met=met, cpu_sample_complete=complete,
                         conservative_cpu_completeness=conservative_complete,
                         predicate=selected_predicate, runtime_states=states[t], missing=missing,
                         replica_source_refs=[s['record_ref'] for p in (a.get(t), d.get(t)) if p for s in p['sources']]))
    certain = contiguous(grid, {t: predicates[t] is True for t in grid}, cadence)
    possible = contiguous(grid, {t: predicates[t] is not False for t in grid}, cadence)
    phase = pick_run(certain, task['phase_operator'])
    upper = pick_run(possible, task['phase_operator'])
    base = dict(phase_operator=task['phase_operator'], contrast=contrast,
                observed_grid=dict(first=iso(grid[0]), last=iso(grid[-1]), count=len(grid),
                                   public_window_unchanged=True, missing_counterpart=[iso(t) for t in grid if t not in a or t not in d]),
                phase_evidence=rows, phase_runs=[dict(first=iso(r[0]), last=iso(r[-1]), samples=len(r)) for r in certain])
    if phase is None or phase != upper:
        return dict(base, status='indeterminate' if upper else 'no_matching_phase',
                    reason='unknown_phase_can_change_selection' if upper else 'predicate_never_met', answer=None)
    answer = dict(selected_period=dict(first_met_sample=iso(phase[0]), last_met_sample=iso(phase[-1])))
    rankings, uncertain = {}, {}
    for field, sig in rule['outputs'].items():
        definite, potential = [], []
        for name in names:
            # Metric clocks need not equal the replica clock. Keep every real
            # sample inside the selected period; never resample to its grid.
            points = [(t, point) for t, point in maps.get((name, sig), {}).items() if phase[0] <= t <= phase[-1]]
            for target, allowed in [(definite, {'active'}), (potential, {'active', 'unknown'})]:
                eligible = [(t, p) for t, p in points if runtime_at(events[name], t) in allowed]
                if eligible:
                    t, point = min(eligible, key=lambda x: (-x[1]['value'], x[0]))
                    target.append(dict(instance=name, value=point['value'], time=iso(t), unit=units[name, sig],
                                       sources=point['sources'], runtime_evidence=events[name]))
        definite_rows, error = rank_rows(definite, task)
        possible_rows, possible_error = rank_rows(potential, task)
        safe = error is None and possible_error is None and definite_rows == possible_rows
        rankings[field] = dict(definite=sorted(definite, key=lambda r: (-r['value'], r['instance'])),
                               potential=sorted(potential, key=lambda r: (-r['value'], r['instance'])))
        if not safe:
            uncertain[field] = error or possible_error or 'unknown_runtime_can_change_output'
        if definite_rows:
            answer[field] = definite_rows[0] if task['query_kind'] == 'peak_pair' else definite_rows
    return dict(base, status='certified' if not uncertain else 'indeterminate',
                answer=answer, uncertainty=uncertain, rankings=rankings,
                runtime_intervals={p: runtime_intervals(events[p], lo, hi) for p in names})


def validate_task(task):
    if task['phase_operator'] not in ('last_met', 'longest_met', 'last_unmet'):
        raise ValueError('unsupported phase operator')
    if task['query_kind'] not in ('peak_pair', 'topk'):
        raise ValueError('unsupported output kind')
    if type(task['top_k']) is not int or task['top_k'] <= 0:
        raise ValueError('top_k must be a positive integer')
    if task['cohort'] not in ('started_in_question_window', 'all_observed_instances'):
        raise ValueError('unsupported cohort')
    if task['runtime_scope'] not in ('observed_instance', 'business_container'):
        raise ValueError('unsupported runtime scope')
    if not task['container_name']:
        raise ValueError('container binding required')


def compile_family(source, rule, task):
    validate_task(task)
    graph = build_graph(source, rule, task)
    correct = evaluate(graph, rule, task)
    contrast = evaluate(graph, rule, task, contrast=True)
    comparable = correct['status'] == contrast['status'] == 'certified'
    differences = None
    if comparable:
        differences = {key: correct['answer'][key] != contrast['answer'][key]
                       for key in ('selected_period', *rule['outputs'])}
    return dict(schema_version='l2_temporal_family_instance_v2', task=copy.deepcopy(task),
                graph=graph, correct=correct, contrast=contrast,
                contrast_mutation='only phase predicate; same task operator, cohort, runtime and ranking',
                answer_comparison=dict(status='comparable' if comparable else 'not_comparable', differences=differences),
                difficulty_label=None)
