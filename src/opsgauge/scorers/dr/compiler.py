"""Five family adapters share one graph->projection->answer interface.

Ground truth/model results are not accepted as compiler inputs. Frozen legacy
calculations remain in their original modules; this file supplies graph data.
"""
import collections
import copy
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'l2-family-discovery-v2'))
sys.path.insert(0, str(HERE.parent/'l2-task-family-v1'))
from families import alert_transactions, api_calls, crossing_calls, epoch_ns, failure_rankings
from family_v2 import compile_family as compile_temporal
from family import contiguous, iso, timestamp
from observation_graph import ObservationGraph

REGISTRY = {
    'replica_fulfillment': dict(modalities=['metric', 'event', 'topology'], required=['deployment', 'namespace', 'cadence_seconds'],
        pattern=['Entity:Deployment', 'observed:available', 'observed:desired'],
        operators=['align', 'compare', 'count', 'contiguous'], outputs=['met_percentage', 'unmet_periods'],
        contrast='observed availability alone is not fulfillment of configured replica count'),
    'deployment_phase_runtime_dual_peak': dict(modalities=['metric', 'event', 'topology'],
        required=['deployment', 'namespace', 'task'], pattern=['Deployment->Pod', 'Pod->RuntimeEvent', 'Entity->Metric'],
        operators=['phase', 'lifecycle', 'filter', 'peak', 'rank'], outputs=['selected_period', 'cpu_usage', 'cpu_limit_pressure'],
        contrast='substitute CPU sampling completeness for replica fulfillment'),
    'alert_transaction_state': dict(modalities=['event', 'topology'], required=['service'],
        pattern=['Entity->Notification', 'Notification->Transaction', 'Transaction->Rule'],
        operators=['scope', 'group_identity', 'order_time', 'terminal_state', 'count'], outputs=['total_active_transactions', 'rules'],
        contrast='collapse transactions to rules or ignore recovery'),
    'observed_call_failure_rankings': dict(modalities=['trace', 'topology'], required=['service', 'api_prefix'],
        pattern=['Entity->Call', 'Call->ObservedOperation'], operators=['scope', 'deduplicate_call', 'count', 'ratio', 'rank'],
        outputs=['by_failure_count', 'by_failure_ratio'], contrast='reuse count order for failure-ratio ranking'),
    'event_anchored_call_intervals': dict(modalities=['event', 'trace', 'topology'], required=['service', 'namespace', 'api_prefix'],
        pattern=['Entity->Event', 'Entity->Call', 'Call->ObservedOperation'],
        operators=['event_anchor', 'interval_intersection', 'clip', 'sum', 'latest_completion'],
        outputs=['marker_time', 'marker_evidence_refs', 'results'], contrast='sum full call duration instead of post-anchor duration')
}


class NotApplicable(ValueError):
    pass


def temporal_rule():
    return json.loads((HERE.parent/'l2-task-family-v1/family-rule.json').read_text())


def project_task(graph, family, params):
    """Select a candidate scope, before choosing states, anchors or winners."""
    spec=REGISTRY[family]
    if family=='alert_transaction_state':
        scope={e['entity_id'] for e in graph.entities() if e['entity_type'] in ['apm.service','apm.operation']
               and (e['entity_name']==params['service'] or (e.get('attributes') or {}).get('service')==params['service'])}
        predicate=lambda r:r['modality']=='event' and r['attributes'].get('source_stream')=='alert' and bool(scope.intersection(r['entity_ids']))
    elif family in ['observed_call_failure_rankings','event_anchored_call_intervals']:
        # Keep all operations and all namespace events, including irrelevant
        # methods and init-container events. Do not preselect correct answers.
        predicate=lambda r:((r['modality']=='trace' and r['attributes'].get('service_name')==params['service'])
                            or (r['modality']=='event' and r['attributes'].get('source_stream')=='k8s_event'
                                and r['attributes'].get('namespace')==params['namespace']))
    else:
        rule=temporal_rule();signals={rule['phase']['available_signal'],rule['phase']['desired_signal']}
        if family=='deployment_phase_runtime_dual_peak':signals.update(rule['outputs'].values())
        predicate=lambda r:r['modality'] in ['event','topology'] or (r['modality']=='metric' and r['signal'] in signals)
    return graph.project(spec['modalities'],predicate)


