"""Compact slice-ID pie panels; counts and order match the appendix table."""
from pathlib import Path
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
R=Path(__file__).resolve().parent.parent;D=R/'real-data'
m=json.loads((D/'plot-labels.json').read_text());names=json.loads((D/'paper-slice-names.json').read_text())['labels']
df=pd.read_csv(D/'family-means.csv');assert df.groupby(['task','family'])['slice'].nunique().eq(1).all()
c=df.drop_duplicates(['task','family']).groupby(['task','slice']).size()
plt.rcParams.update({'font.family':'STIXGeneral','pdf.fonttype':42,'svg.fonttype':'none','font.size':6.8,'savefig.dpi':360})
COL=['#A4C7B9','#E8B382','#B4A9D0','#9EBACB','#C8D8A6','#D7B5C7','#D7CCAA','#A9C4D8']
tasks=['DR','AD','HA','RCA'];titles=['Data Retrieval','Anomaly Detection','Health Assessment','Root-Cause Analysis']
# Verify IDs against the actual table's per-task name definitions.
table=(R/'appendix-slice-table-preview/table.tex').read_text()
for task in tasks:
 for j,sl in enumerate(m['orders'][task],1):
  assert f"S{j}: {names[sl]}" in table, (task,j,sl)
def legend(ax,task):
 order=m['orders'][task]
 labels=[f"S{j+1} ({int(c.loc[(task,sl)])})" for j,sl in enumerate(order)]
 return ax.legend([Patch(fc=COL[j],ec='none') for j in range(len(order))],labels,loc='center left',frameon=False,fontsize=8.2,handlelength=.65,handleheight=.75,handletextpad=.4,labelspacing=.22,borderaxespad=0,borderpad=0)
# Measure legends before layout, so all approved names fit without reducing font size.
probe=plt.figure(figsize=(3.5,8));heights=[]
for i,t in enumerate(tasks):
 ax=probe.add_axes([0,.1,.245,.8]);ax.axis('off');l=legend(ax,t);probe.canvas.draw();heights.append(l.get_window_extent().height/probe.dpi);ax.remove()
plt.close(probe)
rowheights=[max(1.04,max(heights[:2]))+.24,max(1.04,max(heights[2:]))+.24];W=3.5;H=sum(rowheights)+.16
fig=plt.figure(figsize=(W,H),facecolor='white');legs=[];export=[]
top=H-.08
for i,task in enumerate(tasks):
 row=i//2;rh=rowheights[row];rowtop=top if row==0 else top-rowheights[0]
 center=rowtop-(rh-.23)/2-.015
 x=.002+(i%2)*.505
 ax=fig.add_axes([x,(center-.515)/H,.294,1.03/H])
 order=m['orders'][task];vals=[int(c.loc[(task,s)]) for s in order]
 ax.pie(vals,colors=COL[:len(vals)],startangle=90,counterclock=False,radius=1.23,wedgeprops={'linewidth':.42,'edgecolor':'white'},autopct=lambda p:f'{p:.0f}%' if p>=3 else '',pctdistance=.72,textprops={'fontsize':7.3,'color':'#354344'});ax.set_aspect('equal')
 lax=fig.add_axes([x+.303,(center-.5)/H,.185,1/H]);lax.axis('off');legs.append(legend(lax,task))
 fig.text(x+.24,(rowtop-rh+.13)/H,f'({chr(97+i)}) {titles[i]}',ha='center',va='top',fontsize=8.0,fontweight='bold')
 export.extend({'task':task,'slice_id':f'S{j+1}','slice':s,'label':names[s],'families':v,'percent':100*v/sum(vals)} for j,(s,v) in enumerate(zip(order,vals)))
fig.canvas.draw()
for l in legs:
 b=l.get_window_extent();assert b.x0>=0 and b.x1<=fig.bbox.width+1 and b.y0>=0 and b.y1<=fig.bbox.height+1,(b,fig.bbox)
for ext in ['pdf','svg','png']:fig.savefig(R/f'distribution.{ext}',bbox_inches=None,pad_inches=0)
plt.close(fig)
pd.DataFrame(export).to_csv(D/'slice-distribution.csv',index=False)
print('Saved',R/'distribution.pdf','size',W,H)
print(pd.DataFrame(export).groupby('task').families.agg(['size','sum']).to_string())
