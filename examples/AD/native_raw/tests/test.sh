#!/bin/sh
set -eu
mkdir -p /logs/verifier
python3 - <<'PY2'
import json,pathlib,sys
sys.path.insert(0,'/tests');from scorer import score
ap=pathlib.Path('/logs/agent/answer.json');pp=pathlib.Path('/logs/agent/progress.json')
a=json.loads(ap.read_text()) if ap.exists() else {};p=json.loads(pp.read_text()) if pp.exists() else None
r=score(pathlib.Path('/tests/case'),a,p)
pathlib.Path('/logs/verifier/details.json').write_text(json.dumps(r,indent=2)+'\n')
pathlib.Path('/logs/verifier/reward.json').write_text(json.dumps({'reward':r['composite_score']/100})+'\n')
PY2
