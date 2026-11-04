# OpsGauge

OpsGauge evaluates AIOps agents on observability Data Retrieval (DR), Anomaly Detection (AD), Health Assessment (HA), and Root-Cause Analysis (RCA) in distributed services supporting Web applications.

[中文完整使用说明](README_CN.md)

| Task | Measurement families | Cases | Case–profile packages |
|---|---:|---:|---:|
| DR | 74 | 222 | 666 |
| AD | 50 | 150 | 450 |
| HA | 177 | 531 | 1,593 |
| RCA | 128 | 384 | 1,152 |
| **Total** | **429** | **1,287** | **3,861** |

L1 supplies the most investigative information; L3 the least. Families can share recorded evidence. `results/` contains 15,444 final scores for four model–harness pairings. Source AD IL3/IL2/IL1 maps to paper L1/L2/L3; the indexes record this mapping.

## 1. Choose the workflow and install host tools

| Goal | Host requirements | Model credentials? |
|---|---|---|
| Read cases or inspect recorded scores | No containers; text/JSON reader | No |
| Restore data or score saved answers | Python 3.11+, pip/venv; Git LFS for a Git checkout | No |
| Recompute paper analyses | Python 3.12 environment plus `.[analysis]` | No |
| Run a new agent | Linux amd64, Docker Engine + Compose v2-compatible plugin, Python 3.14 runner + `.[agents]` | Yes |

The tested container host uses Docker Engine 28.3.3 and Compose 5.1.2 on Linux amd64. The bundled replay image is amd64; native ARM64/macOS/Windows execution is not validated. A Linux amd64 VM/server is the supported full-run path.

On Ubuntu/Debian, install the basic host tools (Python must be 3.11+):

```bash
sudo apt-get update
sudo apt-get install -y git git-lfs curl ca-certificates python3 python3-venv
```