def calculate(view, family, params):
    records, entities = view.records, view.graph.entities()
    start, end = epoch_ns(params['start']), epoch_ns(params['end'])
    if end < start:
        raise NotApplicable('invalid_time_range')
    events = [r for r in records if r['modality']=='event']
    if family == 'alert_transaction_state':
        scope = {e['entity_id'] for e in entities if e['entity_type'] in ['apm.service','apm.operation']
                 and (e['entity_name']==params['service'] or (e.get('attributes') or {}).get('service')==params['service'])}
        if not scope:
            raise NotApplicable('service_scope_missing')
        active, triggered = alert_transactions(events, scope, start, end)
        if not triggered:
            raise NotApplicable('no_observed_trigger_in_window')
        rules = collections.defaultdict(list)
        for row in active:
            rules[row['rule_id']].append(row)
        answer = dict(total_active_transactions=len(active), rules=[dict(rule_id=rid,subject=rows[0]['subject'],
                      active_count=len(rows),transactions=rows) for rid,rows in sorted(rules.items())])
        last_per_rule = {}
        for row in triggered:
            old = last_per_rule.get(row['rule_id'])
            if old is None or epoch_ns(row['last_notification_at']) > epoch_ns(old['last_notification_at']):
                last_per_rule[row['rule_id']] = row
        wrong = sum(r['last_status']!='RECOVERED' for r in last_per_rule.values())
        return answer, dict(rule_level_count=wrong,ignore_recovery_count=len(triggered),
                            answer_diverges=wrong!=len(active) or len(triggered)!=len(active))
    if family in ['observed_call_failure_rankings', 'event_anchored_call_intervals']:
        calls = api_calls([r for r in records if r['modality']=='trace'],params['service'],params['api_prefix'])
        if not calls:
            raise NotApplicable('no_identity_bound_api_calls')
        if family == 'observed_call_failure_rankings':
            answer = failure_rankings(calls,start,end)
            if not answer['by_failure_count']:
                raise NotApplicable('no_calls_in_window')
            count = [r['operation'] for r in answer['by_failure_count']]
            ratio = [r['operation'] for r in answer['by_failure_ratio']]
            board=answer['by_failure_count']
            invalid_ratio_order=any(a['error_ratio']<b['error_ratio'] for a,b in zip(board,board[1:]))
            return answer, dict(count_order=count,ratio_order=ratio,answer_diverges=invalid_ratio_order,
                                tie_equivalent_orders_not_counted_as_divergence=True)
        result = crossing_calls(calls,events,params['namespace'],params['service'],start,end)
        if not result['results']:
            raise NotApplicable('no_completed_calls_cross_anchor')
        answer = dict(marker_time=result['anchor'],marker_evidence_refs=result['anchor_refs'],results=[
            dict(operation=r['operation'],call_count=r['call_count'],after_marker_total_ms=r['after_marker_total_ns']/1e6,
                 last_completed_calls=r['last_completed_calls']) for r in result['results']])
        return answer, dict(full_duration_total_ms={r['operation']:r['full_duration_total_ns']/1e6 for r in result['results']},
                            answer_diverges=any(r['full_duration_total_ns']!=r['after_marker_total_ns'] for r in result['results']))
    rule = temporal_rule()
    if family == 'deployment_phase_runtime_dual_peak':
        result = compile_temporal(view.legacy_source(params),rule,params['task'])
        if result['correct']['status']!='certified':
            raise NotApplicable('temporal_output_'+result['correct']['status'])
        def objects(answer, field):
            value=answer.get(field)
            rows=value if isinstance(value,list) else [value] if isinstance(value,dict) else []
            return sorted(r['instance'] for r in rows)
        alternative=result['contrast'].get('answer')
        membership_change=(any(objects(result['correct']['answer'],f)!=objects(alternative,f) for f in rule['outputs'])
                           if alternative is not None else None)
        return result['correct']['answer'], dict(status=result['contrast']['status'],answer=alternative,
                 object_selection_changed=membership_change,changed_output_fields=result['answer_comparison']['differences'],
                 answer_diverges=bool(result['answer_comparison']['differences'] and any(result['answer_comparison']['differences'].values())))
    # Replica task has no runtime requirement; do not reuse the peak task's
    # restrictive container preconditions for a different business question.
    deps = [e for e in entities if e['entity_type']=='k8s.deployment' and e['entity_name']==params['deployment']]
    if len(deps)!=1:
        raise NotApplicable('deployment_identity_not_unique')
    signals = [rule['phase']['available_signal'],rule['phase']['desired_signal']]
    points = {s:{} for s in signals}
    for row in records:
        dims = row.get('dimensions') or {}
        if (row['modality']!='metric' or row['signal'] not in points or deps[0]['entity_id'] not in row['entity_ids']
            or dims.get('namespace')!=params['namespace'] or dims.get('deployment')!=params['deployment']):
            continue
        t = timestamp(row['observed_at'])
        if not start <= epoch_ns(row['observed_at']) <= end:
            continue
        value = row['value_json']
        if type(value) not in [int,float] or not math.isfinite(value) or value<0:
            raise NotApplicable('invalid_replica_value')
        old = points[row['signal']].get(t)
        if old is not None and old!=value:
            raise NotApplicable('conflicting_replica_sample')
        points[row['signal']][t]=value
    a,d = (points[s] for s in signals)
    cadence = params['cadence_seconds']
    if type(cadence) not in [int,float] or cadence<=0:
        raise NotApplicable('invalid_declared_sampling_cadence')
    grid=sorted(set(a)|set(d))
    if not grid or set(a)!=set(d) or any(abs(right-left-cadence)>1e-6 for left,right in zip(grid,grid[1:])):
        raise NotApplicable('missing_or_unaligned_replica_samples')
    flags = {t:a[t]>=d[t] for t in grid}
    unmet = contiguous(grid,{t:not flags[t] for t in grid},cadence)
    answer = dict(met_percentage=100*sum(flags.values())/len(grid), unmet_periods=[
        dict(first_unmet_sample=iso(run[0]),last_unmet_sample=iso(run[-1])) for run in unmet])
    wrong = 100*sum(a[t]>0 for t in grid)/len(grid)
    return answer, dict(nonzero_available_percentage=wrong,answer_diverges=wrong!=answer['met_percentage'])


