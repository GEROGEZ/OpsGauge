"""No-model startup/isolation smoke for a NEW evidence-protocol package."""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess

from .integration import PUBLIC_PROTOCOL


def command(args, timeout=120):
    env = dict(os.environ, DOCKER_BUILDKIT='0', COMPOSE_DOCKER_CLI_BUILD='0')
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=env, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise TimeoutError('acceptance_command_timeout')
    if process.returncode:
        raise RuntimeError('acceptance_command_failed:' + output[-1500:])
    return output


def accept(package, baseline, output):
    results = []
    for task in sorted((package / 'harbor-tasks').iterdir()):
        if not task.is_dir():
            continue
        old = baseline / 'harbor-tasks' / task.name
        row = dict(task=task.name, status='running', model_calls=0)
        compose = ['docker', 'compose', '-p', 'l2-evidence-accept-' + task.name.rsplit('-', 1)[1],
                   '-f', str(task / 'environment/docker-compose.yaml')]
        try:
            # This is a delta acceptance against the already accepted data/API
            # package. No telemetry or original verifier changes are permitted.
            for source in old.rglob('*'):
                rel = source.relative_to(old)
                if source.is_file() and str(rel) not in ['instruction.md', 'environment/main/scene/instruction.md']:
                    if source.read_bytes() != (task / rel).read_bytes():
                        raise ValueError('baseline_data_or_verifier_changed:' + str(rel))
            if not (task / 'instruction.md').read_text().startswith((old / 'instruction.md').read_text()):
                raise ValueError('business_input_changed')
            command(compose + ['config', '--quiet'], timeout=30)
            command(compose + ['up', '-d', '--build', '--wait', '--wait-timeout', '90'], timeout=240)
            container = command(compose + ['ps', '-q', 'main'], timeout=20).strip()
            inspection = json.loads(command(['docker', 'inspect', container], timeout=20))[0]
            if inspection['Mounts'] or inspection['HostConfig']['Privileged']:
                raise ValueError('private_or_privileged_mount')
            script = '''from pathlib import Path
import os,urllib.request
assert not Path('/tests').exists()
assert not Path('/app/answer.json').exists()
assert not Path('/app/work-evidence.json').exists()
assert not Path('/var/run/docker.sock').exists()
assert not any(k in os.environ for k in ['OPENAI_API_KEY','DASHSCOPE_API_KEY'])
assert Path('/scene/EVIDENCE_FORMAT.md').read_text()==PROTOCOL
for p in Path('/scene').rglob('*'):
 assert not any(x in p.name for x in ['ground_truth','scorer','contract.json','source.json'])
if os.environ.get('UMODEL_URL'):
 urllib.request.urlopen(os.environ['UMODEL_URL']+'/v1/health',timeout=15).read()
elif os.environ.get('PROMETHEUS_URL'):
 urllib.request.urlopen(os.environ['PROMETHEUS_URL']+'/-/ready',timeout=15).read()
else:assert Path('/scene/records.jsonl').stat().st_size>0
print('public_protocol_and_interface_passed')
'''.replace('PROTOCOL', repr(PUBLIC_PROTOCOL))
            command(compose + ['exec', '-T', 'main', 'python', '-c', script], timeout=30)
            row.update(status='passed', baseline_data_and_verifier_unchanged=True,
                       neutral_protocol_visible=True, private_contract_visible=False,
                       interface_ready=True, answer_prefilled=False)
        except Exception as exc:
            row.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        finally:
            try:
                command(compose + ['down', '-v', '--remove-orphans'], timeout=60)
                row['cleaned'] = True
            except Exception as exc:
                row.update(cleaned=False, cleanup_error=str(exc))
        results.append(row)
        report = dict(status='running', model_calls=0, profiles=results)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if row['status'] != 'passed' or not row.get('cleaned'):
            break
    report = dict(status='passed' if len(results) == 3 and all(
        r['status'] == 'passed' and r.get('cleaned') for r in results) else 'failed',
        model_calls=0, acceptance_scope='delta_against_previously_accepted_frozen_data', profiles=results)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ['package', 'baseline', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if accept(args.package, args.baseline, args.output)['status'] == 'passed' else 1)
