"""Harness-neutral deterministic scoring-v2 for Codex and Claude Code Harbor runs."""
from __future__ import annotations
import argparse,collections,fcntl,json,math,os,re,statistics,sys,tempfile,time
from contextlib import contextmanager
from pathlib import Path
from score_reporting_v2 import POLICY,reporting_row,score_means

def load_artifact(folder,name):
 for p in [folder/'artifacts/app'/name,folder/'artifacts'/name]:
  if p.is_file():
   try:
    x=json.loads(p.read_text());return x if isinstance(x,dict) else None
   except (ValueError,UnicodeError):pass
 return None

def objects(text):
 d=json.JSONDecoder();i=0
 while i<len(text):
  if text[i] not in '{[':i+=1;continue
  try:x,j=d.raw_decode(text,i)
  except ValueError:i+=1;continue
  yield x;i=j

def normalize_trace(folder):
 raw=folder/'agent/codex.txt'
 if raw.exists():return raw,'codex_raw'
 trajectory=folder/'agent/trajectory.json'
 if not trajectory.exists():return raw,'unavailable'
 x=json.loads(trajectory.read_text());events=[];index=0
 for step in x.get('steps',[]):
  if step.get('source')!='agent':continue
  calls={c.get('tool_call_id'):c for c in step.get('tool_calls',[]) if isinstance(c,dict)}
  for result in (step.get('observation') or {}).get('results',[]) or []:
   if not isinstance(result,dict) or not isinstance(result.get('content'),str):continue
   content=result['content'];match=re.search(r'Process exited with code (\d+)',content)
   if match and int(match.group(1))!=0:continue
   call=calls.get(result.get('source_call_id'),{})
   events.append({'type':'item.completed','item':{'id':result.get('source_call_id') or f'atif-{index}','type':'command_execution','command':json.dumps(call.get('arguments',{}),ensure_ascii=False),'aggregated_output':content,'exit_code':0,'status':'completed'}});index+=1
 work=load_artifact(folder,'work-evidence.json');candidate=(work or {}).get('candidate')
 if isinstance(candidate,dict):
  events.append({'type':'item.completed','item':{'id':'work-evidence-candidate','type':'command_execution','command':'derived structured work evidence','aggregated_output':json.dumps(candidate,ensure_ascii=False),'exit_code':0,'status':'completed'}})
 raw.write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in events)+'\n')
 (folder/'agent/trace-adapter.json').write_text(json.dumps({'version':'atif_to_completed_tool_json_v1','source':'trajectory.json','events':len(events),'work_candidate_appended':isinstance(candidate,dict)},indent=2))
 return raw,'atif_normalized'

def query_v2(folder,contract,qs,trace_source):
 result=json.loads((folder/'result.json').read_text());exception=(result.get('exception_info') or {}).get('exception_type');status=exception or 'completed';valid=not exception or 'AgentTimeout' in exception
 submitted=load_artifact(folder,'answer.json');work=load_artifact(folder,'work-evidence.json');work_candidate=(work or {}).get('candidate') if isinstance((work or {}).get('candidate'),dict) else None
 roots=set(contract['private_ground_truth'])-{'marker_evidence_refs'};candidates=[];trace=folder/'agent/codex.txt'
 if trace.exists():
  for i,line in enumerate(trace.read_text().splitlines()):
   try:event=json.loads(line)
   except ValueError:continue
   item=event.get('item') or {}
   if event.get('type')!='item.completed' or item.get('type')!='command_execution' or item.get('exit_code')!=0:continue
   for obj in objects(item.get('aggregated_output','')):
    if isinstance(obj,dict) and roots.intersection(obj) and 'page' not in obj:candidates.append((i,obj))
 process_candidate=work_candidate if work_candidate is not None else candidates[-1][1] if candidates else None
 source='work_evidence_artifact' if work_candidate is not None else 'latest_completed_tool_json' if candidates else 'unavailable'
 rs=qs.compare(contract,submitted) if submitted is not None else dict(score=0,strict_pass=False,checks=[]);ps=qs.compare(contract,process_candidate) if process_candidate is not None else dict(score=0,strict_pass=False,checks=[])
 return dict(scoring_version='l2_factorial_harness_neutral_v2',trial=folder.name,execution_status=status,valid_for_comparison=valid,submitted=submitted is not None,result=rs,process=ps,process_score=ps['score'],process_candidate_source=source,process_observable=process_candidate is not None,aggregate_score=round(.6*rs['score']+.4*ps['score'],8) if valid else None,aggregate_policy=dict(result_weight=.6,process_weight=.4,version='result60_process40_v2'),trace_source=trace_source,agent_rerun=False,model_calls_for_scoring=0)

def install_comparator():
 import query_scorer as qs
 original=getattr(qs,'_factorial_v2_original_equal',qs.equal)
 qs._factorial_v2_original_equal=original
 def eq(a,b,key=''):
  if key=='end':
   try:return abs(qs.instant(a)-qs.instant(b))<=1000
   except (TypeError,ValueError):return False
  if key=='after_marker_total_ms' and isinstance(b,(int,float)) and not isinstance(b,bool):return type(a) in (int,float) and math.isfinite(a) and math.isclose(a,b,rel_tol=1e-7,abs_tol=.002)
  return original(a,b,key)
 qs.equal=eq
 return qs

