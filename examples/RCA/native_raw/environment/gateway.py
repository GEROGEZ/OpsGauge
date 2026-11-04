"""Read-only native-tool replay API; no evaluator input or inferred evidence."""
import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import re
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

ROUTES = {'/v1/events':'event', '/v1/logs':'log', '/v1/traces':'trace',
          '/v1/topology':'topology', '/v1/metrics/index':'metric',
          '/v1/resources':'resource'}

def timestamp(s):
    if not s: return None
    try: return float(s)
    except (ValueError, TypeError):
        t=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if t.tzinfo is None: raise ValueError('timestamp requires timezone')
        return t.timestamp()

def record_trace_id(row):
    attrs=row.get('attributes') or {}
    body=attrs.get('body') or {}
    if isinstance(body,str):
        try:body=json.loads(body)
        except (ValueError,TypeError):body={}
    if not isinstance(body,dict):body={}
    value=row.get('trace_id') or attrs.get('trace_id') or body.get('trace_id') or ''
    if isinstance(value,float) and not math.isfinite(value):return ''
    return str(value)

def dimension_label_name(key):
    # Injective UTF-8 encoding: no reserved-name or normalization collision.
    return 'dim_'+str(key).encode('utf-8').hex()

def metric_identity(row):
    name=re.sub('[^a-zA-Z0-9_:]','_',row['signal'])
    if not name or name[0].isdigit():name='metric_'+name
    stat=row.get('stat','identity')
    if stat!='identity':name+='__recorded_'+re.sub('[^a-zA-Z0-9_]','_',stat)
    labels={'entity_ids':','.join(sorted(row.get('entity_ids',[]))),
            'metric_unit':row.get('unit',''),'source_stat':stat}
    ids=row.get('entity_ids',[])
    if len(ids)==1:labels['entity_id']=ids[0]
    for k,v in sorted((row.get('dimensions') or {}).items()):
        labels[dimension_label_name(k)]=str(v)
    if stat!='identity':labels['observation_ref']=row['record_ref']
    t=timestamp(row.get('observed_at'))
    if t is not None and abs(t*1000-round(t*1000))>0.001:
        labels['source_observed_at']=row['observed_at']
        labels['observation_ref']=row['record_ref']
    return name,labels

def export_metrics(scene, path):
    series={};seen={};counts={'source_rows':0,'unique_samples':0,'identical_duplicates':0}
    with (pathlib.Path(scene)/'telemetry/metric.jsonl').open() as handle:
        for line in handle:
            row=json.loads(line);v=row.get('value');counts['source_rows']+=1
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):
                raise ValueError('non-numeric metric cannot be exported without conversion')
            name,labels=metric_identity(row);t=math.floor(timestamp(row['observed_at'])*1000)/1000
            labeltext=','.join(k+'='+json.dumps(str(v),ensure_ascii=False) for k,v in sorted(labels.items()) if str(v)!='')
            key=(name,labeltext,t)
            if key in seen:
                if seen[key]!=v:raise ValueError('conflicting metric samples at identical label/time')
                counts['identical_duplicates']+=1;continue
            seen[key]=v;counts['unique_samples']+=1
            series.setdefault(name,[]).append((labeltext,t,v))
    with pathlib.Path(path).open('w') as f:
        for name,samples in sorted(series.items()):
            f.write(f'# HELP {name} archived observation; preserve source_stat and metric_unit\n# TYPE {name} gauge\n')
            for labels,t,v in sorted(samples):f.write(f'{name}{{{labels}}} {v:.17g} {t:.9f}\n')
        f.write('# EOF\n')
    return counts

def build(scene, destination):
    scene=pathlib.Path(scene);destination=pathlib.Path(destination)
    if destination.exists(): raise ValueError('refusing to overwrite database')
    con=sqlite3.connect(destination)
    con.executescript('''CREATE TABLE records(id INTEGER PRIMARY KEY, modality TEXT,
      ref TEXT, observed REAL, signal TEXT, trace_id TEXT, payload TEXT);
      CREATE TABLE entities(record_id INTEGER, entity_id TEXT);
      CREATE INDEX entity_idx ON entities(entity_id,record_id);
      CREATE INDEX record_idx ON records(modality,observed,id);
      CREATE INDEX ref_idx ON records(ref);
      CREATE INDEX trace_idx ON records(trace_id);''')
    counts={}
    sources=[('resource',scene/'entities.jsonl')]+[(p.stem,p) for p in sorted((scene/'telemetry').glob('*.jsonl'))]
    for modality,path in sources:
        count=0
        with path.open() as f:
            for line in f:
                if not line.strip(): continue
                row=json.loads(line);attrs=row.get('attributes') or {}
                if not isinstance(attrs,dict): attrs={}
                ids=row.get('entity_ids') or ([row['entity_id']] if row.get('entity_id') else [])
                cursor=con.execute('INSERT INTO records(modality,ref,observed,signal,trace_id,payload) VALUES(?,?,?,?,?,?)',
                    (modality,row.get('record_ref'),timestamp(row.get('observed_at')),
                     row.get('signal',''),record_trace_id(row),json.dumps(row,ensure_ascii=False)))
                con.executemany('INSERT INTO entities VALUES(?,?)',[(cursor.lastrowid,e) for e in ids]);count+=1
        counts[modality]=count
    con.commit();con.close();return counts

