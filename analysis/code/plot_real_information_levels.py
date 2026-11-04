"""Actual matched-family L1-L3 scores; paper L1 has most information for every task."""
from pathlib import Path
import json,shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
R=Path(__file__).resolve().parent.parent;D=R/'real-data';j=json.loads((D/'canonical-results.json').read_text());d=pd.DataFrame(j['rows']);d=d[d.profile=='shell_raw'].copy()
assert j['ad_level_mapping']=={'IL3':1,'IL2':2,'IL1':3}
assert (d.loc[d.task=='AD','level']==4-d.loc[d.task=='AD','raw_level'].str[-1].astype(int)).all()
keys=['task','config','family','level'];assert not d.duplicated(keys).any()
p=d.pivot(index=['task','config','family'],columns='level',values='score');assert list(p.columns)==[1,2,3] and not p.isna().any().any()
# Equal family sets across levels and configurations within each task.
for task,g in d.groupby('task'):
 reference=None
 for _,h in g.groupby(['config','level']):
  fs=set(h.family)
  if reference is None:reference=fs
  else:assert fs==reference
means=d.groupby(['task','config','level']).score.mean()
CS=['codex-qwen38','claude-qwen38','codex-glm53','claude-glm53'];COL=['#7CA48F','#7CA48F','#919CB9','#919CB9'];MARK=['o','s','o','s'];LINE=['-','--','-','--'];LEG=['Qwen3.8-Max / Codex','Qwen3.8-Max / Claude Code','GLM-5.3 / Codex','GLM-5.3 / Claude Code']
TASKS=['DR','AD','HA','RCA'];TITLES=['Data Retrieval','Anomaly Detection','Health Assessment','Root-Cause Analysis']
plt.rcParams.update({'font.family':'STIXGeneral','mathtext.fontset':'stix','pdf.fonttype':42,'svg.fonttype':'none','font.size':7.7,'savefig.dpi':360})
W=3.6;H=3.35;fig=plt.figure(figsize=(W,H),facecolor='white');artists=[];rows=[]
for i,task in enumerate(TASKS):
 col=i%2;row=i//2;bottom=1.94 if row==0 else .42
 ax=fig.add_axes([(col*1.80+.39)/W,bottom/H,1.24/W,1.04/H])
 for a,c in enumerate(CS):
  values=[float(means.loc[(task,c,l)]) for l in [1,2,3]]
  ax.plot([1,2,3],values,color=COL[a],linestyle=LINE[a],lw=1.05,marker=MARK[a],markersize=3.4,markerfacecolor=COL[a] if a%2==0 else 'white',markeredgecolor=COL[a],markeredgewidth=.8,zorder=4 if a%2 else 3)
  rows.extend({'task':task,'profile':'File-based','config':c,'level':l,'mean':v,'families':int((d[(d.task==task)&(d.config==c)&(d.level==l)]).family.nunique())} for l,v in zip([1,2,3],values))
 ax.set_xlim(.86,3.14);ax.set_xticks([1,2,3],['L1','L2','L3']);ax.set_ylim(40,100);ax.set_yticks([40,60,80,100])
 ax.tick_params(axis='both',labelsize=7.5,length=2,width=.45,pad=2)
 ax.set_xlabel('Information level',fontsize=7.7,labelpad=3);ax.set_ylabel('Mean reward (×100)',fontsize=7.7,labelpad=3)
 ax.grid(color='#DBE0DE',ls=(0,(1.5,3)),lw=.42);ax.set_axisbelow(True)
 for side in ['top','right']:ax.spines[side].set_visible(False)
 for side in ['left','bottom']:ax.spines[side].set_color('#849189');ax.spines[side].set_linewidth(.45)
 t=fig.text((col*1.80+.93)/W,(bottom-.31)/H,f'({chr(97+i)}) {TITLES[i]}',ha='center',va='top',fontsize=7.8,fontweight='bold')
 artists+=ax.get_xticklabels()+ax.get_yticklabels()+[ax.xaxis.label,ax.yaxis.label,t]
handles=[Line2D([],[],color=COL[a],ls=LINE[a],lw=1.05,marker=MARK[a],markersize=3.4,markerfacecolor=COL[a] if a%2==0 else 'white',markeredgewidth=.8,label=LEG[a]) for a in range(4)]
leg=fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.51,.995),ncol=2,frameon=False,fontsize=7.3,handlelength=2,handletextpad=.4,columnspacing=1,labelspacing=.65,borderaxespad=0)
fig.canvas.draw();renderer=fig.canvas.get_renderer()
for t in artists+[leg]:
 b=t.get_window_extent(renderer);assert b.x0>=0 and b.x1<=fig.bbox.width+1 and b.y0>=0 and b.y1<=fig.bbox.height+1,(b,fig.bbox)
assert len(rows)==48
backup=R/'history/demand-demo-before-real-data';backup.mkdir(parents=True,exist_ok=True)
if (R/'demand.pdf').exists() and not (backup/'demand.pdf').exists():shutil.copyfile(R/'demand.pdf',backup/'demand.pdf')
for ext in ['pdf','svg','png']:fig.savefig(R/f'demand.{ext}',bbox_inches=None,pad_inches=0)
plt.close(fig)
pd.DataFrame(rows).to_csv(D/'information-level-means-file-based.csv',index=False)
(D/'information-level-figure-validation.json').write_text(json.dumps({'source':'canonical-results.json','profile':'File-based','scored_cases':len(d),'matched_family_configuration_groups':len(p),'points':48,'AD_raw_to_paper_levels':j['ad_level_mapping'],'all_levels_have_same_families':True,'y_axis_range':[40,100],'y_axis_range_shared_across_tasks':True,'synthetic':False},indent=2)+'\n')
print(R/'demand.pdf');print('48 real task/configuration/level means verified; matched family sets; AD level direction normalized.')
