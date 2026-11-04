"""Locate an explicit benchmark checkout without searching user directories."""
from pathlib import Path
import os

def project_root():
    selected=os.environ.get('OPSGAUGE_HOME')
    candidates=[Path(selected)] if selected else [Path.cwd(),Path(__file__).resolve().parents[2]]
    for root in candidates:
        if (root/'data/manifest.json').is_file():return root.resolve()
    raise RuntimeError('Set OPSGAUGE_HOME or pass --data-root with the OpsGauge checkout path')
