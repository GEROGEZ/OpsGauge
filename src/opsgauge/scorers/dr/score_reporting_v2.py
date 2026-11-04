"""Validity and aggregation policy; never invent zeroes for failed execution."""
import copy,math,statistics

POLICY='v2_invalid_exclusion_saved_answer_recovery_v1'

def valid_execution(row):
 status=row.get('execution_status','completed')
 return row.get('valid_for_comparison',status=='completed' or 'AgentTimeout' in status) is not False

def comparable(row):
 value=row.get('aggregate_score')
 return valid_execution(row) and isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def reporting_row(row):
 row=copy.deepcopy(row)
 if not valid_execution(row):
  row.setdefault('invalid_execution_diagnostics',dict(result=row.get('result'),process=row.get('process'),process_score=row.get('process_score'),aggregate_score=row.get('aggregate_score')))
  row['result']={**(row.get('result') or {}),'score':None,'strict_pass':None}
  if isinstance(row.get('process'),dict):row['process']={**row['process'],'score':None,'strict_pass':None}
  row['process_score']=None;row['aggregate_score']=None
  row['valid_for_comparison']=False;row['aggregate_score_kind']='invalid_execution'
 row['score_validity_policy']=POLICY
 return row

def score_means(rows):
 selected=[r for r in rows if comparable(r)]
 def avg(key):
  values=[(r.get('result') or {}).get('score') if key=='result' else r.get(key) for r in selected]
  assert all(isinstance(v,(int,float)) and math.isfinite(v) for v in values)
  return round(statistics.mean(values),6) if values else None
 return dict(comparable_trials=len(selected),invalid_execution_trials=sum(not valid_execution(r) for r in rows),
  recovered_verifier_trials=sum((r.get('recovery') or {}).get('status')=='recovered_from_saved_answer' for r in rows),
  result_mean=avg('result'),process_mean=avg('process_score'),aggregate_mean=avg('aggregate_score'),
  median=statistics.median(r['aggregate_score'] for r in selected) if selected else None)
