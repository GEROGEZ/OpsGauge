"""Generate the complete 2x2 appendix table from frozen scored cases."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'real-data'
OUT = ROOT / 'appendix-slice-table-preview'
source = DATA / 'canonical-results.json'
rows = json.loads(source.read_text())['rows']
orders = json.loads((DATA / 'plot-labels.json').read_text())['orders']
names = json.loads((DATA / 'paper-slice-names.json').read_text())['labels']
tasks = [('DR', 'Data Retrieval'), ('AD', 'Anomaly Detection'),
         ('HA', 'Health Assessment'), ('RCA', 'Root-Cause Analysis')]
profiles = [('F', 'shell_raw'), ('N', 'native_raw'), ('S', 'umodel_semantic')]
configs = [('Q/C', 'codex-qwen38'), ('Q/CC', 'claude-qwen38'),
           ('G/C', 'codex-glm53'), ('G/CC', 'claude-glm53')]
grouped = defaultdict(list)
families = defaultdict(set)
for row in rows:
    key = row['task'], row['slice'], row['profile'], row['config'], row['level']
    grouped[key].append(row['score'])
    families[key].add(row['family'])

means = {}
records = []
for task, _ in tasks:
    assert set(orders[task]) == {r['slice'] for r in rows if r['task'] == task}
    for index, slice_id in enumerate(orders[task], 1):
        reference_families = None
        for _, profile in profiles:
            for _, config in configs:
                level_scores = []
                for level in (1, 2, 3):
                    key = task, slice_id, profile, config, level
                    values = grouped[key]
                    assert len(values) == len(families[key])
                    if reference_families is None:
                        reference_families = families[key]
                    assert families[key] == reference_families
                    level_scores.append(sum(values) / len(values))
                mean = sum(level_scores) / 3
                means[task, slice_id, profile, config] = mean
                records.append(dict(task=task, slice=slice_id,
                    identifier=f'S{index}', profile=profile, config=config,
                    mean=mean, displayed=f'{mean:.1f}'))
assert len(records) == 336

def block(task, title, letter):
    n = len(orders[task])
    lines = [r'\begin{minipage}[t]{0.49\textwidth}', r'\centering',
        rf'\textbf{{({letter}) {title}}}\par\vspace{{3pt}}',
        rf'\begin{{tabular*}}{{\linewidth}}{{@{{\extracolsep{{\fill}}}}ll*{{{n}}}{{r}}@{{}}}}',
        r'\toprule',
        'P & Config. & ' + ' & '.join(f'S{i}' for i in range(1, n + 1)) + r' \\',
        r'\midrule']
    for pi, (short, profile) in enumerate(profiles):
        if pi:
            lines.append(rf'\cmidrule(lr){{1-{n + 2}}}')
        for ci, (config_short, config) in enumerate(configs):
            left = rf'\multirow{{4}}{{*}}{{{short}}}' if ci == 0 else ''
            numbers = [f'{means[task, s, profile, config]:.1f}' for s in orders[task]]
            lines.append(' & '.join([left, config_short] + numbers) + r' \\')
    lines.extend([r'\bottomrule', r'\end{tabular*}', r'\end{minipage}'])
    return '\n'.join(lines)

header = r'''\begin{table*}[t]
\centering
\caption{Complete slice-level results (mean reward, $\times 100$).
Scores average equally over L1--L3.}
\label{tab:app-slice-results}
\small
\setlength{\tabcolsep}{1.5pt}
\renewcommand{\arraystretch}{1.08}
'''
blocks = [block(t, title, chr(97 + i)) for i, (t, title) in enumerate(tasks)]
body = blocks[0] + r'\hfill' + '\n' + blocks[1]
body += '\n\n' + r'\par\vspace{8pt}' + '\n\n'
body += blocks[2] + r'\hfill' + '\n' + blocks[3]
footer = r'''
\par\vspace{4pt}
\begin{minipage}{\textwidth}
\footnotesize
\textit{Notation.}
P: access profile; F: File-based; N: Native-API; S: Semantic-query.
Q: Qwen3.8-Max; G: GLM-5.3; C: Codex; CC: Claude Code.
Slice identifiers are local to each task.\par
'''
for task, _ in tasks:
    mapping = '; '.join(f'S{i}: {names[s]}' for i, s in enumerate(orders[task], 1))
    footer += rf'\textit{{{task}.}} ' + mapping + r'.\par' + '\n'
footer += r'\end{minipage}' + '\n' + r'\end{table*}' + '\n'
latex = header + body + footer
OUT.mkdir(exist_ok=True)
(OUT / 'table.tex').write_text(latex)
(OUT / 'main.tex').write_text(r'''\documentclass[sigconf,anonymous,review,balance=false]{acmart}
\usepackage{booktabs}
\usepackage{multirow}
\settopmatter{printacmref=false}
\begin{document}
\input{table}
\end{document}
''')
validation = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    scored_rows=len(rows), entries=len(records),
    aggregation='equal mean of L1, L2, and L3 slice means',
    matching='identical family sets across levels, configurations, and profiles',
    identifiers='same task-local order and labels as distribution.pdf',
    rounding='one decimal', records=records)
(OUT / 'validation.json').write_text(json.dumps(validation, indent=2))
print(latex)
