"""Bounded SQL-paged UModel semantic replay server, revision 2026-10-23.

Unlike the original in-memory replay engine, this server never materializes an
entire million-record modality before applying a page limit. It preserves the
same participant-facing semantic operations and stable evidence references.
"""
from __future__ import annotations
import datetime as dt,json,os,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
import duckdb

DB=os.environ.get('REPLAY_DB','/scene/replay.duckdb');BUNDLE=os.environ['BUNDLE_ID'];ENV=os.environ.get('ENVIRONMENT_ID',BUNDLE);LOCK=threading.Lock()

def parse(v,d):
 if v is None:return d
 if isinstance(v,(dict,list)):return v
 try:return json.loads(v)
 except:return d

def iso(v):return v.isoformat() if hasattr(v,'isoformat') else str(v)
def rows(sql,args=()):
 with LOCK:
  c=duckdb.connect(DB,read_only=True)
  try:
   cur=c.execute(sql,args);names=[x[0] for x in cur.description];return [dict(zip(names,r)) for r in cur.fetchall()]
  finally:c.close()
def env_ok(p):return p.get('environment_id')==ENV and p.get('workspace')=='rca-benchmark' and p.get('region_id')=='cn-hongkong'
def page_params(p,cap):
 limit=min(max(int(p.get('page_size',100)),1),cap);offset=max(int(p.get('cursor') or 0),0);return limit,offset
def page(items,limit,offset):return {'result_count':len(items),'truncated':len(items)==limit,'next_cursor':str(offset+len(items)) if len(items)==limit else None}
def entities(payload):
 limit,offset=page_params(payload,200);where=['bundle_id=?'];args=[BUNDLE];types=payload.get('entity_types') or []
 if types:where.append('entity_type in ('+','.join('?' for _ in types)+')');args.extend(types)
 q=str(payload.get('query') or '').strip().lower()
 if q:where.append('(lower(entity_name) like ? or lower(cast(attributes as varchar)) like ?)');args.extend(['%'+q+'%','%'+q+'%'])
 data=rows('select entity_id,entity_name,entity_type,entity_domain,attributes from entities where '+' and '.join(where)+' order by entity_type,entity_name,entity_id limit ? offset ?',args+[limit,offset])
 for r in data:r['attributes']=parse(r['attributes'],{})
 return {'results':data,'page':page(data,limit,offset)}
def selected_entities(selector):
 where=['bundle_id=?'];args=[BUNDLE];ids=selector.get('entity_ids') or [];types=selector.get('entity_types') or []
 if ids:where.append('entity_id in ('+','.join('?' for _ in ids)+')');args.extend(ids)
 if types:where.append('entity_type in ('+','.join('?' for _ in types)+')');args.extend(types)
 return [r['entity_id'] for r in rows('select entity_id from entities where '+' and '.join(where),args)]
def base_records(payload,kind):
 limit,offset=page_params(payload,1000);tr=payload.get('time_range') or {};selector=payload.get('entity_selector') or {};selected=selected_entities(selector)
 where=['bundle_id=?','modality=?'];args=[BUNDLE,kind]
 if tr.get('start') and tr.get('end'):
  col='time_start' if kind=='trace' else 'observed_at';where += [col+'>=?::TIMESTAMPTZ',col+'<=?::TIMESTAMPTZ'];args += [tr['start'],tr['end']]
 # If the selector covers every entity, avoid a JSON membership scan.
 all_count=rows('select count(*) n from entities where bundle_id=?',[BUNDLE])[0]['n']
 if selected and len(selected)<all_count:
  where.append('('+' or '.join('json_contains(entity_ids,to_json(?::varchar))' for _ in selected)+')');args.extend(selected)
 data=rows('select record_ref,provenance_ref,modality,entity_ids,signal,time_start,time_end,observed_at,value_json,unit,stat,dimensions,attributes from records where '+' and '.join(where)+' order by observed_at,record_ref limit ? offset ?',args+[limit,offset])
 for r in data:
  r['entity_ids']=parse(r['entity_ids'],[]);r['value_json']=parse(r['value_json'],r['value_json']);r['dimensions']=parse(r['dimensions'],{});r['attributes']=parse(r['attributes'],{});r['time_start']=iso(r['time_start']);r['time_end']=iso(r['time_end']);r['observed_at']=iso(r['observed_at'])
 return data,limit,offset
