"""Validate release image and example startup, without invoking any model or agent."""
from pathlib import Path
import argparse,datetime,hashlib,json,subprocess,uuid
p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--plan-only',action='store_true');a=p.parse_args();root=a.release_root.resolve();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
settings=json.loads((root/'configs/custom-base-image-inspection.json').read_text());archive=root/'data/images/replay-base.docker.tar.gz';examples=[]
for task in ['DR','AD','HA','RCA']:
 for profile in ['shell_raw','native_raw','umodel_semantic']:
  env=root/'examples'/task/profile/'environment';files=[env/n for n in ['docker-compose.yaml','docker-compose.yml','compose.yaml','compose.yml'] if (env/n).exists()]
  if len(files)!=1:raise RuntimeError('Expected exactly one compose file: '+str(env))
  examples.append((task,profile,files[0]))
plan={'image':settings['image'],'expected_image_id':settings['image_id'],'example_environments':len(examples),'tasks':[{'task':t,'profile':p} for t,p,f in examples],'agent_runs':0,'model_calls':0,'scope':'Image import and Python dependency import; Docker Compose build/start/readiness of each example. Does not establish full agent execution or scoring correctness.'}
(out/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
if a.plan_only:print(json.dumps(plan));raise SystemExit(0)
def run(cmd,log,timeout=900):
 with (out/log).open('w') as f:
  x=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 if x.returncode:raise RuntimeError('Command failed; see '+log)
run(['docker','info','--format','{{.OSType}}/{{.Architecture}}'],'docker-info.log',30)
run(['docker','compose','version'],'compose-version.log',30)
h=hashlib.sha256()
with archive.open('rb') as f:
 for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
if h.hexdigest()!=settings['archive_sha256']:raise RuntimeError('Base image archive SHA256 mismatch')
run(['docker','load','-i',str(archive)],'image-load.log')
actual=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',settings['image']],text=True).strip()
if actual!=settings['image_id']:raise RuntimeError('Loaded image ID mismatch')
run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp','--entrypoint','python',settings['image'],'-c','import duckdb, fastapi, uvicorn, pydantic, yaml, pytz; from bench_factory.replay.query_engine import ReplayQueryEngine; print("runtime imports OK")'],'image-runtime-import.log',60)
checks=[]
for task,profile,compose in examples:
 project='opsgauge-smoke-'+uuid.uuid4().hex[:12];cmd=['docker','compose','--project-name',project,'--file',str(compose)];name=task+'-'+profile;item={'task':task,'profile':profile,'passed':False}
 try:
  run(cmd+['config','--quiet'],name+'-config.log',30)
  run(cmd+['up','--build','--detach','--wait','--wait-timeout','180'],name+'-startup.log',1800)
  run(cmd+['ps','--all','--format','json'],name+'-status.json',30);item['passed']=True
 except Exception as exc:item['error']=str(exc)
 finally:
  try:run(cmd+['down','--volumes','--remove-orphans'],name+'-cleanup.log',120)
  except Exception as exc:item['cleanup_error']=str(exc);item['passed']=False
 checks.append(item);(out/'progress.json').write_text(json.dumps(checks,indent=2)+'\n')
report={**plan,'passed':all(x['passed'] for x in checks),'image_id_verified':True,'image_runtime_imports_passed':True,'checks':checks,'docker_executed':True,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));raise SystemExit(0 if report['passed'] else 1)
