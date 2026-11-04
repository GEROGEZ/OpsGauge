"""Summarize original slice labels; level-balanced means and paired composition contrasts."""
from pathlib import Path
import json,itertools
import pandas as pd
import numpy as np
R=Path(__file__).resolve().parent.parent/'real-data';d=json.loads((R/'canonical-results.json').read_text());df=pd.DataFrame(d['rows'])
C={'codex-qwen38':'Qwen-Codex','claude-qwen38':'Qwen-Claude','codex-glm53':'GLM-Codex','claude-glm53':'GLM-Claude'}
P={'shell_raw':'File-based','native_raw':'Native-API','umodel_semantic':'Semantic-query'}
df['config']=df.config.map(C);df['profile']=df.profile.map(P)
assert (df.groupby(['task','config','profile','family'])['level'].nunique()==3).all()
assert (df.groupby(['task','family'])['slice'].nunique()==1).all()
fam=df.groupby(['task','config','profile','family','cluster','slice','slice_name'],as_index=False).score.mean()
sl=fam.groupby(['task','profile','config','slice','slice_name'],as_index=False).agg(mean=('score','mean'),families=('family','nunique'))
sl['n']=sl.families*3
fam.to_csv(R/'family-means.csv',index=False);sl.to_csv(R/'slices.csv',index=False)
counts=fam[fam.config=='Qwen-Codex'].groupby(['task','profile','slice']).size();assert counts.groupby(['task','slice']).nunique().eq(1).all()
res=[]
# Paired descriptive contrasts only; no inferential claims or confidence intervals.
for (task,profile),g in fam.groupby(['task','profile']):
 pivot=g.pivot(index=['family','cluster','slice'],columns='config',values='score');assert not pivot.isna().any().any()
 for a,b in itertools.combinations(C.values(),2):
  dd=(pivot[b]-pivot[a]).reset_index(name='delta');sm=dd.groupby('slice').delta.mean()
  res.append(dict(task=task,profile=profile,a=a,b=b,released=dd.delta.mean(),uniform=sm.mean(),shift=sm.mean()-dd.delta.mean(),families=len(dd),clusters=dd.cluster.nunique(),slice_min=sm.min(),slice_max=sm.max(),slice_deltas=sm.to_dict()))
(R/'composition-analysis.json').write_text(json.dumps({'method':'paired descriptive mean contrasts; uniform gives each observed slice equal weight; levels equally weighted within families; no confidence intervals or significance claims','results':res},indent=2))
print('Slice counts',sl[sl.profile=='File-based'].groupby('task')['slice'].nunique().to_dict())
for r in res:
 if r['profile']=='File-based':print(r['task'],r['a'],r['b'],'released',round(r['released'],2),'uniform',round(r['uniform'],2),'slice_range',round(r['slice_min'],2),round(r['slice_max'],2))
