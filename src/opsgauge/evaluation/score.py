"""Call frozen task scorers through a path-only local interface; no LLM judging."""
from pathlib import Path
import argparse
import importlib.util
import json
import math
import sys
import tempfile
from ..data.materialize import ROOT, restore
PACKAGE=Path(__file__).resolve().parents[1]


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task-id',required=True)
    p.add_argument('--task-dir',type=Path,help='Previously materialized exact task')
    p.add_argument('--answer',type=Path)
    p.add_argument('--progress',type=Path)
    p.add_argument('--query-trace',type=Path)
    p.add_argument('--source-only',action='store_true',help='Explicitly grade without query-derived records')
    p.add_argument('--trial',type=Path,help='Original trial directory; required for DR process scoring')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Output must be a new file; existing scores are never overwritten')
    tasks={r['task_id']:r for r in map(json.loads,(ROOT/'data/tasks.jsonl').read_text().splitlines())}
    if a.task_id not in tasks:p.error('Unknown task ID')
    info=tasks[a.task_id];kind=info['task']
    for name in ['answer','progress','query_trace']:
        value=getattr(a,name)
        if value is not None and not value.is_file():p.error(name+' does not exist')
    if a.trial and not (a.trial/'result.json').is_file():p.error('Trial is missing result.json')
    if a.task_dir and not a.task_dir.is_dir():p.error('Task directory does not exist')
    with tempfile.TemporaryDirectory(prefix='opsgauge-score-') as tmp:
        task=a.task_dir
        if task is None:
            task=Path(tmp)/'task';restore(a.task_id,task)
        if kind=='DR':
            if not a.trial:p.error('DR requires --trial: process credit cannot be reconstructed from the final answer alone')
            code=PACKAGE/'scorers/dr';sys.path.insert(0,str(code))
            scorer=module(code/'rescore_factorial_v2.py','opsgauge_frozen_dr')
            contract=json.loads((task/'tests/private/contract.json').read_text())
            result=scorer.score_trial(a.trial,contract,Path(tmp)/'scoring-work')
        elif kind=='AD':
            if a.trial:
                scorer=module(PACKAGE/'scorers/ad/score_adapter.py','opsgauge_frozen_ad_adapter')
                result=scorer.collect_and_score(a.trial,task,task/'tests/scorer.py',Path(tmp)/'ad-score.json')
            else:
                if not a.answer:p.error('AD requires --answer, or --trial for original execution-aware scoring')
                scorer=module(task/'tests/scorer.py','opsgauge_frozen_ad')
                answer=json.loads(a.answer.read_text())
                progress=json.loads(a.progress.read_text()) if a.progress else None
                result=scorer.score(task/'tests/case',answer,progress)
        else:
            if not a.answer:p.error(kind+' requires --answer')
            if not a.query_trace and not a.source_only:
                p.error('Provide --query-trace, or explicitly choose --source-only; missing query evidence is never silently filled')
            name='verifier_v1.py' if kind=='HA' else 'verifier_v2.py'
            scorer=module(PACKAGE/'scorers/health_and_rca'/name,'opsgauge_frozen_'+kind.lower())
            envelope=json.loads((task/'tests/ground_truth.json').read_text())
            answer=json.loads(a.answer.read_text())
            events=[json.loads(x) for x in a.query_trace.read_text().splitlines() if x.strip()] if a.query_trace else []
            records=scorer._load_scene_records(task/'tests/scene-records.jsonl',envelope,answer,events)
            try:
                result=scorer.score(envelope,answer,records,events)
            except (KeyError,TypeError,ValueError) as exc:
                result={'reward':0.0,'protocol_valid':0.0,'error':str(exc),'dimensions':{}}
        if kind=='DR':
            raw_score=result['aggregate_score'];scale=100.0
        elif kind=='AD':
            raw_score=next((result[k] for k in ['score','reward','composite_score','total_score'] if result.get(k) is not None),None);scale=100.0
        else:
            raw_score=result['reward'];scale=1.0
        unscored=kind=='AD' and raw_score is None and result.get('assessment') in {'agent_not_started','scoring_failed'}
        if not unscored and (isinstance(raw_score,bool) or not isinstance(raw_score,(int,float)) or not math.isfinite(raw_score) or not 0<=raw_score<=scale):
            raise ValueError('Scorer did not return a finite reward in its documented range')
        reward=None if unscored else raw_score/scale
        result={'task_id':a.task_id,'task':kind,'source_profile':info['profile'],
                'scored':not unscored,'reward':reward,'score':None if unscored else reward*100.0,
                'agent_runs':0,'model_calls':0,'scorer_output':result,
                'scope':'Grades the supplied answer and process/query inputs under the selected task contract.'}
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(a.output)


if __name__=='__main__':main()