def query(payload):
 kind=(payload.get('query') or {}).get('kind');data,limit,offset=base_records(payload,kind);out=[]
 for r in data:
  a=r['attributes'];prov={'record_ref':r['record_ref'],'provenance_ref':r['provenance_ref']}
  if kind=='trace':
   out.append({'record_ref':r['record_ref'],'trace_id':str(a.get('trace_id') or ''),'span_id':str(a.get('span_id') or ''),'parent_span_id':str(a.get('parent_span_id') or ''),'service_name':str(a.get('service_name') or ''),'operation_name':str(a.get('operation_name') or r['signal']),'status':str(a.get('status') or a.get('statusCode') or ''),'start_time':r['time_start'],'end_time':r['time_end'],'entity_ids':r['entity_ids'],'attributes':a,'provenance':prov})
  elif kind=='event':out.append({'record_ref':r['record_ref'],'event_type':r['signal'],'occurred_at':r['observed_at'],'entity_ids':r['entity_ids'],'status':str(a.get('status') or ''),'rule_id':str(a.get('rule_id') or ''),'attributes':a,'value':r['value_json'],'provenance':prov})
  elif kind=='log':out.append({'record_ref':r['record_ref'],'timestamp':r['observed_at'],'entity_ids':r['entity_ids'],'text':str(a.get('message') or a.get('body') or r['signal']),'attributes':a,'value':r['value_json'],'provenance':prov})
  elif kind=='topology':out.append({'record_ref':r['record_ref'],'observed_at':r['observed_at'],'entity_ids':r['entity_ids'],'relation':r['signal'],'attributes':a,'value':r['value_json'],'provenance':prov})
  elif kind=='metric':out.append({'record_ref':r['record_ref'],'entity_ids':r['entity_ids'],'metric_name':r['signal'],'stat':r['stat'],'value':r['value_json'],'unit':r['unit'],'dimensions':r['dimensions'],'observed_at':r['observed_at'],'provenance':prov})
 return {'request_id':payload.get('request_id',''),'results':out,'page':page(out,limit,offset)}
def signals(payload):
 mods=payload.get('modalities') or ['metric','event','trace','log','topology'];data=rows('select distinct modality,signal,unit,stat from records where bundle_id=? and modality in ('+','.join('?' for _ in mods)+') order by modality,signal,unit,stat',[BUNDLE,*mods]);return {'signals':data,'page':{'result_count':len(data),'truncated':False,'next_cursor':None}}
class H(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def sendj(self,status,payload):
  raw=json.dumps(payload,ensure_ascii=False,default=str,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  path=urlsplit(self.path).path
  if path=='/v1/health':return self.sendj(200,{'ready':True,'environment_id':ENV})
  if path=='/v1/capabilities':return self.sendj(200,{'profile':'umodel_semantic','query_kinds':['metric','event','trace','log','topology'],'pagination':'cursor','max_page_size':1000})
  return self.sendj(404,{'error':{'code':'NOT_FOUND'}})
 def do_POST(self):
  try:
   n=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(n));
   if not env_ok(payload):return self.sendj(403,{'error':{'code':'ENVIRONMENT_MISMATCH'}})
   path=urlsplit(self.path).path
   if path=='/v1/entities/search':return self.sendj(200,entities(payload))
   if path=='/v1/signals':return self.sendj(200,signals(payload))
   if path=='/v1/query':
    kind=(payload.get('query') or {}).get('kind')
    if kind not in {'metric','event','trace','log','topology'}:return self.sendj(400,{'error':{'code':'INVALID_QUERY_KIND'}})
    return self.sendj(200,query(payload))
   return self.sendj(404,{'error':{'code':'NOT_FOUND'}})
  except (ValueError,TypeError,json.JSONDecodeError) as e:return self.sendj(400,{'error':{'code':'INVALID_REQUEST','message':str(e)}})
  except Exception as e:return self.sendj(502,{'error':{'code':'BACKEND_UNAVAILABLE','type':type(e).__name__,'message':str(e)}})
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8080),H).serve_forever()
