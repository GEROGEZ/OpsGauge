"""Same deterministic peak-pair result score; process score runs after trial."""
import json
from pathlib import Path
from family_runtime import result_score
contract=json.loads(Path('/tests/private/contract.json').read_text())
try:answer=json.loads(Path('/app/answer.json').read_text())
except (OSError,ValueError):answer=None
report=result_score(answer,contract)
dest=Path('/logs/verifier');dest.mkdir(parents=True,exist_ok=True)
(dest/'details.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(dest/'reward.txt').write_text(str(report['score']/100))
