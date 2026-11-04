"""Check case/profile coverage, scoring bindings, and local evidence objects without model calls."""
from pathlib import Path
import argparse
import collections
import gzip
import hashlib
import json

from ..paths import project_root
ROOT=project_root()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--full-hash',action='store_true',help='Reread every large object; transfer already verifies SHA256')
    p.add_argument('--metadata-only',action='store_true',help='Check indexes, scores and scorer bindings without restored evidence')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.metadata_only and a.full_hash:p.error('--metadata-only and --full-hash cannot be combined')
    tasks=[json.loads(x) for x in (ROOT/'data/tasks.jsonl').read_text().splitlines()]
    rows=json.loads((ROOT/'results/selected-results.json').read_text())['rows']
    checks={}
    checks['task_profile_packages_3861']=len(tasks)==len({r['task_id'] for r in tasks})==3861
    expected={'DR':666,'AD':450,'HA':1593,'RCA':1152}
    checks['packages_by_task']=dict(collections.Counter(r['task'] for r in tasks))==expected
    manifest=json.loads((ROOT/'data/manifest.json').read_text())
    checks['release_manifest_counts']=(manifest.get('name')=='OpsGauge' and manifest.get('task_profile_counts')==expected
        and manifest.get('cases')==1287 and manifest.get('selected_scores')==len(rows)
        and manifest.get('task_profile_packages')==len(tasks))
    checks['scored_cells_15444']=len(rows)==len({(r['task'],r['config'],r['profile'],r['family'],r['level']) for r in rows})==15444
    checks['all_scores_present']=all(r['score'] is not None and 0<=r['score']<=100 for r in rows)
    index={(r['task'],r['source_case_id'],r['level'],r['profile']):r for r in tasks}
    checks['all_scores_bind_to_package']=all((r['task'],r['case_id'],r['level'],r['profile']) in index for r in rows)
    checks['AD_level_direction']=all(r['level']==4-int(r['source_level'][-1]) for r in tasks if r['task']=='AD')
    grouped=collections.defaultdict(set)
    for row in tasks:grouped[(row['task'],row['source_case_id'],row['level'])].add(row['profile'])
    checks['three_profiles_per_case']=len(grouped)==1287 and all(v=={'shell_raw','native_raw','umodel_semantic'} for v in grouped.values())
    objects=json.loads((ROOT/'data/objects.json').read_text())
    checks['manifest_object_totals']=(manifest.get('objects')==len(objects)
        and manifest.get('object_bytes')==sum(v['size'] for v in objects.values()))
    missing=[];wrong=[];checked_bytes=0
    for key,info in objects.items():
        if a.metadata_only:continue
        path=ROOT/'data/objects'/key[:2]/key
        if not path.is_file():missing.append(key);continue
        if path.stat().st_size!=info['size']:wrong.append(key);continue
        if a.full_hash:
            h=hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
            if h.hexdigest()!=key:wrong.append(key)
            checked_bytes+=info['size']
    if not a.metadata_only:
        checks['all_data_objects_present']=not missing
        checks['object_sizes_and_requested_hashes']=not wrong
    bindings=json.loads((ROOT/'data/provenance/frozen-code-bindings.json').read_text())['files']
    checks['scorer_source_hashes']=all((ROOT/r['path']).is_file() and hashlib.sha256((ROOT/r['path']).read_bytes()).hexdigest()==r['source_sha256'] for r in bindings)
    shards=json.loads((ROOT/'data/shards.json').read_text())
    shard_objects=[key for shard in shards['shards'] for key in shard['objects']]
    checks['shard_object_coverage']=len(shard_objects)==len(set(shard_objects))==len(objects) and set(shard_objects)==set(objects)
    counts=collections.Counter();small_wrong=[]
    with gzip.open(ROOT/'data/file-index.jsonl.gz','rt') as f:
        for line in f:
            row=json.loads(line);counts[row['owner']]+=1
            if row['sha256'] not in objects:small_wrong.append(row['owner']+'/'+row['path'])
    checks['all_task_files_indexed']=all(counts[t['task_id']]>0 for t in tasks) and not small_wrong
    report={'passed':all(checks.values()),'checks':checks,'task_profile_packages':len(tasks),
            'selected_scores':len(rows),'object_count':len(objects),'object_bytes':sum(x['size'] for x in objects.values()),
            'missing_objects':missing,'invalid_objects':wrong,'full_hash_requested':a.full_hash,'metadata_only':a.metadata_only,
            'bytes_rehashed':checked_bytes,'agent_runs':0,'model_calls':0,
            'scope':'Index, score and source-code checks; evidence objects are checked only outside metadata-only mode. No Docker or agent execution.'}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
