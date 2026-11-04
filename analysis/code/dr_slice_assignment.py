"""Apply the user-authorized CPU-phase slice merge; preserve source scores."""
from pathlib import Path
import json,hashlib
PATH=Path(__file__).resolve().parent.parent/'real-data/dr-slice-classification-v6.json'
def apply_dr_slices(data):
    spec=json.loads(PATH.read_text())
    for r in data['rows']:
        if r['task']!='DR': continue
        r.setdefault('source_slice',r['slice']);r.setdefault('source_slice_name',r['slice_name'])
        if r['slice'] in spec['merge_from']:r['slice']=spec['merge_to']
        r['slice_name']=spec['display_labels'][r['slice']]
    data['dr_classification_version']=spec['version']
    data['sources'][str(PATH)]=hashlib.sha256(PATH.read_bytes()).hexdigest()
    return data