Install Docker Engine and its Compose plugin using the [official Docker instructions](https://docs.docker.com/engine/install/ubuntu/), then check:

```bash
docker version
docker compose version
```

Do not install Prometheus, DuckDB, Node.js, or Claude Code globally for task execution. The task images and agent adapters install their container dependencies. The separate DR image preparation step below is required.

Commands below run from the OpsGauge repository root. Replace `/path/to/OpsGauge` with your checkout or extracted release directory.

```bash
cd /path/to/OpsGauge
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
opsgauge --help
```

## 2. Obtain and restore evidence

For a Git checkout, initialize Git LFS and fetch the archive bodies:

```bash
git lfs install
git lfs pull
```

Skip those two commands for an extracted release already containing the actual `.tar.gz` archives. The release has 122 evidence shards and one replay image, approximately 12.85 GB total. Restored evidence occupies about 65 GiB; also allow space for archives, Git LFS cache and Docker builds.

```bash
opsgauge restore-data
opsgauge verify --full-hash --output tests/reports/local-data-check.json
```

Restoration creates the deduplicated `data/objects/` store. To inspect metadata without restoring the large store:

```bash
opsgauge list --task AD --profile native_raw
opsgauge verify --metadata-only --output tests/reports/local-index-check.json
```

`examples/` already contains one illustrative case per task under all three profiles. These examples can be used for environment startup without restoring the complete store; they do not replace the full dataset.

## 3. Prepare container dependencies for all tasks/profiles

| Task | File-based (`shell_raw`) | Native-API (`native_raw`) | Semantic-query (`umodel_semantic`) |
|---|---|---|---|
| DR | Bundled replay base + DR Claude runtime | Replay base + DR Claude runtime + Prometheus | Replay base + DR Claude runtime |
| AD | Python task image | Replay base + Prometheus | Replay base |
| HA | Python task image | Python API image + Prometheus | Python query-service image |
| RCA | Python task image | Python API image + Prometheus | Python query-service image |

Python task/API images are built automatically from the included Dockerfiles (`python:3.11-slim`). Native metric replay pulls `prom/prometheus:v3.10.0` automatically. HA/RCA query images install DuckDB, FastAPI, Uvicorn, Pydantic, PyYAML and pytz inside the image. Shell utilities are installed by the task Dockerfiles or agent setup. No live production observability service is required: evidence is recorded locally for replay.

Load the included replay base once:

```bash
docker load -i data/images/replay-base.docker.tar.gz
```

Before building **any DR profile**, build its preinstalled Claude runtime from public downloads:

```bash
docker build --platform linux/amd64 \
  -t benchmark-claude-runtime:2.1.309-v1 containers/claude-runtime
docker run --rm --network none benchmark-claude-runtime:2.1.309-v1
```

This recipe downloads Node.js 22.23.4 (SHA256 checked) and the npm package `@anthropic-ai/claude-code@2.1.309`; no API key is needed. The tag matches existing DR `COPY --from` instructions. DR currently needs this build even if the selected agent is Codex. It recreates the required layout and versions, not the historical image bytes. See [the recipe](containers/claude-runtime/README.md).

Initial preparation requires access to Docker Hub, Debian package repositories, PyPI, nodejs.org and registry.npmjs.org. New agent runs additionally require a reachable model endpoint. Keys belong in local runtime configuration, never in Docker build arguments, task data or committed files.

Optional startup check for all 12 bundled example environments (no agents/model calls; creates and removes its own containers and volumes):

```bash
python scripts/check_example_environments.py \
  --release-root . --output-dir local-checks/docker
```

For a single example, start and inspect its containers manually:

```bash
docker compose -p opsgauge-demo \
  -f examples/AD/native_raw/environment/docker-compose.yaml up --build -d --wait
docker compose -p opsgauge-demo \
  -f examples/AD/native_raw/environment/docker-compose.yaml ps
# Finish the inspection before cleaning up this demo's containers and volumes.
docker compose -p opsgauge-demo \
  -f examples/AD/native_raw/environment/docker-compose.yaml down --volumes
```

This checks environment startup only. It does not run an investigation or produce an agent score. Do not start this manual demo and a Harbor run under the same project name.

## 4. Install the agent runner and select a task

Use a separate Python 3.14 environment. Install Python 3.14 first using your Python distribution, or [uv's Python installer](https://docs.astral.sh/uv/guides/install-python/). Then:

```bash
python3.14 -m venv .venv-runner
. .venv-runner/bin/activate
python -m pip install -e ".[agents]"
harbor run --help
```

The runner dependencies are Harbor 0.21.0, Pydantic 2.13.4, HTTPX 0.28.1 and LiteLLM 1.97.0. Host Python and replay-container Python are intentionally separate. You do not need host installations of the Codex/Claude CLIs: the included adapters prepare them inside the agent container. Pin their versions explicitly as below; recorded task/profile settings are in `configs/`, not one universal experiment setting.

```bash
opsgauge list --task AD --profile native_raw
opsgauge materialize \
  --task-id AD--F035--L3--native_raw --output materialized/ad-native
```

Change `--task` to DR/AD/HA/RCA and `--profile` to `shell_raw`, `native_raw` or `umodel_semantic`; use a task ID printed by `list`. Exported tasks contain instructions, evidence-access environments and evaluator files. Let Harbor manage agent/test separation; do not mount the repository or evaluator `tests/` into the agent container.

## 5. Configure a model and run

The two harnesses require different endpoint protocols:

| Harness | Adapter | Local credentials | Endpoint requirement |
|---|---|---|---|
| Codex | `opsgauge.agents.codex_agent:CodexAgent` | `OPENAI_API_KEY`; optional `OPENAI_BASE_URL` | Responses-compatible endpoint |
| Claude Code | `opsgauge.agents.claude_agent:ClaudeAgent` | `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`; optional `ANTHROPIC_BASE_URL` | Anthropic Messages-compatible endpoint |

A Chat Completions-only endpoint is not sufficient for Codex. Set base URLs only when using an appropriate compatible provider/gateway. Access to the Qwen/GLM endpoints used for the paper is not included; supply your own accessible model and compatible endpoint. `.env.example` lists variable names but is not loaded automatically by the shell.

Create a local credentials file and edit only the variables needed by your provider (the file is Git-ignored):

```bash
cp .env.example .env
# Edit .env locally with your own key and optional compatible base URL.
```

Example Codex run, replacing `YOUR_MODEL_ID` with your provider-recognized model ID:

```bash
harbor run --path materialized/ad-native --env-file .env \
  --agent opsgauge.agents.codex_agent:CodexAgent \
  --model YOUR_MODEL_ID --agent-kwarg version=0.160.0 \
  --n-concurrent 1 --jobs-dir runs/codex
```

Example Claude Code run, after setting its corresponding credentials:

```bash
harbor run --path materialized/ad-native --env-file .env \
  --agent opsgauge.agents.claude_agent:ClaudeAgent \
  --model YOUR_MODEL_ID --agent-kwarg version=2.1.309 \
  --n-concurrent 1 --jobs-dir runs/claude
```

To check installation without invoking the model, add `--install-only` to either command. Normal runs use the selected task's `task.toml` limits. Provider access, protocol compatibility and quota are the reader's responsibility. Keep the resulting trial directory, answer files and tool/query records for grading. The [Harbor CLI](https://github.com/harbor-framework/docs/blob/main/cli/run.mdx) describes runner options.

## 6. Score a submission

Use the release scorer, rather than treating every raw Harbor output as the final release score. `TRIAL` below is one completed trial directory containing `result.json`, not the parent job directory.

```bash
opsgauge score --task-id AD--F035--L3--native_raw \
  --task-dir materialized/ad-native --trial /path/to/TRIAL \
  --output runs/ad-release-score.json
```

| Task | Required saved inputs for `opsgauge score` |
|---|---|
| DR | `--trial /path/to/TRIAL` (answer plus process/tool artifacts) |
| AD | `--trial`, or `--answer answer.json --progress progress.json` |
| HA/RCA | `--answer answer.json --query-trace query-events.jsonl`; or explicitly `--source-only` when query-derived evidence is intentionally omitted |

AD and HA/RCA ask agents to save answers to `/logs/agent/answer.json` (AD also writes `progress.json`); DR uses its task-specific answer path, usually `/app/answer.json`. Read the selected instruction. HA/RCA Semantic-query writes query events to `/shared/umodel-trace.jsonl`; use Harbor's `--artifact /shared/umodel-trace.jsonl` when running that profile to retain it. Native-API audit files are not interchangeable with semantic query events: preserve the profile's own audit files and use verifier-compatible records for rescoring. Arbitrary agent conversation logs are not query traces. See [scoring and citation inputs](docs/scoring.md).

Scoring makes no model calls and returns `reward` (0–1), `score` (0–100), and task components. `--source-only` can change grounding credit and is not equivalent to a full query-trace evaluation. Output paths must be new files; existing scores are not overwritten.

## 7. Reproduce analyses and check installation

Use Python 3.12 for the pinned scientific plotting dependencies, separately from the Python 3.14 agent runner:

```bash
python3.12 -m venv .venv-analysis
. .venv-analysis/bin/activate
python -m pip install -e ".[analysis]"
python analysis/code/analyze_real_slices.py
python analysis/code/analyze_real_access_profiles.py
python analysis/code/plot_selected_four_tasks.py
python analysis/code/plot_real_information_levels.py
python analysis/code/plot_real_access_profiles.py
python analysis/code/plot_distribution_combined.py

python -m unittest discover -s tests
opsgauge check-scoring --output tests/reports/local-scoring-check.json
```

Analysis uses included final scores, without models. Scoring fixture checks require restored evidence. Validation reports include [12 environment startups](tests/reports/container-startup-check.json), [new DR runtime recipe and three DR profiles](tests/reports/claude-runtime-build-check.json), and [fresh runner installation with both adapters](tests/reports/clean-runner-install-check.json). These checks do not constitute a new agent benchmark evaluation or a test of the reader's model account.

## Layout and data handling

```text
src/opsgauge/  CLI, scorers, data tools and Harbor adapters
data/          indexes, evidence archives, replay image and provenance
examples/      one illustrative case per task under three profiles
containers/    reader-buildable auxiliary image recipes
scripts/       environment validation helpers
configs/       recorded settings and dependency metadata
results/       final case scores
analysis/      aggregation and plotting
tests/         scoring fixtures and validation reports
docs/          detailed contracts, usage and dataset card
```

See [dataset card](docs/DATA_CARD.md), [data handling](docs/SANITIZATION.md), and [reproducibility](docs/REPRODUCIBILITY.md). URL aliases are recorded-evidence labels, not reachable services. License terms have not yet been specified; repository access alone does not grant redistribution rights.
