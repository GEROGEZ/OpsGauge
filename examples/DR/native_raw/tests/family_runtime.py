"""Dispatch to existing frozen deterministic family scorers; no new weights."""
import collections
import copy

from deterministic_l2 import engine
import query_scorer

TEMPORAL='deployment_phase_runtime_dual_peak'


def compile_scoring_contract(source,case,rule,input_adapter):
    if case['family_id']==TEMPORAL:
        from export_common import temporal_projection_source
        source=temporal_projection_source(source,rule)
        gt=case['private_ground_truth']
        truth=dict(selected_run=gt['selected_period'],answer={rule['outputs'][k]:gt[k] for k in rule['outputs']})
        if input_adapter=='observed_grid_intervals_v1':
            from observed_contract import compile_observed_contract
            return compile_observed_contract(source,rule,truth)
        return engine.compile_contract(source,rule,truth)
    from compiler import compile_task
    from observation_graph import ObservationGraph
    from families import api_calls,epoch_ns
    computed=compile_task(ObservationGraph(source),case['family_id'],case['binding'])
    if computed['status']!='ready':raise ValueError('family_recompile_failed:'+str(computed.get('reason')))
    contract=copy.deepcopy(case['scoring_contract'])
    if case['family_id']=='observed_call_failure_rankings':
        refs=collections.defaultdict(list);b=case['binding'];start,end=epoch_ns(b['start']),epoch_ns(b['end'])
        for call in api_calls([r for r in source['records'] if r['modality']=='trace'],b['service'],b['api_prefix']):
            if start<=call['start_ns']<=end:refs[call['operation']].append(call['record_ref'])
        contract['operation_refs']={key:sorted(set(value)) for key,value in refs.items()}
    if not query_scorer.compare(contract,computed['private_ground_truth'])['strict_pass']:
        raise ValueError('recomputed_gt_disagrees_with_frozen_case')
    return contract


def result_score(answer,contract):
    return engine.result_score(answer,contract) if contract['family_id']==TEMPORAL else query_scorer.compare(contract,answer)


def score_trial(folder,contract):
    if contract['family_id']==TEMPORAL:
        from deterministic_l2.cli import score_trial as temporal_score
        return temporal_score(folder,contract)
    return query_scorer.score_trial(folder,contract)
