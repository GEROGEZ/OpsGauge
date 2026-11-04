"""Single default CLI for compiling contracts and scoring archived Harbor jobs."""
import argparse
import json
from pathlib import Path

from .engine import compile_contract, verify, instant, number
from . import VERSION
from .trace_work import evaluate_trace, SCORING_VERSION


def json_objects(text):
    """Decode printed JSON objects only, never interpret an agent's code."""
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        if text[index] not in '{[':
            index += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, index)
        except ValueError:
            index += 1
            continue
        yield obj
        index = end


def candidates_from_trace(path, fields):
    """Latest completed structured candidate, never best-of or private thoughts."""
    found = []
    if not path.is_file():
        return found
    for event_index, line in enumerate(path.read_text().splitlines()):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            # Raw Codex stdout can prefix JSONL with transport/runtime banners.
            # They cannot establish progress. A malformed JSON event is still
            # an error, never silently dropped as a failed agent decision.
            if not line.lstrip().startswith('{'):
                continue
            raise ValueError('corrupt_trace_jsonl')
        if not isinstance(event, dict):
            continue
        item = event.get('item') or {}
        if (event.get('type') != 'item.completed' or item.get('type') != 'command_execution'
                or item.get('exit_code') != 0):
            continue
        for obj in json_objects(item.get('aggregated_output', '')):
            if isinstance(obj, dict) and 'selected_period' in obj and any(f in obj for f in fields):
                try:
                    for key in ['first_met_sample', 'last_met_sample']:
                        instant(obj['selected_period'][key])
                    typed_fields = [obj[f] for f in fields if isinstance(obj.get(f), dict)
                                    and isinstance(obj[f].get('instance'), str)
                                    and number(obj[f].get('value'))]
                    if not typed_fields:
                        continue
                    for row in typed_fields:
                        instant(row['time'])
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue  # Instruction schemas and placeholders aren't answers.
                found.append(dict(event_index=event_index, candidate=obj))
    return found


def score_trial(folder, contract):
    result_path = folder / 'result.json'
    raw_before = result_path.read_bytes()
    raw = json.loads(raw_before)
    exception = (raw.get('exception_info') or {}).get('exception_type')
    status = exception or 'completed'
    artifact_paths = [folder / 'artifacts/app/answer.json', folder / 'artifacts/answer.json']
    answer_path = next((p for p in artifact_paths if p.exists()), None)
    parse_error = None
    try:
        answer = json.loads(answer_path.read_text()) if answer_path else None
    except (ValueError, UnicodeDecodeError) as exc:
        answer = None
        parse_error = type(exc).__name__
    if not isinstance(answer, dict):
        answer = None
    certificate_path = next((p for p in [folder / 'artifacts/app/work-evidence.json',
                                       folder / 'artifacts/work-evidence.json'] if p.exists()), None)
    certificate_error = None
    try:
        certificate = json.loads(certificate_path.read_text()) if certificate_path else None
    except (ValueError, UnicodeDecodeError) as exc:
        certificate = None
        certificate_error = type(exc).__name__
    materialized = candidates_from_trace(folder / 'agent/codex.txt', contract['outputs'])
    source = 'submitted_artifact' if answer is not None else 'no_structured_candidate'
    process_candidate = answer
    if answer is None and materialized:
        process_candidate = materialized[-1]['candidate']
        if certificate is None:
            certificate = dict(schema_version='l2_work_evidence_v1', candidate=process_candidate, claims=[])
        source = 'latest_completed_tool_json'
    if answer is None and certificate_path and not materialized:
        source = 'work_evidence_artifact'
    # No automatic coercion or GT filling of malformed certificates.
    try:
        report = verify(contract, process_candidate, certificate, execution_status=status, submitted=answer is not None)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        if certificate is None:
            raise
        certificate_error = str(exc)
        # A malformed proof doesn't erase a valid final result or become an
        # infrastructure scoring failure. Keep unsupported process items pending.
        report = verify(contract, process_candidate, None, execution_status=status, submitted=answer is not None)
    original = ((raw.get('verifier_result') or {}).get('rewards') or {}).get('reward')
    if original is not None and answer is not None and abs(original * 100 - report['result']['score']) > 1e-7:
        raise ValueError('result_scorer_regression:' + folder.name)
    if raw_before != result_path.read_bytes():
        raise RuntimeError('original_result_changed')
    report = evaluate_trace(contract, folder / 'agent/codex.txt', report, final_answer=process_candidate,
                            candidate_event=materialized[-1]['event_index'] if materialized else None)
    report.update(trial=folder.name, candidate_source=source, original_reward=original,
                  original_result_unchanged=True, answer_parse_error=parse_error,
                  certificate_status='rejected' if certificate_error else 'present' if certificate_path else 'legacy_missing',
                  certificate_error=certificate_error,
                  materialized_candidate_event=materialized[-1]['event_index'] if materialized else None)
    return report


