# Agent runners

`src/opsgauge/agents/` provides Codex and Claude Code adapters used with Harbor. Harness executables and provider credentials are external dependencies.

Install `configs/runner-requirements.txt` in a separate runner environment compatible with the recorded Python 3.14 host. Replay images and offline scoring have their own dependencies; do not combine these environments automatically. Per-task/profile versions and time budgets are recorded in `configs/`.

Configure the selected Harbor provider integration or agent arguments using locally supplied credentials. `.env.example` lists supported variable names. Materialized tasks define the evidence-access environment; provider API settings do not change that evidence interface.

## Install the adapters

```bash
python3.14 -m venv .venv-runner
.venv-runner/bin/python -m pip install -e ".[agents]"
```

Harbor import paths are `opsgauge.agents.codex_agent:CodexAgent` and `opsgauge.agents.claude_agent:ClaudeAgent`. Configure the corresponding import path in your Harbor run settings, along with the model identifier and local provider settings.

## Complete commands and credentials

The root [README](../README.md#4-install-the-agent-runner-and-select-a-task) includes actual task selection, separate Python environments, both custom adapter commands, `.env` handling, version arguments, install-only checks, endpoint protocols and score inputs. Use `--agent opsgauge.agents.codex_agent:CodexAgent` or `--agent opsgauge.agents.claude_agent:ClaudeAgent` with `--agent-kwarg version=...`; a custom import path is accepted by Harbor's `--agent` option.

DR images additionally need the reader-built [preinstalled runtime](../containers/claude-runtime/README.md) before the runner can create the environment. Other task images rely on adapter installation. Container startup checks do not themselves exercise this installation phase or contact a model.
