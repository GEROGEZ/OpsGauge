"""Versioned public evidence protocol and mandatory post-Harbor scoring hook."""
import json
from pathlib import Path
import shutil

from . import VERSION
from .cli import score_batch
from .trace_work import SCORING_VERSION


PUBLIC_PROTOCOL = '''# 通用作答证据格式

最终答案仍使用题目规定的文件与JSON格式。

可以把工作中已形成的结构化候选及其来源持续保存到`/app/work-evidence.json`。
该文件用于保留未完成交卷时的可验证工作；不改变题目要求。

格式：
```json
{
  "schema_version": "l2_work_evidence_v1",
  "candidate": {},
  "claims": [{"target": "<候选答案字段的JSON Pointer>", "refs": ["<支持该字段的记录引用>"]}],
  "source_refs": ["<其他引用>"],
  "observations": []
}
```

candidate使用与最终答案相同的字段，可以只保存已经形成的部分；最终答案优先。
refs使用数据接口返回的引用，不填写猜测的引用。
若接口返回的是派生时间序列引用，可在observations中保留对应的原始查询结果：
`record_ref`、`metric_name`、`entity_id`、`dimensions`、`unit`和`points`；
points是包含`timestamp`、`value`的列表，保留查询返回的原单位、尺度和时区。
不要写API密钥、访问令牌或连接凭据。此格式没有指定解题顺序或要求使用特定工具。
'''


def _install_protocol(destination):
    public = destination / 'environment/main/scene'
    if not public.is_dir():
        raise ValueError('unsupported_image_layout_requires_public_protocol_adapter')
    (public / 'EVIDENCE_FORMAT.md').write_text(PUBLIC_PROTOCOL)
    # Only a neutral interface pointer, identical across access profiles. No
    # private verification contract, scored criteria, values, or solution plan.
    instruction = destination / 'instruction.md'
    text = instruction.read_text() + '\n\n# 工作产物接口\n\n通用格式见`/scene/EVIDENCE_FORMAT.md`；如需保留工作证据，写入`/app/work-evidence.json`。\n'
    instruction.write_text(text)
    (public / 'instruction.md').write_text(text)
    return dict(scoring_version=VERSION, frozen_source_unchanged=True,
                participant_addition='generic_evidence_format_only', task=str(destination))


def prepare_task(source_task, destination):
    """Clone to a NEW task only. Never mutate an accepted/frozen task in place."""
    if not (source_task / 'environment/main/scene').is_dir():
        raise ValueError('unsupported_image_layout_requires_public_protocol_adapter')
    if destination.exists():
        raise FileExistsError('new_protocol_task_destination_exists')
    shutil.copytree(source_task, destination, copy_function=shutil.copy2)
    return _install_protocol(destination)


def prepare_package(source, destination):
    """Preserve compose-relative backend paths by cloning the whole package."""
    tasks = sorted(p for p in (source / 'harbor-tasks').iterdir() if p.is_dir())
    if not tasks or any(not (p / 'environment/main/scene').is_dir() for p in tasks):
        raise ValueError('unsupported_task_package')
    if destination.exists():
        raise FileExistsError('protocol_package_already_exists')
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    checks = [_install_protocol(destination / 'harbor-tasks' / p.name) for p in tasks]
    manifest_path = destination / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest['evidence_protocol_version'] = 'l2_work_evidence_v1'
        manifest['participant_input_unchanged'] = False
        manifest['participant_input_change'] = 'neutral_evidence_format_interface_only'
        for check in manifest.get('frozen_checks', []):
            if check.get('original_umodel_input_exact') is True:
                check['original_umodel_input_exact'] = False
            check['neutral_evidence_protocol_added'] = True
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (destination / 'evidence-protocol.json').write_text(json.dumps(dict(
        scoring_version=VERSION, protocol='l2_work_evidence_v1',
        contract_visible_to_agent=False, requires_new_container_acceptance=True,
        task_count=len(checks), tasks=checks), indent=2))
    return checks


def after_harbor(jobs, private_contract, destination, expected_tasks=None):
    """Call after normal exit OR agent timeout; no credentials or model access."""
    contract = json.loads(Path(private_contract).read_text())
    if contract.get('schema_version') not in [VERSION, 'l2_scoring_registry_v1']:
        raise ValueError('unsupported_scoring_contract')
    result = score_batch(Path(jobs), contract, Path(destination))
    failures = [t for t in result['trials'] if t.get('scoring_status') == 'failed']
    if failures:
        raise ValueError('scoring_incomplete_do_not_report_success')
    if expected_tasks is not None:
        actual = [t['trial'].rsplit('__', 1)[0] for t in result['trials']]
        if sorted(actual) != sorted(expected_tasks):
            raise ValueError('missing_or_duplicate_trials_do_not_report_complete')
    return dict(scoring_version=SCORING_VERSION, contract_version=VERSION, trials=len(result['trials']),
                model_calls=0, manual_verdicts=0,
                report=str(Path(destination) / 'scores.json'))


def validate_contracts(private_contract, task_names):
    """Fail before model calls if the batch lacks a supported per-task contract."""
    spec = json.loads(Path(private_contract).read_text())
    if spec.get('schema_version') == 'l2_scoring_registry_v1':
        for task in task_names:
            if spec['contracts'][spec['tasks'][task]].get('schema_version') != VERSION:
                raise ValueError('unsupported_contract:' + task)
    elif spec.get('schema_version') == VERSION:
        base_names = {name.rsplit('-', 1)[0] for name in task_names}
        if len(base_names) != 1:
            raise ValueError('multiple_business_cases_require_per_task_contract_registry')
    else:
        raise ValueError('unsupported_scoring_contract')
    return spec