def score_batch(jobs, contract, output):
    trials = []
    for folder in sorted(p.parent for p in jobs.rglob('result.json') if (p.parent / 'trial.log').is_file()):
        try:
            selected = contract
            if contract.get('schema_version') == 'l2_scoring_registry_v1':
                task_name = folder.name.rsplit('__', 1)[0]
                selected = contract['contracts'][contract['tasks'][task_name]]
            if selected.get('schema_version') != VERSION:
                raise ValueError('unsupported_scoring_contract')
            trials.append(score_trial(folder, selected))
        except (ValueError, KeyError, TypeError) as exc:
            # A scoring error is not an agent failure and must not become zero.
            trials.append(dict(trial=folder.name, scoring_status='failed',
                               error_type=type(exc).__name__, error=str(exc),
                               result=None, process_score=None, valid_for_comparison=False))
    if not trials:
        raise ValueError('no_finished_trials')
    output.mkdir(parents=True, exist_ok=True)
    payload = dict(schema_version=VERSION, scoring_version=SCORING_VERSION, model_calls=0, manual_verdicts=0,
                   source=str(jobs), trials=trials, aggregate_score=None)
    (output / 'scores.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    lines = ['# L2确定性评分：结果、可验证工作及综合分', '',
             '综合分=80%可核验工作分+20%交付结果分。没有证据的项目不授分，但不认定为真实决策错误；环境/进程故障不计能力分。', '',
             '不调用LLM、不使用人工逐题判分、不执行Agent代码。读取成功工具输出中的计算表、采样网格和JSON；未知文本不做语义推断。', '',
             '与旧人工过程分不是同一版本；缺失结构化证据记待确认，不视为真实决策错误。', '',
             '过程分仍有待确认部分时，不能用这些下界比较模型或访问形式强弱。', '',
             '|实验|综合分|交付结果分|可核验工作分|未核验工作分|执行状态|', '|---|---:|---:|---:|---:|---|']
    for trial in trials:
        if trial.get('scoring_status') == 'failed':
            lines.append(f'|{trial["trial"]}|待评|待评|待评|待评|评分失败，不记Agent零分|')
        else:
            process = '待评（证据不足）' if trial['process_status'] == 'unavailable' else str(trial['process_score'])
            lines.append(f'|{trial["trial"]}|{trial["aggregate_score"]}|{trial["result"]["score"]}|{process}|'
                         f'{trial["pending_points"]}|{trial["execution_status"]}|')
    for trial in trials:
        lines += ['', '## ' + trial['trial'], '']
        for check in trial.get('checks', []):
            lines.append(f'- {check["id"]}: {check["awarded"]}/{check["maximum"]}, '
                         f'{check["status"]}。{check["reason"]}')
    (output / 'README.md').write_text('\n'.join(lines) + '\n')
    return payload


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    compile_parser = sub.add_parser('compile')
    for key in ['source', 'rule', 'truth', 'output']:
        compile_parser.add_argument('--' + key, required=True, type=Path)
    batch = sub.add_parser('batch')
    for key in ['jobs', 'contract', 'output']:
        batch.add_argument('--' + key, required=True, type=Path)
    args = p.parse_args()
    if args.action == 'compile':
        contract = compile_contract(json.loads(args.source.read_text()), json.loads(args.rule.read_text()),
                                    json.loads(args.truth.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': 'compiled', 'facts': len(contract['facts']),
                          'phase_samples': len(contract['phase_rows'])}))
    else:
        result = score_batch(args.jobs, json.loads(args.contract.read_text()), args.output)
        for trial in result['trials']:
            print(json.dumps({k: trial.get(k) for k in ['trial', 'aggregate_score', 'aggregate_score_kind', 'result', 'process_score',
                              'pending_points', 'execution_status', 'scoring_status']}, ensure_ascii=False))
        if any(t.get('scoring_status') == 'failed' for t in result['trials']):
            raise SystemExit(2)


if __name__ == '__main__':
    main()
