"""Static checks of frozen Compose build contexts; does not execute Docker."""
from pathlib import Path, PurePosixPath
import collections,gzip,json,posixpath
import yaml

from ..paths import project_root
ROOT=project_root()

def main():
 tasks={r['task_id']:r for r in map(json.loads,(ROOT/'data/tasks.jsonl').read_text().splitlines())}
 files=collections.defaultdict(dict)
 with gzip.open(ROOT/'data/file-index.jsonl.gz','rt') as f:
  for line in f:
   r=json.loads(line)
   if r['owner'] in tasks:files[r['owner']][r['path']]=r
 errors=[];warnings=[];images=collections.Counter();count=0
 for task_id,rows in files.items():
  compose=next((p for p in ['environment/docker-compose.yaml','environment/docker-compose.yml'] if p in rows),None)
  if not compose:errors.append({'task_id':task_id,'reason':'no Compose definition'});continue
  item=rows[compose];obj=ROOT/'data/objects'/item['sha256'][:2]/item['sha256']
  definition=yaml.safe_load(obj.read_text());services=definition.get('services',{})
  if not services:errors.append({'task_id':task_id,'reason':'no Compose services'})
  for name,service in services.items():
   if service.get('image'):images[service['image']]+=1
   build=service.get('build')
   if build:
    context=build if isinstance(build,str) else build.get('context','.')
    dockerfile='Dockerfile' if isinstance(build,str) else build.get('dockerfile','Dockerfile')
    if '$' in context or str(context).startswith('/'):
     warnings.append({'task_id':task_id,'service':name,'reason':'nonrelative or variable build context'});continue
    target=posixpath.normpath(str(PurePosixPath(compose).parent/context/dockerfile))
    if target not in rows:errors.append({'task_id':task_id,'service':name,'missing_dockerfile':target})
  count+=1
 result={'passed':not errors,'compose_definitions_checked':count,'errors':errors,'warnings':warnings,'service_images':dict(images),'docker_executed':False,'scope':'YAML syntax, service definitions, and referenced build Dockerfiles in the collected task tree. Not an execution/availability guarantee.'}
 out=ROOT/'tests/reports/environment-static-check.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
 if errors:raise SystemExit(1)

if __name__=='__main__':main()
