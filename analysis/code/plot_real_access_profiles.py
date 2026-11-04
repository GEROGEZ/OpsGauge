"""Actual matched-case access scores, at journal single-column dimensions."""
from pathlib import Path
import hashlib
import json
import shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.transforms import blended_transform_factory

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'real-data'
source = DATA / 'canonical-results.json'
payload = json.loads(source.read_text())
assert payload['synthetic'] is False
df = pd.DataFrame(payload['rows'])
TASKS = ['DR', 'AD', 'HA', 'RCA']
TITLES = ['Data Retrieval', 'Anomaly Detection', 'Health Assessment', 'Root-Cause Analysis']
CONFIGS = ['codex-qwen38', 'claude-qwen38', 'codex-glm53', 'claude-glm53']
PROFILES = ['shell_raw', 'native_raw', 'umodel_semantic']
PROFILE_NAMES = ['File-based', 'Native-API', 'Semantic-query']
COLORS = ['#EED0AF', '#B7D2C2', '#C3C9DE']
EDGES = ['#BD956F', '#7FA58F', '#929CB9']
HATCHES = ['//', chr(92) * 2, 'xx']
EXPECTED = {'DR': 222, 'AD': 150, 'HA': 531, 'RCA': 384}

# Reject incomplete or differently identified comparisons before aggregating.
keys = ['task', 'config', 'family', 'level']
assert not df.duplicated(keys + ['profile']).any()
matched = df.pivot(index=keys, columns='profile', values='score')
assert len(matched) == 5148 and not matched.isna().any().any()
assert set(matched.columns) == set(PROFILES)
assert (df.groupby(keys).case_id.nunique() == 1).all()
assert (df.groupby(keys).slice.nunique() == 1).all()
assert (df.loc[df.task == 'AD', 'level'] ==
        4 - df.loc[df.task == 'AD', 'raw_level'].str[-1].astype(int)).all()
means = df.groupby(['task', 'config', 'profile']).score.mean()
audit = json.loads((DATA / 'access-profile-paired-analysis.json').read_text())
for row in audit['results']:
    index = (row['task'], row['config'])
    assert abs(means.loc[index + (row['profile'],)] - row['profile_mean']) < 1e-10
    assert abs(means.loc[index + ('shell_raw',)] - row['file_mean']) < 1e-10

plt.rcParams.update({
    'font.family': 'STIXGeneral', 'mathtext.fontset': 'stix',
    'font.size': 7.5, 'pdf.fonttype': 42, 'svg.fonttype': 'none',
    'hatch.linewidth': .30, 'savefig.dpi': 400,
})
W, H = 3.6, 3.70
fig = plt.figure(figsize=(W, H), facecolor='white')
artists, exported = [], []
centers = np.array([0., 1., 2.35, 3.35])
for i, (task, title) in enumerate(zip(TASKS, TITLES)):
    col, row = i % 2, i // 2
    left = .35 if col == 0 else 2.08
    bottom = 2.35 if row == 0 else .68
    ax = fig.add_axes([left / W, bottom / H, 1.37 / W, 1.03 / H])
    for j, profile in enumerate(PROFILES):
        values = []
        for config in CONFIGS:
            group = df[(df.task == task) & (df.config == config) &
                       (df.profile == profile)]
            assert len(group) == EXPECTED[task]
            assert group.groupby('level').size().to_dict() == {
                level: EXPECTED[task] // 3 for level in [1, 2, 3]}
            value = float(means.loc[(task, config, profile)])
            # Directly verify equal level weighting, not only the pooled mean.
            assert abs(value - group.groupby('level').score.mean().mean()) < 1e-10
            values.append(value)
            exported.append(dict(task=task, config=config, profile=PROFILE_NAMES[j],
                                 mean=value, cases=len(group)))
        ax.bar(centers + (j - 1) * .235, values, width=.215,
               facecolor=COLORS[j], edgecolor=EDGES[j], hatch=HATCHES[j],
               linewidth=.42, zorder=3)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xlim(-.56, 3.91)
    ax.set_xticks(centers, ['Codex', 'Claude\nCode', 'Codex', 'Claude\nCode'])
    ax.tick_params(axis='y', labelsize=7.2, length=2, width=.45, pad=2)
    ax.tick_params(axis='x', labelsize=7.2, length=0, pad=4)
    ax.grid(axis='y', color='#D9DFDC', linewidth=.42, linestyle=(0, (1.5, 3)))
    ax.set_axisbelow(True)
    for side in ['top', 'right']:
        ax.spines[side].set_visible(False)
    for side in ['left', 'bottom']:
        ax.spines[side].set_color('#849189')
        ax.spines[side].set_linewidth(.45)
    if col == 0:
        ax.set_ylabel('Mean reward (×100)', fontsize=7.6, labelpad=3)
        artists.append(ax.yaxis.label)
    tr = blended_transform_factory(ax.transData, ax.transAxes)
    for x0, x1, text in [(-.39, 1.39, 'Qwen3.8-Max'),
                         (1.96, 3.74, 'GLM-5.3')]:
        ax.plot([x0, x1], [-.26 / 1.03, -.26 / 1.03], transform=tr,
                color='#A7ADA9', linewidth=.40, clip_on=False)
        label = ax.text((x0 + x1) / 2, -.305 / 1.03, text, transform=tr,
                        ha='center', va='top', fontsize=7.3)
        artists.append(label)
    panel = fig.text((left + .685) / W, (bottom - .505) / H,
                     f'({chr(97 + i)}) {title}', ha='center', va='top',
                     fontsize=7.8, fontweight='bold')
    artists.extend(ax.get_xticklabels() + ax.get_yticklabels() + [panel])

legend = fig.legend(
    handles=[Patch(facecolor=c, edgecolor=e, hatch=h, linewidth=.42, label=n)
             for c, e, h, n in zip(COLORS, EDGES, HATCHES, PROFILE_NAMES)],
    loc='upper center', bbox_to_anchor=(.52, .992), ncol=3, frameon=False,
    fontsize=7.5, handlelength=1.35, handletextpad=.38,
    columnspacing=.9, borderaxespad=0,
)
fig.canvas.draw()
renderer = fig.canvas.get_renderer()
for artist in artists + [legend]:
    box = artist.get_window_extent(renderer)
    assert box.x0 >= 0 and box.y0 >= 0 and box.x1 <= fig.bbox.width + 1 and \
        box.y1 <= fig.bbox.height + 1, ('clipped', artist, box)

history = ROOT / 'history/access-demo-before-real-data'
history.mkdir(parents=True, exist_ok=True)
if (ROOT / 'access.pdf').exists() and not (history / 'access.pdf').exists():
    shutil.copyfile(ROOT / 'access.pdf', history / 'access.pdf')
for ext in ['pdf', 'svg', 'png']:
    fig.savefig(ROOT / f'access.{ext}', bbox_inches=None, pad_inches=0)
plt.close(fig)
assert len(exported) == 48
pd.DataFrame(exported).to_csv(DATA / 'access-figure-values.csv', index=False)
(DATA / 'access-figure-validation.json').write_text(json.dumps(dict(
    synthetic=False, source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    matched_groups=5148, bars=48, cases_per_configuration_profile=EXPECTED,
    equal_information_level_weighting=True, identical_case_ids_and_levels=True,
    matched_to_paired_analysis=True, figure_width_inches=W, figure_height_inches=H,
    shared_y_axis=[0, 100], content_bounds_check='passed',
), indent=2) + '\n')
print(f'Saved {ROOT / "access.pdf"}; all 48 real means validated.')
