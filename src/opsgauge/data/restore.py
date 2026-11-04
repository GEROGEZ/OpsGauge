"""Verify Git LFS data shards and restore the complete sanitized content-addressed store."""
from pathlib import Path
import argparse,hashlib,json,tarfile,re,os
from ..paths import project_root
ROOT=project_root()

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verify-only',action='store_true',help='Read/hash every archive and object without restoring files');p.add_argument('--report',type=Path);a=p.parse_args()
 manifest=json.loads((ROOT/'data/shards.json').read_text());objects=json.loads((ROOT/'data/objects.json').read_text());assigned=[h for x in manifest['shards'] for h in x['objects']]
 if len(assigned)!=len(set(assigned)) or set(assigned)!=set(objects):raise ValueError('Shard manifest must cover every indexed object exactly once')
 checked=set();total=0
 for i,shard in enumerate(manifest['shards'],1):
  name=shard['file']
  if not re.fullmatch(r'objects-\d+\.tar\.gz',name):raise ValueError('Unsafe archive filename')
  archive=ROOT/'data/shards'/name
  if not archive.is_file() or archive.stat().st_size!=shard['bytes']:raise ValueError(name+': missing archive or Git LFS pointer; run git lfs pull first')
  if digest(archive)!=shard['sha256']:raise ValueError(name+': archive SHA256 mismatch')
  expected=set(shard['objects']);seen=set()
  with tarfile.open(archive,'r|gz') as tar:
   for member in tar:
    parts=member.name.split('/');h=parts[-1]
    if not member.isfile() or len(parts)!=2 or not re.fullmatch(r'[a-f0-9]{64}',h) or parts[0]!=h[:2] or h not in expected or h in seen:raise ValueError('Unexpected or unsafe archive member')
    if member.size!=objects[h]['size']:raise ValueError('Object size mismatch')
    target=ROOT/'data/objects'/h[:2]/h;temp=target.with_name(h+'.part');stream=tar.extractfile(member);ha=hashlib.sha256();writer=None
    if not a.verify_only:
     target.parent.mkdir(parents=True,exist_ok=True)
     if target.exists():
      if target.stat().st_size!=member.size or digest(target)!=h:raise ValueError('Existing object differs; refusing to overwrite')
     else:writer=temp.open('xb')
    try:
     for b in iter(lambda:stream.read(4*1024*1024),b''):
      ha.update(b)
      if writer:writer.write(b)
    finally:
     if writer:writer.close()
    if ha.hexdigest()!=h:
     if writer:temp.unlink(missing_ok=True)
     raise ValueError('Object SHA256 mismatch')
    if writer:temp.chmod(0o444);os.replace(temp,target)
    seen.add(h);checked.add(h);total+=member.size
  if seen!=expected:raise ValueError('Incomplete archive members')
  print(json.dumps({'shards_checked':i,'total_shards':len(manifest['shards']),'objects_checked':len(checked)}),flush=True)
 result={'passed':checked==set(objects),'shards_checked':len(manifest['shards']),'objects_checked':len(checked),'object_bytes_verified':total,'archive_bytes':manifest['archive_bytes'],'verify_only':a.verify_only,'scope':'Archive and every member SHA256 plus exact object-index coverage; no Docker or agent execution.'}
 if a.report:a.report.parent.mkdir(exist_ok=True,parents=True);a.report.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result),flush=True)
if __name__=='__main__':main()
