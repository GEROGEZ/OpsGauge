"""Three data-supported L2 query families. No model, no GT-driven answer selection."""
import collections
import datetime as dt
import re


def epoch_ns(value):
    stamp = dt.datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if stamp.tzinfo is None:
        raise ValueError('timezone_required')
    delta=stamp-dt.datetime(1970,1,1,tzinfo=dt.timezone.utc)
    return (delta.days*86400+delta.seconds)*10**9+delta.microseconds*1000


def iso_ns(value):
    seconds,ns=divmod(value,10**9)
    return dt.datetime.fromtimestamp(seconds,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{ns:09d}Z'


def alert_transactions(events, scope_ids, start, end):
    """Notifications != alert transactions; terminal state is per rule+transaction."""
    grouped=collections.defaultdict(dict)
    for event in events:
        a=event['attributes']
        if (a.get('source_stream')!='alert' or not scope_ids.intersection(event['entity_ids'])
                or not start<=epoch_ns(event['observed_at'])<=end):
            continue
        if a.get('status') not in ['OCCURRED','PERSISTENT','RECOVERED']:
            raise ValueError('unsupported_alert_state')
        if not all(a.get(k) for k in ['rule_id','trans_id','event_id']):
            raise ValueError('missing_alert_identity')
        key=(a['rule_id'],a['trans_id'])
        identity=(a['event_id'],epoch_ns(event['observed_at']))
        previous=grouped[key].get(identity)
        if previous and previous['attributes']!=a:
            raise ValueError('conflicting_notification')
        grouped[key][identity]=event
    result=[]
    alternatives=[]
    for (rule,transaction),values in sorted(grouped.items()):
        rows=sorted(values.values(),key=lambda e:(epoch_ns(e['observed_at']),e['attributes']['event_id']))
        latest=[r for r in rows if epoch_ns(r['observed_at'])==epoch_ns(rows[-1]['observed_at'])]
        if len({r['attributes']['status'] for r in latest})>1:
            raise ValueError('ambiguous_terminal_state')
        occurred=[r for r in rows if r['attributes']['status']=='OCCURRED']
        if not occurred:
            continue  # No trigger record observed inside this task window.
        tail=rows[-1]
        row=dict(rule_id=rule,trans_id=transaction,subject=tail['signal'],
                 first_trigger_at=occurred[0]['observed_at'],last_notification_at=tail['observed_at'],
                 last_status=tail['attributes']['status'],trigger_notifications=len(occurred),
                 notification_count=len(rows),
                 entity_ids=sorted(set().union(*(set(r['entity_ids']) for r in rows))),
                 evidence_refs=sorted({occurred[0]['record_ref'],tail['record_ref']}))
        alternatives.append(row)
        if row['last_status']!='RECOVERED':
            result.append(row)
    return result,alternatives


def api_calls(records, service, api_prefix):
    """Count observed calls by (trace,span), not logs, traces or all local methods."""
    calls={}
    for record in records:
        a=record['attributes']
        operation=a.get('operation_name','')
        if a.get('service_name')!=service or not (operation==api_prefix or operation.startswith(api_prefix+'/')):
            continue
        if not all(isinstance(a.get(k),str) and a[k] for k in ['trace_id','span_id']):
            raise ValueError('invalid_span_identity')
        duration=a.get('duration_ns')
        if type(duration) not in [int,float] or duration<0 or int(duration)!=duration:
            raise ValueError('invalid_duration')
        start=epoch_ns(record['observed_at'])
        item=dict(trace_id=a['trace_id'],span_id=a['span_id'],operation=operation,
                  start_ns=start,end_ns=start+int(duration),duration_ns=int(duration),status=str(a.get('status')),
                  record_ref=record['record_ref'])
        if item['status'] not in ['0','1','2']:
            raise ValueError('unknown_span_status')
        identity=(a['trace_id'],a['span_id'])
        previous=calls.get(identity)
        if previous and {k:v for k,v in previous.items() if k!='record_ref'}!={k:v for k,v in item.items() if k!='record_ref'}:
            raise ValueError('conflicting_span')
        calls[identity]=item
    return list(calls.values())


def failure_rankings(calls,start,end):
    grouped=collections.defaultdict(list)
    for call in calls:
        if start<=call['start_ns']<=end:
            grouped[call['operation']].append(call)
    rows=[]
    for operation,values in sorted(grouped.items()):
        failed=[v for v in values if v['status']=='2']
        rows.append(dict(operation=operation,call_count=len(values),error_count=len(failed),
                         error_ratio=len(failed)/len(values),
                         sample_refs=sorted({values[0]['record_ref'],failed[0]['record_ref'] if failed else values[0]['record_ref']})))
    counts=sorted(rows,key=lambda r:(-r['error_count'],r['operation']))
    ratios=sorted(rows,key=lambda r:(-r['error_ratio'],r['operation']))
    return dict(by_failure_count=counts,by_failure_ratio=ratios)


def crossing_calls(calls,events,namespace,service,start,end):
    markers=[e for e in events if e['attributes'].get('source_stream')=='k8s_event'
             and e['attributes'].get('namespace')==namespace and e['attributes'].get('event_type')=='Killing'
             and e['attributes'].get('object_kind')=='Pod'
             and e['attributes'].get('message')=='Stopping container '+service
             and start<=epoch_ns(e['observed_at'])<=end]
    if not markers:
        raise ValueError('no_business_stop_marker')
    first=min(epoch_ns(e['observed_at']) for e in markers)
    anchors=[e for e in markers if epoch_ns(e['observed_at'])==first]
    selected=[c for c in calls if start<=c['start_ns']<first<c['end_ns']<=end]
    grouped=collections.defaultdict(list)
    for call in selected:
        grouped[call['operation']].append(call)
    rows=[]
    for operation,values in sorted(grouped.items()):
        last=max(c['end_ns'] for c in values)
        final=[c for c in values if c['end_ns']==last]
        rows.append(dict(operation=operation,call_count=len(values),
                         after_marker_total_ns=sum(c['end_ns']-first for c in values),
                         full_duration_total_ns=sum(c['duration_ns'] for c in values),
                         last_completed_calls=[dict(trace_id=c['trace_id'],span_id=c['span_id'],
                                                    end=iso_ns(c['end_ns']),record_ref=c['record_ref']) for c in final]))
    return dict(anchor=iso_ns(first),anchor_refs=[e['record_ref'] for e in anchors],results=rows,
                selected_calls=[dict(**c,after_marker_ns=c['end_ns']-first) for c in selected])
