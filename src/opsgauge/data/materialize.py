"""Materialize a packaged Harbor task from the content-addressed evidence store."""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import os
import shutil

from ..paths import project_root
ROOT=project_root()


def restore(owner, destination, writable=False):
    destination=Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Destination must be absent or empty; original files are never overwritten')
    selected=[]
    with gzip.open(ROOT/'data/file-index.jsonl.gz','rt') as stream:
        for line in stream:
            row=json.loads(line)
            if row['owner']==owner:selected.append(row)
    if not selected:raise ValueError('Unknown owner/task ID: '+owner)
    destination.mkdir(exist_ok=True,parents=True)
    for row in selected:
        rel=Path(row['path'])
        if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe manifest path')
        source=ROOT/'data/objects'/row['sha256'][:2]/row['sha256']
        if not source.is_file() or source.stat().st_size!=row['size']:
            raise FileNotFoundError('Missing or incorrect evidence object: '+row['sha256'])
        target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True)
        if writable:
            shutil.copyfile(source,target);target.chmod(row['mode'])
        else:
            try:os.link(source,target)
            except OSError:shutil.copyfile(source,target)
            target.chmod(0o444|(row['mode']&0o111))
    return len(selected)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id',help='Use --list to inspect available IDs')
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--all',action='store_true',help='Restore selected tasks in one pass through the file index')
    parser.add_argument('--task',choices=['DR','AD','HA','RCA'])
    parser.add_argument('--profile',choices=['shell_raw','native_raw','umodel_semantic'])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--writable',action='store_true',help='Copy rather than hard-link immutable files')
    args=parser.parse_args()
    tasks=[json.loads(x) for x in (ROOT/'data/tasks.jsonl').read_text().splitlines()]
    if args.list:
        for row in tasks:
            if args.task and row['task']!=args.task:continue
            if args.profile and row['profile']!=args.profile:continue
            print(row['task_id'])
        return
    if args.all:
        if not args.output:parser.error('--all requires --output')
        chosen={t['task_id']:t for t in tasks if (not args.task or t['task']==args.task)
                and (not args.profile or t['profile']==args.profile)}
        if args.output.exists() and any(args.output.iterdir()):parser.error('Output must be absent or empty')
        args.output.mkdir(exist_ok=True,parents=True)
        count=0
        with gzip.open(ROOT/'data/file-index.jsonl.gz','rt') as f:
            for line in f:
                row=json.loads(line)
                if row['owner'] not in chosen:continue
                rel=Path(row['path'])
                if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe manifest path')
                task=chosen[row['owner']]
                source=ROOT/'data/objects'/row['sha256'][:2]/row['sha256']
                if not source.exists() or source.stat().st_size!=row['size']:
                    raise FileNotFoundError(row['sha256'])
                target=args.output/task['task']/task['profile']/task['task_id']/rel
                target.parent.mkdir(exist_ok=True,parents=True)
                if args.writable:shutil.copyfile(source,target);target.chmod(row['mode'])
                else:
                    try:os.link(source,target)
                    except OSError:shutil.copyfile(source,target)
                    target.chmod(0o444|(row['mode']&0o111))
                count+=1
        print(json.dumps({'tasks':len(chosen),'restored_files':count,'output':str(args.output)}))
        return
    if not args.task_id or not args.output:parser.error('--task-id and --output are required')
    if args.task_id not in {x['task_id'] for x in tasks}:parser.error('Unknown task ID')
    count=restore(args.task_id,args.output,args.writable)
    print(json.dumps({'task_id':args.task_id,'restored_files':count,'output':str(args.output)}))


if __name__=='__main__':main()
