"""Translate an observed-grid temporal graph to the unchanged scoring schema.

No case IDs or frozen answers participate in phase/lifecycle/peak selection.
Missing internal samples fail; uncovered window edges are recorded, never filled.
"""
import collections

from deterministic_l2.engine import CHECKS, VERSION, instant, iso, number, period_equal, same_row
from family_v2 import build_graph, context, evaluate, runtime_at


def compile_observed_contract(source, rule, frozen_truth):
    binding = source['binding']
    task = binding['task']
    if task['query_kind'] != 'peak_pair' or task['cohort'] != 'started_in_question_window':
        raise ValueError('unsupported_task_contract')
    if task['phase_operator'] not in ['last_met', 'longest_met', 'last_unmet']:
        raise ValueError('unsupported_phase_operator')
    begin, end = instant(binding['start']), instant(binding['end'])
    source = dict(source, records=[r for r in source['records'] if begin <= instant(r['observed_at']) <= end])
    graph = build_graph(source, rule, task)
    maps, units, events = context(graph, rule)
    cadence = rule['phase']['cadence_seconds']
    desired = maps[binding['deployment'], rule['phase']['desired_signal']]
    available = maps[binding['deployment'], rule['phase']['available_signal']]
    grid = sorted(set(desired) | set(available))
    if (not number(cadence) or cadence <= 0 or not grid or set(desired) != set(available)
            or any(right-left != cadence for left, right in zip(grid, grid[1:]))
            or grid[0] < begin or grid[-1] > end):
        raise ValueError('incomplete_observed_phase_grid')
    result = evaluate(graph, rule, task)
    if result['status'] != 'certified':
        raise ValueError('uncertain_observed_contract:' + result['status'])
    if task['phase_operator'] == 'longest_met':
        lengths = sorted((r['samples'] for r in result['phase_runs']), reverse=True)
        if len(lengths) > 1 and lengths[0] == lengths[1]:
            raise ValueError('ambiguous_longest_phase')
    answer = result['answer']
    if not period_equal(answer['selected_period'], frozen_truth['selected_run']):
        raise ValueError('recomputed_phase_disagrees_with_frozen_gt')
    facts = {}
    for row in source['records']:
        if row.get('synthetic') or row.get('source_kind', 'raw_telemetry') != 'raw_telemetry':
            raise ValueError('non_raw_source')
        if row.get('bundle_id') is not None and row['bundle_id'] != binding['bundle_id']:
            raise ValueError('cross_scene_record')
        attrs, dims = row.get('attributes') or {}, row.get('dimensions') or {}
        if not begin <= instant(row['observed_at']) <= end:
            continue
        owner = dims.get('pod') or attrs.get('object_name') or attrs.get('entity_name')
        if row['signal'] in [rule['phase']['desired_signal'], rule['phase']['available_signal']]:
            owner = dims.get('deployment') or owner
        fact = dict(record_ref=row['record_ref'], modality=row['modality'], signal=row['signal'],
                    time=iso(instant(row['observed_at'])), value=row['value_json'], unit=row.get('unit', ''),
                    entity_ids=row.get('entity_ids') or [], dimensions=dims, instance=owner)
        if row['modality'] == 'event':
            fact['event_type'] = attrs.get('event_type') or row['signal']
        if row['record_ref'] in facts and facts[row['record_ref']] != fact:
            raise ValueError('conflicting_reference')
        facts[row['record_ref']] = fact
    lifecycle, life_groups = {}, {}
    for owner, evs in events.items():
        bounds = dict(start=[], stop=[])
        grouped = collections.defaultdict(list)
        for event in evs:
            if event['state'] in ['active', 'inactive']:
                bounds['start' if event['state'] == 'active' else 'stop'].append(event['id'])
            grouped[(instant(event['time']), event['state'])].append(event['id'])
            facts[event['id']]['instance'] = owner
        lifecycle[owner] = bounds
        life_groups[owner] = [grouped[key] for key in sorted(grouped)]
    left, right = (instant(answer['selected_period'][k]) for k in ['first_met_sample', 'last_met_sample'])
    outputs = {}
    for field, signal in rule['outputs'].items():
        table = []
        for winner in result['rankings'][field]['definite']:
            owner = winner['instance']
            points = [(t, point) for t, point in maps[owner, signal].items()
                      if left <= t <= right and runtime_at(events[owner], t) == 'active']
            groups = [[s['record_ref'] for s in point['sources']] for _, point in sorted(points)]
            table.append(dict(instance=owner, value=winner['value'], time=winner['time'], unit=units[owner, signal],
                              peak_times=[iso(t) for t, point in points if point['value'] == winner['value']],
                              sample_ref_groups=groups))
        table.sort(key=lambda row: (-row['value'], row['instance']))
        if not table or not same_row(table[0], frozen_truth['answer'][signal]):
            raise ValueError('recomputed_peak_disagrees_with_frozen_gt')
        outputs[field] = dict(signal=signal, winner=table[0], rankings=table,
                              sample_ref_groups=[g for row in table for g in row['sample_ref_groups']])
    phase_rows = [dict(time=iso(t), qualifies=(available[t]['value'] < desired[t]['value'] if task['phase_operator']=='last_unmet'
                                            else available[t]['value'] >= desired[t]['value'] > 0),
                       ref_groups=[[s['record_ref'] for s in points[t]['sources']] for points in [desired, available]])
                  for t in grid]
    members = sorted(n['properties']['entity_name'] for n in graph['nodes']
                     if n['type'] == 'OperationalEntity' and n['properties']['entity_type'] == 'k8s.pod')
    return dict(schema_version=VERSION, family_id=rule['family_id'], visibility='evaluator_only', binding=binding,
                input_adapter='observed_grid_intervals_v1', phase_predicate=('available < desired; desired > 0' if task['phase_operator']=='last_unmet' else 'available >= desired > 0'),
                candidate_instances=members, phase_selection=task['phase_operator'], phase_rows=phase_rows,
                selected_period=answer['selected_period'], outputs=outputs, lifecycle=lifecycle,
                lifecycle_ref_groups=life_groups, runtime_intervals=result['runtime_intervals'], facts=facts,
                sampling_coverage=dict(first=iso(grid[0]), last=iso(grid[-1]), count=len(grid),
                                       start_unobserved_seconds=grid[0]-begin, end_unobserved_seconds=end-grid[-1],
                                       inferred_edge_samples=False, boundary_semantics='observed_sampling_points_only'),
                checks=[dict(id=key, points=points) for key, points in CHECKS])
