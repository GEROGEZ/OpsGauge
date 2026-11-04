"""Selected actual slice scores: three slices per task in a single-column 2x2 figure."""
from pathlib import Path
import json,shutil
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath
R=Path(__file__).resolve().parent.parent;D=R/'real-data';s=pd.read_csv(D/'slices.csv');f=pd.read_csv(D/'family-means.csv');names=json.loads((D/'paper-slice-names.json').read_text())['labels']
selected={'DR':['metric.retrieve-cpu-peaks','event.aggregate-cross-rule-state','trace.rank-failure-volume-rate'],'AD':['L3-S1','L3-S3','L3-S4'],'HA':['mission.saturation','mission.state_flow','mission.multi_state_change'],'RCA':['rca.capacity_load','rca.service_execution','rca.concurrent_causes']}
titles={'DR':'Data Retrieval','AD':'Anomaly Detection','HA':'Health Assessment','RCA':'Root-Cause Analysis'}
CS=['Qwen-Codex','Qwen-Claude','GLM-Codex','GLM-Claude'];COL=['#A9C8B8','#CBDDD3','#B4BCD3','#D1D5E4'];EDGE=['#7CA48F','#7CA48F','#919CB9','#919CB9'];HATCH=['','///','','///'];LEG=['Qwen3.8-Max / Codex','Qwen3.8-Max / Claude Code','GLM-5.3 / Codex','GLM-5.3 / Claude Code']
plt.rcParams.update({'font.family':'STIXGeneral','mathtext.fontset':'stix','pdf.fonttype':42,'svg.fonttype':'none','font.size':7.3,'hatch.linewidth':.32,'savefig.dpi':360})
fp=FontProperties(family='STIXGeneral',size=7.5)
def wrap(t):
 lines=[];line=''
 for word in t.split(' '):
  candidate=(line+' '+word).strip()
  if line and TextPath((0,0),candidate,prop=fp).get_extents().width>54:lines.append(line);line=word
  else:line=candidate
 lines.append(line);return '\n'.join(lines)
W=3.6;H=3.65;fig=plt.figure(figsize=(W,H),facecolor='white');export=[];artists=[]
for i,(task,ids) in enumerate(selected.items()):
 row=i//2;col=i%2;bottom=2.04 if row==0 else .43
 ax=fig.add_axes([(col*1.81+.86)/W,bottom/H,.80/W,1.16/H]);y=np.arange(3)
 for j,c in enumerate(CS):
  vals=[]
  for sid in ids:
   g=s[(s.task==task)&(s.profile=='File-based')&(s.config==c)&(s['slice']==sid)];assert len(g)==1
   val=float(g.iloc[0]['mean']);check=f[(f.task==task)&(f.profile=='File-based')&(f.config==c)&(f['slice']==sid)].score.mean();assert abs(val-check)<1e-10;vals.append(val)
   export.append({'task':task,'slice':sid,'label':names[sid],'config':c,'mean':val,'families':int(g.iloc[0]['families'])})
  ax.barh(y+(j-1.5)*.18,vals,height=.155,fc=COL[j],ec=EDGE[j],lw=.4,hatch=HATCH[j],zorder=3)
 ax.set_ylim(2.52,-.52);ax.set_xlim(0,103);ax.set_yticks(y,[wrap(names[sid]) for sid in ids],fontsize=7.5)
 ax.tick_params(axis='y',length=0,pad=3);ax.set_xticks([0,50,100]);ax.tick_params(axis='x',length=2,width=.4,labelsize=7.3,pad=2)
 ax.set_xlabel('Mean reward (×100)',fontsize=7.3,labelpad=3)
 ax.grid(axis='x',lw=.4,ls=(0,(1.5,3)),color='#D4DBD7');ax.set_axisbelow(True)
 for side in ['top','right']:ax.spines[side].set_visible(False)
 for side in ['left','bottom']:ax.spines[side].set_linewidth(.45);ax.spines[side].set_color('#849189')
 title=fig.text((col*1.81+.88)/W,(bottom-.31)/H,f'({chr(97+i)}) {titles[task]}',ha='center',va='top',fontsize=7.5,fontweight='bold')
 artists+=ax.get_yticklabels()+ax.get_xticklabels()+[ax.xaxis.label,title]
handles=[Patch(fc=COL[j],ec=EDGE[j],hatch=HATCH[j],lw=.4,label=LEG[j]) for j in range(4)]
leg=fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.51,.994),ncol=2,frameon=False,fontsize=7.3,handlelength=1.6,handletextpad=.4,columnspacing=1,labelspacing=.65,borderaxespad=0)
fig.canvas.draw();ren=fig.canvas.get_renderer()
for a in artists+[leg]:
 b=a.get_window_extent(ren);assert b.x0>=0 and b.x1<=fig.bbox.width+1 and b.y0>=0 and b.y1<=fig.bbox.height+1,(b,fig.bbox)
for ext in ['pdf','svg','png']:
 fig.savefig(R/f'slice-selected-four-tasks.{ext}',bbox_inches=None,pad_inches=0)
 shutil.copyfile(R/f'slice-selected-four-tasks.{ext}',R/f'slice.{ext}')
plt.close(fig);pd.DataFrame(export).to_csv(D/'slice-selected-four-tasks-values.csv',index=False)
print('Saved selected 2x2 figure and updated slice.pdf; 48 means verified against original family-level results.')
