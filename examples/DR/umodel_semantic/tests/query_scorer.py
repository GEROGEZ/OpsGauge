"""Deterministic family adapters. No model calls and no execution of agent code.

Retains the established 80% observable-work / 20% submitted-result policy.
Only completed, structured calculations are observable work; absent evidence
is not a demonstrated decision error. Raw API rows alone are not calculations.
"""
import datetime as dt
import json
import math
from pathlib import Path
import re

VERSION = 'l2_query_families_observable_work_v1'


def instant(text):
    if not isinstance(text, str):
        raise ValueError('timestamp_string_required')
    m = re.search(r'\.(\d+)', text)
    nano = int(((m.group(1) if m else '') + '000000000')[:9])
    stamp = dt.datetime.fromisoformat(re.sub(r'\.\d+', '', text).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone_required')
    delta = stamp - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 10**9 + nano


def equal(a, b, key=''):
    if key in ('first_trigger_at', 'last_notification_at', 'marker_time', 'end'):
        try:
            return instant(a) == instant(b)
        except (TypeError, ValueError):
            return False
    if isinstance(b, (float, int)) and not isinstance(b, bool):
        return type(a) in (int, float) and math.isfinite(a) and math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-6)
    if isinstance(b, list):
        return isinstance(a, list) and len(a) == len(set(map(str, a))) and set(a) == set(b)
    return a == b


def index(rows, keys):
    if not isinstance(rows, list):
        return {}, 1
    out = {}; invalid = 0
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in keys):
            invalid += 1; continue
        identity = tuple(row[k] for k in keys)
        if identity in out:
            invalid += 1
        else:
            out[identity] = row
    return out, invalid


def compare(contract, answer):
    """Return field checks, with extra/missing/duplicate objects penalized."""
    truth = contract['private_ground_truth']; family = contract['family_id']
    answer = answer if isinstance(answer, dict) else {}
    checks = []

    def scalar(label, got, expected):
        checks.append(dict(id=label, correct=equal(got, expected, label.rsplit('/', 1)[-1]),
                           actual=got, expected=expected))

    def table(label, got, expected, keys, fields):
        observed, bad = index(got, keys); wanted, _ = index(expected, keys)
        scalar(label + '/object_set', sorted(observed), sorted(wanted))
        for n in range(bad):
            scalar(label + '/invalid_or_duplicate_' + str(n), False, True)
        for identity in sorted(set(observed) | set(wanted)):
            row, target = observed.get(identity, {}), wanted.get(identity)
            name = label + '/' + '|'.join(identity)
            if target is None:
                for field in fields:
                    scalar(name + '/' + field, row.get(field), '<not_in_scope>')
            else:
                for field in fields:
                    scalar(name + '/' + field, row.get(field), target[field])
        return observed, wanted

    if family == 'alert_transaction_state':
        scalar('total_active_transactions', answer.get('total_active_transactions'), truth['total_active_transactions'])
        got, wanted = table('rules', answer.get('rules'), truth['rules'], ['rule_id'], ['active_count'])
        for identity, target in wanted.items():
            table('transactions/' + identity[0], got.get(identity, {}).get('transactions'), target['transactions'],
                  ['trans_id'], ['first_trigger_at', 'last_notification_at', 'last_status', 'entity_ids'])
    elif family == 'observed_call_failure_rankings':
        for name, metric in [('by_failure_count', 'error_count'), ('by_failure_ratio', 'error_ratio')]:
            got, wanted = table(name, answer.get(name), truth[name], ['operation'], ['call_count', 'error_count', 'error_ratio'])
            rows = answer.get(name, [])
            valid = isinstance(rows, list) and len(got) == len(rows) and set(got) == set(wanted)
            if valid:
                values = [wanted[(r['operation'],)][metric] for r in rows]
                valid = all(a >= b for a, b in zip(values, values[1:]))
            scalar(name + '/ranking', valid, True)
            for identity, target in wanted.items():
                row = got.get(identity, {})
                refs = row.get('evidence_refs', row.get('sample_refs', []))
                valid_refs = contract.get('operation_refs', {}).get(identity[0], target.get('sample_refs', []))
                scalar(name + '/' + identity[0] + '/source_evidence',
                       isinstance(refs, list) and bool(refs) and all(r in valid_refs for r in refs), True)
    elif family == 'event_anchored_call_intervals':
        scalar('marker_time', answer.get('marker_time'), truth['marker_time'])
        got, wanted = table('results', answer.get('results'), truth['results'], ['operation'],
                            ['call_count', 'after_marker_total_ms'])
        for identity, target in wanted.items():
            table('last_completed_calls/' + identity[0], got.get(identity, {}).get('last_completed_calls'),
                  target['last_completed_calls'], ['trace_id', 'span_id'], ['end'])
    else:
        raise ValueError('unsupported_family')
    return dict(score=round(100 * sum(c['correct'] for c in checks) / len(checks), 6),
                strict_pass=all(c['correct'] for c in checks), checks=checks)


