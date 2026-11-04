"""Replay bundled submissions through the public scoring command, without model calls."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
import tempfile

from ..data.materialize import ROOT, restore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    fixtures=ROOT/'tests/fixtures/scoring'
    checks=[]
    with tempfile.TemporaryDirectory(prefix='opsgauge-scoring-check-') as temp:
        work=Path(temp)
        specifications=[
            ('DR','DR--L2-P06--L2--native_raw',ROOT/'examples/DR/native_raw',
             ['--trial',str(fixtures/'dr-original-trial')],0.66),
            ('F020','AD--F020--L3--umodel_semantic',None,
             ['--answer',str(fixtures/'F020/answer.json'),'--progress',str(fixtures/'F020/progress.json')],0.85),
            ('F025','AD--F025--L3--umodel_semantic',None,
             ['--answer',str(fixtures/'F025/answer.json'),'--progress',str(fixtures/'F025/progress.json')],0.85),
            ('HA','HA--case-9f9307b5e17b126b573f--L3--native_raw',ROOT/'examples/HA/native_raw',
             ['--answer',str(fixtures/'ha-original-answer.json'),'--source-only'],0.875),
            ('RCA','RCA--case-fcc6fc7eeb9b447df39f--L3--native_raw',ROOT/'examples/RCA/native_raw',
             ['--answer',str(fixtures/'rca-original-answer.json'),'--source-only'],0.5333333333333333),
        ]
        for name,task_id,task,inputs,expected in specifications:
            if task is None:
                task=work/name
                restore(task_id,task)
            output=work/(name+'-score.json')
            result=subprocess.run([sys.executable,'-m','opsgauge','--data-root',str(ROOT),'score','--task-id',task_id,
                                   '--task-dir',str(task),*inputs,'--output',str(output)],capture_output=True,text=True)
            if result.returncode:
                checks.append({'fixture':name,'passed':False,'error':result.stderr[-1500:]})
                continue
            data=json.loads(output.read_text())
            actual=data['reward']
            checks.append({'fixture':name,'expected_reward':expected,'actual_reward':actual,
                           'passed':abs(actual-expected)<1e-9 and abs(data['score']-expected*100)<1e-7})
    report={'passed':all(x['passed'] for x in checks),'checks':checks,'agent_runs':0,'model_calls':0,
            'scope':'Five bundled submissions scored through the current public command; not a new agent or Docker run.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
