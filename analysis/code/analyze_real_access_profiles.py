"""Describe access differences after exact case/configuration/level matching."""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'real-data/canonical-results.json'
PROFILES = ('shell_raw', 'native_raw', 'umodel_semantic')
TASKS = ('DR', 'AD', 'HA', 'RCA')
CONFIGS = ('codex-qwen38', 'claude-qwen38', 'codex-glm53', 'claude-glm53')
EXPECTED = dict(DR=222, AD=150, HA=531, RCA=384)


def main():
    data = json.loads(SOURCE.read_text())
    assert data['synthetic'] is False
    pairs = defaultdict(dict)
    for row in data['rows']:
        key = (row['task'], row['config'], row['family'], row['level'])
        assert row['profile'] not in pairs[key], ('duplicate', key)
        assert row['score'] is not None and 0 <= row['score'] <= 100
        pairs[key][row['profile']] = row
    assert len(pairs) == 5148
    for key, rows in pairs.items():
        assert set(rows) == set(PROFILES), ('incomplete', key)
        assert len({r['case_id'] for r in rows.values()}) == 1
        assert len({r['slice'] for r in rows.values()}) == 1
    result = []
    for task, config in product(TASKS, CONFIGS):
        group = [(k, v) for k, v in pairs.items() if k[:2] == (task, config)]
        assert len(group) == EXPECTED[task]
        assert Counter(k[3] for k, v in group) == {1: EXPECTED[task] // 3,
                                                  2: EXPECTED[task] // 3,
                                                  3: EXPECTED[task] // 3}
        scores = {p: mean(v[p]['score'] for k, v in group) for p in PROFILES}
        for profile in PROFILES[1:]:
            delta = [v[profile]['score'] - v['shell_raw']['score'] for k, v in group]
            level_deltas = {
                str(level): mean(v[profile]['score'] - v['shell_raw']['score']
                                 for k, v in group if k[3] == level)
                for level in (1, 2, 3)
            }
            result.append(dict(task=task, config=config, profile=profile,
                               n=len(group), file_mean=scores['shell_raw'],
                               profile_mean=scores[profile], delta=mean(delta),
                               improved=sum(x > 1e-8 for x in delta),
                               tied=sum(abs(x) <= 1e-8 for x in delta),
                               declined=sum(x < -1e-8 for x in delta),
                               delta_by_level=level_deltas))
    out = dict(source=SOURCE.relative_to(ROOT).as_posix(), source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
               matched_case_configuration_level_groups=len(pairs),
               cases_per_task_per_configuration=EXPECTED,
               AD_complete_cases_per_profile_configuration=150,
               matching_checks='identical case ID, family, paper information level, slice',
               uncertainty='descriptive comparisons; no significance or causal claims',
               results=result)
    target = ROOT / 'real-data/access-profile-paired-analysis.json'
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    flat = [{k: v for k, v in r.items() if k != 'delta_by_level'} |
            {f'delta_L{level}': value for level, value in r['delta_by_level'].items()}
            for r in result]
    with target.with_suffix('.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    print(f'Validated {len(pairs):,} matched groups; wrote {len(result)} paired comparisons.')
    for level in (1, 2, 3):
        values = [r['delta_by_level'][str(level)] for r in result
                  if r['task'] == 'HA' and r['profile'] == 'umodel_semantic']
        print(f'HA Semantic-query minus File-based, L{level}: {mean(values):.8f}')


if __name__ == '__main__':
    main()