def objects(text):
    decoder = json.JSONDecoder(); position = 0
    while position < len(text):
        if text[position] not in '{[':
            position += 1; continue
        try:
            obj, end = decoder.raw_decode(text, position)
        except ValueError:
            position += 1; continue
        yield obj; position = end


def score_trial(folder, contract):
    result = json.loads((folder / 'result.json').read_text())
    exception = (result.get('exception_info') or {}).get('exception_type')
    status = exception or 'completed'
    valid = not exception or 'AgentTimeout' in exception
    artifacts = folder / 'artifacts'
    def load(name):
        for p in [artifacts / 'app' / name, artifacts / name]:
            if p.is_file():
                try:
                    obj = json.loads(p.read_text())
                    return obj if isinstance(obj, dict) else None
                except (ValueError, UnicodeError):
                    return None
        return None
    submitted = load('answer.json'); work = load('work-evidence.json')
    roots = set(contract['private_ground_truth']) - {'marker_evidence_refs'}
    candidates = []
    trace = folder / 'agent/codex.txt'
    if trace.exists():
        for i, line in enumerate(trace.read_text().splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get('item') or {}
            if event.get('type') != 'item.completed' or item.get('type') != 'command_execution' or item.get('exit_code') != 0:
                continue
            for obj in objects(item.get('aggregated_output', '')):
                if isinstance(obj, dict) and roots.intersection(obj) and 'page' not in obj:
                    candidates.append((i, obj))
    candidate = (submitted if submitted is not None else (work or {}).get('candidate')
                 if isinstance((work or {}).get('candidate'), dict) else candidates[-1][1] if candidates else None)
    source = ('submitted_artifact' if submitted is not None else 'work_evidence_artifact' if work and work.get('candidate')
              else 'latest_completed_tool_json' if candidates else 'unavailable')
    result_score = compare(contract, submitted) if submitted is not None else dict(score=0, strict_pass=False, checks=[])
    process = compare(contract, candidate) if candidate is not None else dict(score=0, strict_pass=False, checks=[])
    return dict(scoring_version=VERSION, trial=folder.name, execution_status=status, valid_for_comparison=valid,
                submitted=submitted is not None, candidate_source=source,
                result=result_score, process=process, process_score=process['score'],
                aggregate_score=round(.8 * process['score'] + .2 * result_score['score'], 6) if valid else None,
                process_observable=candidate is not None,
                interpretation='Completed structured calculations only; missing work is not demonstrated decision error.',
                original_reward=((result.get('verifier_result') or {}).get('rewards') or {}).get('reward'))


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--truth', type=Path, default=Path('/tests/private/contract.json'))
    p.add_argument('--trial', type=Path); p.add_argument('--output', type=Path); args = p.parse_args()
    contract = json.loads(args.truth.read_text())
    if args.trial:
        report = score_trial(args.trial, contract)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        try:
            answer = json.loads(Path('/app/answer.json').read_text())
        except (OSError, ValueError):
            answer = None
        report = compare(contract, answer)
        dest = Path('/logs/verifier'); dest.mkdir(parents=True, exist_ok=True)
        (dest / 'details.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (dest / 'reward.txt').write_text(str(report['score'] / 100))


if __name__ == '__main__':
    main()
