"""Apply the paper's seven AD operation slices without changing source scores."""
from pathlib import Path
import json,hashlib
PATH=Path(__file__).resolve().parent.parent/'real-data/ad-slice-classification-v5.json'
def apply_ad_slices(data):
    j=json.loads(PATH.read_text());assign={r['family_id']:r['primary_slice'] for r in j['assignments']};names={t['slice_id']:t['name_en'] for t in j['taxonomy']}
    rows=[r for r in data['rows'] if r['task']=='AD']
    assert {r['family'] for r in rows}==set(assign)
    for r in rows:
        r.setdefault('source_slice',r['slice']);r.setdefault('source_slice_name',r['slice_name'])
        r['slice']=assign[r['family']];r['slice_name']=names[r['slice']]
    data.setdefault('source_ad_taxonomy',data['ad_taxonomy'])
    data['ad_taxonomy']=j['taxonomy'];data['ad_classification_version']=j['version']
    data['sources'][str(PATH)]=hashlib.sha256(PATH.read_bytes()).hexdigest()
    return data