@contextmanager
def isolated_view(folder,workspace):
 """Read source artifacts via links; normalize Claude only in private scratch."""
 workspace.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='score-',dir=workspace) as tmp:
  view=Path(tmp)/folder.name;view.mkdir();(view/'agent').mkdir()
  for name in ['result.json','trial.log','artifacts']:
   if (folder/name).exists():(view/name).symlink_to((folder/name).resolve(),target_is_directory=(folder/name).is_dir())
  for name in ['codex.txt','trajectory.json']:
   if (folder/'agent'/name).exists():(view/'agent'/name).symlink_to((folder/'agent'/name).resolve())
  yield view

def score_trial(folder,contract,workspace=None):
 """Single frozen v2 entry used by live runners and offline backfills alike."""
 from family_runtime import score_trial as legacy_score
 folder=Path(folder);qs=install_comparator()
 raw_result=(folder/'result.json').read_bytes()
 original_result=json.loads(raw_result);original_status=(original_result.get('exception_info') or {}).get('exception_type') or 'completed'
 recovered=(original_status=='VerifierTimeoutError' and bool((original_result.get('agent_execution') or {}).get('finished_at')) and load_artifact(folder,'answer.json') is not None)
 workspace=Path(workspace) if workspace else folder.parents[2]/'scoring-v2/.work'
 with isolated_view(folder,workspace) as view:
  if recovered:
   # Only the private view is changed. The actual Harbor result stays intact.
   replay=dict(original_result,exception_info=None)
   (view/'result.json').unlink();(view/'result.json').write_text(json.dumps(replay))
  _,trace_source=normalize_trace(view)
  if contract['family_id']=='deployment_phase_runtime_dual_peak':
   row=legacy_score(view,contract);row['scoring_version']='l2_factorial_harness_neutral_v2';row['trace_source']=trace_source;row['aggregate_score']=round(.6*(row.get('result') or {}).get('score',0)+.4*row.get('process_score',0),8);row['aggregate_policy']=dict(result_weight=.6,process_weight=.4,version='result60_process40_v2');row['agent_rerun']=False;row['model_calls_for_scoring']=0
  else:row=query_v2(view,contract,qs,trace_source)
 assert raw_result==(folder/'result.json').read_bytes(),'Source result changed during scoring'
 if recovered:
  row.update(execution_status=original_status,valid_for_comparison=True,
   recovery=dict(status='recovered_from_saved_answer',policy=POLICY,source='saved_answer_and_frozen_contract',original_execution_status=original_status,
    original_result_preserved=True,result_rescored=True,process_evidence_checked_by_program=True,agent_rerun=False))
 elif original_status=='VerifierTimeoutError':
  row['recovery']=dict(status='unrecoverable_saved_answer_or_execution_missing',policy=POLICY,agent_rerun=False)
 return reporting_row(row)

def atomic_json(path,data):
 temporary=path.with_name(path.name+'.'+str(os.getpid())+'.part')
 temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');temporary.replace(path)

def run(root,incremental=False,output=None):
 root=Path(root);sys.path.insert(0,str(root/'code'))
 out=Path(output) if output else root/'scoring-v2';out.mkdir(parents=True,exist_ok=True)
 with (out/'.writer.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  m=json.loads((root/'manifest.json').read_text());expected={t['task_name'] for t in m['tasks']}
  old=json.loads((out/'scores.json').read_text()) if (out/'scores.json').exists() else {}
  saved={r['trial']:reporting_row(r) for r in old.get('trials',[]) if r.get('scoring_status')!='failed'
   and not (r.get('execution_status')=='VerifierTimeoutError' and (r.get('recovery') or {}).get('policy')!=POLICY)}
  errors=[];seen=collections.Counter();terminal=0
  for folder in sorted(p.parent for p in (root/'jobs').rglob('result.json') if (p.parent/'trial.log').is_file()):
   task=folder.name.rsplit('__',1)[0];assert task in expected,task
   seen[task]+=1;assert seen[task]==1,'Duplicate task trials: '+task
   terminal+=1
   if folder.name in saved:continue
   if incremental and time.time()-(folder/'result.json').stat().st_mtime<15:continue
   contract=json.loads((root/'package/harbor-tasks'/task/'tests/private/contract.json').read_text())
   try:saved[folder.name]=score_trial(folder,contract,out/'.work')
   except Exception as exc:errors.append(dict(trial=folder.name,error_type=type(exc).__name__))
  rows=[saved[k] for k in sorted(saved)];sources=dict(collections.Counter(r.get('trace_source') for r in rows))
  complete=len(rows)==len(expected) and terminal==len(expected) and not errors
  doc=dict(schema_version='l2_factorial_scores_v2',created_on='2026-10-23',status='complete' if complete else 'partial',agent_rerun=False,model_calls_for_scoring=0,score_validity_policy=POLICY,aggregate_policy=dict(result_weight=.6,process_weight=.4),trace_sources=sources,trials=rows)
  atomic_json(out/'scores.json',doc)
  means=score_means(rows)
  summary=dict(status='passed' if complete else 'scoring_error' if errors else 'partial',trials=len(rows),expected_trials=len(expected),terminal_trials=terminal,mean=means['aggregate_mean'],**means,score_validity_policy=POLICY,scoring_failed=len(errors),errors=errors,trace_sources=sources,agent_rerun=False,model_calls_for_scoring=0)
  atomic_json(out/'progress.json',summary)
  # Existing completion supervisors interpret summary.json as a final gate.
  # Never publish that filename for a partial batch.
  if complete:atomic_json(out/'summary.json',summary)
  if not incremental:assert complete,'Scoring incomplete; see progress.json'
 return summary

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--incremental',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args();print(json.dumps(run(a.root,a.incremental,a.output)))
