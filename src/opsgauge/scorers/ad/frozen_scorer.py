import gzip,json,math,pathlib

def _load(root):
 records={}
 with gzip.open(root/'public/evidence.jsonl.gz','rt') as f:
  for line in f:
   x=json.loads(line);records[x['record_ref']]=x
 return records,json.loads((root/'private/case-config.json').read_text())
def _claims(payload):return {x.get('id'):x for x in payload.get('claims',[]) if isinstance(x,dict) and x.get('id')} if isinstance(payload,dict) else {}
def _value_ok(expected,actual,tolerance):
 if expected is None:return True
 if isinstance(expected,bool) or isinstance(actual,bool):return expected==actual
 if isinstance(expected,(int,float)) and isinstance(actual,(int,float)):
  return math.isfinite(float(actual)) and abs(float(expected)-float(actual))<=float(tolerance or 0)
 return expected==actual
def score(root,answer,progress=None):
 records,cfg=_load(root);a=_claims(answer);p=_claims(progress or {})
 def valid(c):
  refs=c.get('evidence_refs',[]) if isinstance(c,dict) else []
  return bool(refs) and all(x in records for x in refs)
 refs=[]
 for c in list(a.values())+list(p.values()):
  if valid(c):refs+=c['evidence_refs']
 groups={records[x]['source_group'] for x in set(refs)}
 req=cfg.get('process_requirements',{'min_unique_refs':3,'min_groups':2})
 process=[{'id':'valid_evidence_selection','possible':20,'earned':20 if len(set(refs))>=int(req.get('min_unique_refs',3)) and len(groups)>=int(req.get('min_groups',2)) else 0},{'id':'interim_analytical_progress','possible':10,'earned':10 if progress and progress.get('state')=='active' and sum(valid(x) for x in p.values())>=2 else 0}]
 boundary_ids={x['id'] for x in cfg['claims'] if x['status']=='not_established'}
 boundary=any(i in boundary_ids and a.get(i,{}).get('status')=='not_established' and valid(a.get(i)) for i in boundary_ids)
 process.append({'id':'explicit_unknown_boundary','possible':10,'earned':10 if boundary else 0})
 final=isinstance(answer,dict) and answer.get('state')=='final';result=[]
 for spec in cfg['claims']:
  c=a.get(spec['id']);rs=c.get('evidence_refs',[]) if isinstance(c,dict) else [];gs={records[x]['source_group'] for x in rs if x in records}
  ok=final and valid(c) and c.get('status')==spec['status'] and set(spec.get('required_groups',[]))<=gs and _value_ok(spec.get('value'),c.get('value'),spec.get('tolerance',0))
  result.append({'id':spec['id'],'possible':spec['weight'],'earned':spec['weight'] if ok else 0})
 pe=sum(x['earned'] for x in process);pp=sum(x['possible'] for x in process);re=sum(x['earned'] for x in result);rp=sum(x['possible'] for x in result);ps=100*pe/pp if pp else 0;rs=100*re/rp if rp else 0
 return {'schema_version':'information_level_process_result_v2','process_score':ps,'result_score':rs,'composite_score':ps*0.4+rs*0.6,'weights':{'process':0.4,'result':0.6},'process_items':process,'result_items':result,'unverified_policy':'zero'}
