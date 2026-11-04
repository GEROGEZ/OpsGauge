"""Collect original submissions and invoke the frozen L3 scoring rules.

No extraction from free text or GT-assisted reconstruction. Selective evidence
loading preserves last-reference-wins semantics of the original full loader.
"""
import gzip
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def reviewed_late_submissions(trial, result, review):
    """Validate an explicit timing audit; never infer answers from a transcript."""
    if review is None:
        return set()
    def epoch(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    cutoff = result['agent_execution']['finished_at']
    if review['execution_finished'] != cutoff:
        raise ValueError('Timing review does not match this execution')
    source = trial / review['native_session_path']
    if not source.resolve().is_relative_to((trial / 'agent').resolve()):
        raise ValueError('Timing review source is outside agent artifacts')
    if sha(source) != review['native_session_sha256']:
        raise ValueError('Timing review source hash changed')
    events = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    event = next(x for x in events if x.get('uuid') == review['write_event_uuid'])
    if epoch(event['timestamp']) <= epoch(cutoff):
        raise ValueError('Reviewed write was not after the execution deadline')
    calls = [x for x in event['message']['content'] if x.get('type') == 'tool_use']
    excluded = set()
    for filename, expected_hash in review['excluded_submission_sha256'].items():
        if filename not in {'answer.json', 'progress.json'}:
            raise ValueError('Unsupported timing exclusion')
        file = trial / 'agent' / filename
        if sha(file) != expected_hash or file.stat().st_mtime <= epoch(cutoff):
            raise ValueError('Reviewed submission hash or timestamp changed')
        if not any('/logs/agent/' + filename in json.dumps(x.get('input', {})) for x in calls):
            raise ValueError('Reviewed tool call does not write this submission')
        excluded.add(filename)
    return excluded


def reviewed_submission_sources(trial, result, review):
    """Use unchanged original JSON files only with a hash-bound native write proof.

    This narrow audit handles the literal File: prefix produced by the agent's
    own patch helper. It never repairs JSON, renames originals, or reads GT.
    """
    if review is None:
        return {}
    def epoch(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    if review['schema'] != 'original_submission_path_review_v1':
        raise ValueError('Unsupported source review')
    if sha(trial / 'result.json') != review['result_sha256']:
        raise ValueError('Reviewed result changed')
    execution = result['agent_execution']
    if review['execution_finished'] != execution['finished_at']:
        raise ValueError('Source review cutoff changed')
    native = trial / review['native_session_path']
    if not native.resolve().is_relative_to((trial / 'agent/sessions').resolve()):
        raise ValueError('Source review is outside native agent sessions')
    if sha(native) != review['native_session_sha256']:
        raise ValueError('Native source hash changed')
    events = [json.loads(x) for x in native.read_text().splitlines() if x.strip()]
    calls = [x for x in events if x.get('payload', {}).get('type') == 'function_call'
             and x['payload'].get('call_id') == review['write_call_id']]
    completed = [x for x in events if x.get('payload', {}).get('type') == 'item_completed'
                 and x['payload'].get('item', {}).get('id') == review['write_call_id']]
    if len(calls) != 1 or len(completed) != 1:
        raise ValueError('Missing unique native write and completion')
    call, done = calls[0], completed[0]
    if not (epoch(execution['started_at']) <= epoch(call['timestamp']) <=
            epoch(done['timestamp']) <= epoch(execution['finished_at'])):
        raise ValueError('Reviewed write was outside execution')
    if done['payload']['item'].get('exit_code') != 0:
        raise ValueError('Native write did not complete successfully')
    cmd = json.loads(call['payload']['arguments'])['cmd']
    if call['payload']['name'] != 'exec_command' or "action, target = patch[i].split(' ', 2)[1:]" not in cmd:
        raise ValueError('Not the reviewed literal-prefix patch helper')
    patch = cmd.split("cd /logs/agent && /tmp/apply_patch <<'PATCH'\n", 1)[1].split('\nPATCH', 1)[0].splitlines()
    if patch[0] != '*** Begin Patch' or patch[-1] != '*** End Patch':
        raise ValueError('Invalid original patch boundaries')
    bodies = {}; key = None
    for line in patch[1:-1]:
        if line.startswith('*** Update File: '):
            key = line.removeprefix('*** Update File: ')
            if key in bodies: raise ValueError('Duplicate patch target')
            bodies[key] = []
        elif key and line.startswith('+'):
            bodies[key].append(line[1:])
        else: raise ValueError('Unsupported original patch line')
    sources = {}
    if not review['sources']:
        raise ValueError('Empty source review')
    for name, binding in review['sources'].items():
        if name not in {'answer.json', 'progress.json'}:
            raise ValueError('Unsupported submission name')
        original = trial / 'agent' / name
        expected = 'agent/File: ' + name
        if binding['path'] != expected or original.read_bytes() != b'':
            raise ValueError('Source review may not override a nonempty canonical file')
        if sha(original) != binding['canonical_sha256']:
            raise ValueError('Original submission hash changed')
        source = trial / expected
        if source.is_symlink() or not source.resolve().is_relative_to((trial / 'agent').resolve()):
            raise ValueError('Source escaped the agent artifact directory')
        if sha(source) != binding['sha256']:
            raise ValueError('Reviewed original source changed')
        if source.read_bytes() != ('\n'.join(bodies[name]) + '\n').encode():
            raise ValueError('Original file is not the exact native patch payload')
        if not isinstance(json.loads(source.read_text()), dict):
            raise ValueError('Reviewed source is not an unchanged JSON object')
        sources[name] = source
    return sources


def collect_and_score(trial, task, canonical_scorer, output, *, submission_timing_review=None, submission_source_review=None):
    trial, task, output = map(Path, (trial, task, output))
    result = json.loads((trial / 'result.json').read_text())
    execution = result.get('agent_execution') or {}
    inventory = {}
    for file in sorted((trial / 'agent').rglob('*')):
        if file.is_file():
            inventory[str(file.relative_to(trial))] = {'bytes': file.stat().st_size, 'sha256': sha(file)}
    report = {'trial_path': str(trial), 'task_path': str(task),
              'execution_started': execution.get('started_at'),
              'execution_finished': execution.get('finished_at'),
              'exception_type': (result.get('exception_info') or {}).get('exception_type'),
              'artifacts': inventory, 'model_calls_for_scoring': 0,
              'agent_reruns': 0, 'missing_scores_imputed_as_zero': False}
    trajectory = trial / 'agent/trajectory.json'
    if trajectory.exists():
        try:
            tr = json.loads(trajectory.read_text())
            report['trajectory_agent_steps'] = sum(s.get('source') == 'agent' for s in tr.get('steps', []))
            report['trajectory_agent'] = tr.get('agent')
        except (ValueError, TypeError):
            report['trajectory_parse_failed'] = True
    if not execution.get('started_at'):
        report.update(assessment='agent_not_started', process_score=None, result_score=None, composite_score=None)
        atomic(output, report)
        return report
    try:
        excluded = reviewed_late_submissions(trial, result, submission_timing_review)
        if excluded:
            report['submission_timing_review'] = submission_timing_review
            report['excluded_late_submissions'] = sorted(excluded)
        sources = reviewed_submission_sources(trial, result, submission_source_review)
        if sources:
            if excluded & sources.keys():
                raise ValueError('Source recovery conflicts with timing exclusion')
            report['submission_source_review'] = submission_source_review
            report['parsed_submission_sources'] = {name: str(path.relative_to(trial)) for name, path in sources.items()}
        payloads = []
        parsed_files = []
        parse_errors = {}
        for filename, empty in [('answer.json', {}), ('progress.json', None)]:
            file = sources.get(filename, trial / 'agent' / filename)
            value = empty
            if file.exists() and filename not in excluded:
                try:
                    value = json.loads(file.read_text())
                    if value is not None and not isinstance(value, dict):
                        raise ValueError(filename + ' is not a JSON object')
                    parsed_files.append(filename)
                except ValueError as exc:
                    parse_errors[filename] = dict(error_type=type(exc).__name__,
                                                 error=str(exc)[:300], sha256=sha(file))
                    value = empty
            payloads.append(value)
        report['parsed_submission_files'] = parsed_files
        report['submission_parse_errors'] = parse_errors
        if parse_errors and not parsed_files:
            raise ValueError('No submission file could be parsed; leave this trial unscored')
        answer, progress = payloads
        wanted = set()
        for payload in payloads:
            for claim in (payload or {}).get('claims', []):
                if isinstance(claim, dict):
                    wanted.update(ref for ref in claim.get('evidence_refs', []) if isinstance(ref, str))
        records = {}
        evidence = task / 'tests/case/public/evidence.jsonl.gz'
        # With no submitted references, loading the evidence cannot affect scores.
        count = 0
        if wanted:
            with gzip.open(evidence, 'rt') as stream:
                for line in stream:
                    row = json.loads(line)
                    count += 1
                    if row['record_ref'] in wanted:
                        records[row['record_ref']] = row
        config_path = task / 'tests/case/private/case-config.json'
        config = json.loads(config_path.read_text())
        spec = importlib.util.spec_from_file_location('frozen_l3_scorer', canonical_scorer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._load = lambda root: (records, config)
        scores = module.score(task / 'tests/case', answer, progress)
        report.update(scores, assessment='scored', collector='original_answer_progress_only_v3_independent_parse',
                      canonical_scorer_sha256=sha(canonical_scorer), config_sha256=sha(config_path),
                      source_rows_streamed=count, references_requested=len(wanted),
                      references_verified=len(records), selective_loading_only=True,
                      has_answer=(trial / 'agent/answer.json').exists(),
                      has_progress=(trial / 'agent/progress.json').exists(),
                      has_trajectory=(trial / 'agent/trajectory.json').exists())
        original = trial / 'verifier/details.json'
        if original.exists():
            old = json.loads(original.read_text())
            report['container_score_matches'] = all(old.get(k) == scores[k] for k in
                                                     ['process_score', 'result_score', 'composite_score', 'process_items', 'result_items'])
            if not report['container_score_matches']:
                report['assessment'] = 'score_mismatch'
        else:
            report['container_score_matches'] = None
        # Do not overwrite original artifacts or the original container verifier.
        atomic(output, report)
    except Exception as exc:
        report.update(assessment='scoring_failed', error_type=type(exc).__name__,
                      error=str(exc)[:500], process_score=None, result_score=None, composite_score=None)
        atomic(output, report)
    return report