def query(database, route, params):
    if route not in ROUTES: raise KeyError('unknown endpoint')
    allowed={'entity_id','start','end','signal','trace_id','record_ref','q','offset','limit'}
    if set(params)-allowed: raise ValueError('unsupported query parameter')
    limit=int(params.get('limit','50'));offset=int(params.get('offset','0'))
    if not 1<=limit<=200 or offset<0: raise ValueError('limit 1..200; offset >= 0')
    where=['r.modality=?'];args=[ROUTES[route]]
    if params.get('entity_id'):
        where.append('EXISTS(SELECT 1 FROM entities e WHERE e.record_id=r.id AND e.entity_id=?)');args.append(params['entity_id'])
    for k,col in [('signal','signal'),('trace_id','trace_id'),('record_ref','ref')]:
        if params.get(k):where.append('r.'+col+'=?');args.append(params[k])
    for k,op in [('start','>='),('end','<=')]:
        if params.get(k):where.append('r.observed'+op+'?');args.append(timestamp(params[k]))
    if params.get('start') and params.get('end') and timestamp(params['start'])>timestamp(params['end']):
        raise ValueError('start must not exceed end')
    if params.get('q'):where.append('instr(lower(r.payload),lower(?))>0');args.append(params['q'])
    con=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)
    try:
        sql=' FROM records r WHERE '+' AND '.join(where)
        total=con.execute('SELECT count(*)'+sql,args).fetchone()[0]
        rows=[json.loads(r[0]) for r in con.execute('SELECT r.payload'+sql+' ORDER BY r.id LIMIT ? OFFSET ?',args+[limit,offset])]
    finally:con.close()
    if route=='/v1/metrics/index':
        rows=[{**{k:r.get(k) for k in ['record_ref','entity_ids','signal','stat','unit','observed_at','time_range','provenance']},
               'prometheus_metric_name':metric_identity(r)[0],
               'prometheus_labels':metric_identity(r)[1],
               'prometheus_sample_time':math.floor(timestamp(r['observed_at'])*1000)/1000,
               'dimension_label_map':{k:dimension_label_name(k) for k in (r.get('dimensions') or {})}} for r in rows]
    return {'items':rows,'total':total,'offset':offset,'limit':limit,
            'next_offset':offset+len(rows) if offset+len(rows)<total else None}

def wire_encode(payload):
    fields=[]
    def visit(x,path):
        if isinstance(x,float) and not math.isfinite(x):
            token='NaN' if math.isnan(x) else ('Infinity' if x>0 else '-Infinity')
            fields.append({'path':path,'source_token':token});return token
        if isinstance(x,dict):return {k:visit(v,path+[k]) for k,v in x.items()}
        if isinstance(x,list):return [visit(v,path+[i]) for i,v in enumerate(x)]
        return x
    result=visit(payload,[])
    if fields:result={**result,'source_value_encoding':{'scheme':'nonfinite-token-v1','fields':fields}}
    return result

def handler(database, prometheus):
    audit_lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,payload):
            audit=os.getenv('NATIVE_AUDIT_PATH')
            if audit and urlsplit(self.path).path!='/health':
                event={'timestamp':dt.datetime.now(dt.timezone.utc).isoformat(),'request':self.path,'status':status,
                       'record_refs':[r.get('record_ref') for r in payload.get('items',[]) if r.get('record_ref')],
                       'total':payload.get('total')}
                with audit_lock:
                    with open(audit,'a') as f:f.write(json.dumps(event)+'\n')
            raw=json.dumps(wire_encode(payload),ensure_ascii=False,allow_nan=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            url=urlsplit(self.path)
            try:
                if url.path=='/health':return self.send(200,{'ready':True})
                if url.path=='/v1/catalog':
                    return self.send(200,{'endpoints':ROUTES,'filters':['entity_id','start','end','signal','trace_id','record_ref','q','offset','limit'],
                      'time_semantics':'inclusive observed_at, RFC3339 timezone required or Unix seconds',
                      'metrics':'Use /v1/metrics/index for value-free identity and evidence references. Query all metric values via real PromQL /api/v1/query or /api/v1/query_range. Non-identity observations have __recorded_STAT suffix and source_stat label; these are retained source observations, not reconstructed raw samples. Query at original observed_at; Prometheus evaluation timestamps are not necessarily original sample timestamps.',
                      'pagination':'total and next_offset are exact; follow next_offset until null',
                      'implementation':'benchmark replay API, not Kubernetes API emulation'})
                if url.path in ['/api/v1/query','/api/v1/query_range','/api/v1/label/__name__/values','/api/v1/series','/api/v1/metadata']:
                    try:
                        with urlopen(prometheus+self.path,timeout=30) as r:data=json.load(r)
                    except HTTPError as e:return self.send(e.code,json.loads(e.read()))
                    return self.send(200,data)
                params=parse_qs(url.query,keep_blank_values=True)
                if any(len(v)!=1 for v in params.values()):raise ValueError('duplicate query parameter')
                data=query(database,url.path,{k:v[0] for k,v in params.items()})
                return self.send(200,data)
            except KeyError:return self.send(404,{'error':'unknown endpoint'})
            except (ValueError,TypeError):return self.send(400,{'error':'invalid query; check catalog'})
            except Exception:return self.send(502,{'error':'backend unavailable'})
    return Handler

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build');p.add_argument('--database',default='/data/records.sqlite');p.add_argument('--port',type=int,default=8080);a=p.parse_args()
    if a.build: print(json.dumps(build(a.build,a.database)))
    else:ThreadingHTTPServer(('0.0.0.0',a.port),handler(a.database,os.getenv('PROMETHEUS_UPSTREAM','http://prometheus:9090'))).serve_forever()