def question(family, p):
    service=p.get('service',p.get('deployment')); scope=f"{p.get('workspace','rca-benchmark')} 工作空间，{p['namespace']}，{p['start']}—{p['end']}"
    if family=='deployment_phase_runtime_dual_peak':
        task=p['task']
        phase={'last_met':'最后一段副本连续配齐','longest_met':'副本连续配齐时间最长的一段','last_unmet':'最后一段副本连续未配齐'}[task['phase_operator']]
        cohort='这个时间窗口内新拉起的实例' if task['cohort']=='started_in_question_window' else '观测到的全部实例'
        ranking='分别最高的实例' if task['query_kind']=='peak_pair' else f"分别排名前{task['top_k']}的实例，包含末位并列"
        return f'帮我回看 {scope} 内 {service}，在{phase}期间，{cohort}中 CPU 使用率与 CPU 上限占比{ranking}是哪些？按各自峰值给出结果、峰值首次出现时间和依据，只算实例运行期间的采样。'
    return {
        'replica_fulfillment':f'帮我核对 {scope} 内 {service} 有多少比例的监控采样时刻可用副本配齐了当时配置要求的数量，并列出未配齐的连续采样时段和依据。',
        'alert_transaction_state':f'帮我核对 {scope} 内 {service} 应用及其接口的告警：这段时间有触发记录、到窗口末还没有恢复的有多少起？按规则列出仍在告警的起数，并逐起给出触发时间、最后通知时间、最后状态和涉及对象。只对快照里的通知作答，不推断窗口外的状态。',
        'observed_call_failure_rankings':f'帮我看下 {scope} 内 {service} 的 API（{p.get("api_prefix","")} 及其下的接口）。按快照里记录的调用算，失败量最多和失败比例最高的接口分别是谁，是同一个吗？给我两份完整排名，每个接口带上调用总数、失败数、失败比例和依据。',
        'event_anchored_call_intervals':f'帮我核对 {scope} 内 {service} 的一笔时间账：窗口里第一次出现 {service} 业务容器停止记录时，哪些 API（{p.get("api_prefix","")} 及其下的接口）调用已经开始、但还没有结束？只看起止都在这个窗口里有记录的调用。按接口给出数量、这些调用落在该时点之后的累计耗时，并列出最后结束的那笔调用。累计的是各次调用耗时，不是并行调用合并后的墙钟时间。'
    }[family]


def compile_task(graph, family, params, approved_question=None):
    if family not in REGISTRY:
        raise ValueError('unregistered_family:'+family)
    spec = REGISTRY[family]; p = dict(graph.binding,**params)
    result = dict(schema_version='l2_graph_family_compilation_v1',family_id=family,binding=p,
                  model_calls=0,difficulty='unmeasured',scoring_adapter=family,
                  participant_environment='unchanged full scene; private projection only')
    missing=[k for k in ['start','end','namespace',*spec['required']] if k not in p]
    if missing:
        return dict(result,status='skipped',reason='missing_parameters:'+','.join(missing))
    projection=project_task(graph,family,p);result['projection']=projection.summary()
    try:
        answer, contrast=calculate(projection,family,p)
    except (NotApplicable,ValueError,KeyError) as exc:
        return dict(result,status='skipped',reason=type(exc).__name__+':'+str(exc))
    contract=dict(schema_version='l2_graph_family_scoring_binding_v1',family_id=family,
                  private_ground_truth=answer,required_outputs=spec['outputs'],
                  adapter_status='existing_adapter_binding_not_new_scorer',
                  scope=dict(p),output_order='sets_except_explicit_rankings',visibility='evaluator_only')
    if family=='observed_call_failure_rankings':
        calls=api_calls([r for r in projection.records if r['modality']=='trace'],p['service'],p['api_prefix'])
        lo,hi=epoch_ns(p['start']),epoch_ns(p['end'])
        refs=collections.defaultdict(set)
        for call in calls:
            if lo<=call['start_ns']<=hi:refs[call['operation']].add(call['record_ref'])
        contract['operation_refs']={k:sorted(v) for k,v in sorted(refs.items())}
    return dict(result,status='ready',question=approved_question or question(family,p),
                private_ground_truth=answer,scoring_contract=contract,contrast=contrast,operators=spec['operators'])
