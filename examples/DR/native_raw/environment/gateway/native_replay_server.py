"""Read-only Native replay API over one frozen DuckDB scene plus PromQL proxy."""
from __future__ import annotations
import datetime as dt,json,math,os,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit
from urllib.request import urlopen
import duckdb

DB=os.environ.get('REPLAY_DB','/scene/replay.duckdb')
PROM=os.environ.get('PROMETHEUS_UPSTREAM','http://prometheus:9090')
ROUTES={'/v1/resources':'resource','/v1/events':'event','/v1/logs':'log','/v1/traces':'trace'}

def parse_json(v,default):
 if v is None:return default
 if isinstance(v,(dict,list)):return v
 try:return json.loads(v)
 except Exception:return default

def iso(v):
 if v is None:return None
 if hasattr(v,'isoformat'):return v.isoformat()
 return str(v)

def timestamp(v):
 if v in (None,''):return None
 try:return float(v)
 except Exception:
  x=dt.datetime.fromisoformat(str(v).replace('Z','+00:00'))
  if x.tzinfo is None:raise ValueError('timezone required')
  return x.timestamp()

def db_rows(sql,args=()):
 c=duckdb.connect(DB,read_only=True)
 try:
  cur=c.execute(sql,args);names=[x[0] for x in cur.description]
  return [dict(zip(names,r)) for r in cur.fetchall()]
 finally:c.close()

def resources(params):
 limit=min(max(int(params.get('limit',50)),1),1000);offset=max(int(params.get('offset',0)),0)
 where=[];args=[]
 if params.get('entity_id'):where.append('entity_id=?');args.append(params['entity_id'])
 if params.get('entity_type'):where.append('entity_type=?');args.append(params['entity_type'])
 if params.get('q'):
  where.append('(lower(entity_name) like ? or lower(cast(attributes as varchar)) like ?)');q='%'+params['q'].lower()+'%';args.extend([q,q])
 clause=(' where '+' and '.join(where)) if where else ''
 total=db_rows('select count(*) n from entities'+clause,args)[0]['n']
 rows=db_rows('select entity_id,entity_name,entity_type,entity_domain,attributes from entities'+clause+' order by entity_type,entity_name,entity_id limit ? offset ?',args+[limit,offset])
 for r in rows:r['attributes']=parse_json(r['attributes'],{})
 return dict(items=rows,total=total,offset=offset,limit=limit,next_offset=offset+len(rows) if offset+len(rows)<total else None)

def records(modality,params):
 limit=min(max(int(params.get('limit',50)),1),1000);offset=max(int(params.get('offset',0)),0)
 where=['modality=?'];args=[modality]
 if params.get('record_ref'):where.append('record_ref=?');args.append(params['record_ref'])
 if params.get('signal'):where.append('signal=?');args.append(params['signal'])
 if params.get('start'):where.append('epoch(observed_at)>=?');args.append(timestamp(params['start']))
 if params.get('end'):where.append('epoch(observed_at)<=?');args.append(timestamp(params['end']))
 if params.get('entity_id'):where.append("json_contains(entity_ids, to_json(?::varchar))");args.append(params['entity_id'])
 if params.get('trace_id'):
  where.append("coalesce(json_extract_string(attributes,'$.trace_id'),json_extract_string(attributes,'$.body.trace_id'),'')=?");args.append(params['trace_id'])
 if params.get('q'):
  where.append('(lower(cast(attributes as varchar)) like ? or lower(cast(value_json as varchar)) like ?)');q='%'+params['q'].lower()+'%';args.extend([q,q])
 clause=' where '+' and '.join(where)
 total=db_rows('select count(*) n from records'+clause,args)[0]['n']
 rows=db_rows('select record_ref,provenance_ref,modality,entity_ids,signal,observed_at,value_json,unit,stat,dimensions,attributes from records'+clause+' order by observed_at,record_ref limit ? offset ?',args+[limit,offset])
 for r in rows:
  r['entity_ids']=parse_json(r['entity_ids'],[]);r['value_json']=parse_json(r['value_json'],r['value_json']);r['dimensions']=parse_json(r['dimensions'],{});r['attributes']=parse_json(r['attributes'],{});r['observed_at']=iso(r['observed_at'])
 return dict(items=rows,total=total,offset=offset,limit=limit,next_offset=offset+len(rows) if offset+len(rows)<total else None)

def handler():
 lock=threading.Lock()
 class H(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def sendj(self,status,payload):
   audit=os.getenv('NATIVE_AUDIT_PATH')
   if audit and urlsplit(self.path).path!='/health':
    event=dict(at=dt.datetime.now(dt.timezone.utc).isoformat(),request=self.path,status=status,total=payload.get('total'),record_refs=[x.get('record_ref') for x in payload.get('items',[]) if x.get('record_ref')])
    with lock:
     with open(audit,'a') as f:f.write(json.dumps(event)+'\n')
   raw=json.dumps(payload,ensure_ascii=False,allow_nan=False,default=str).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_GET(self):
   u=urlsplit(self.path)
   try:
    if u.path=='/health':return self.sendj(200,{'ready':True})
    if u.path=='/v1/catalog':return self.sendj(200,{'profile':'native_raw','metric_protocol':'PromQL','topology':'/scene/topology.jsonl','resources':'/scene/entities.jsonl and /v1/resources','endpoints':ROUTES,'filters':['entity_id','entity_type','signal','trace_id','record_ref','start','end','q','offset','limit'],'pagination':'follow next_offset until null'})
    if u.path.startswith('/api/v1/'):
     try:
      with urlopen(PROM+self.path,timeout=60) as r:data=json.load(r)
     except HTTPError as e:return self.sendj(e.code,json.loads(e.read()))
     return self.sendj(200,data)
    p=parse_qs(u.query,keep_blank_values=True)
    if any(len(v)!=1 for v in p.values()):raise ValueError('duplicate parameter')
    p={k:v[0] for k,v in p.items()}
    if u.path=='/v1/resources':return self.sendj(200,resources(p))
    if u.path in ROUTES:return self.sendj(200,records(ROUTES[u.path],p))
    return self.sendj(404,{'error':'unknown endpoint'})
   except (ValueError,TypeError):return self.sendj(400,{'error':'invalid query'})
   except Exception as e:return self.sendj(502,{'error':'backend unavailable','type':type(e).__name__})
 return H

if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8080),handler()).serve_forever()
