# OpsGauge 使用说明

OpsGauge 面向支撑 Web 应用的分布式服务，评估 AIOps agent 的数据检索（DR）、异常检测（AD）、健康评估（HA）和根因分析（RCA）能力。

数据集包含 429 个测量家族、1,287 个案例、3,861 个案例—访问方式组合及四种模型—harness 组合的 15,444 条最终成绩。L1 提供最多调查信息，L3 最少；家族可能共享证据，不等于独立事故数。AD 原始 IL3/IL2/IL1 对应论文 L1/L2/L3，索引已记录映射。

[English README](README.md)

## 1. 先选择使用方式，再安装主机工具

| 你要做什么 | 需要安装什么 | 是否需要模型账号 |
|---|---|---|
| 阅读案例、查看已有分数 | 文本或 JSON 阅读工具 | 否 |
| 恢复完整数据、给已有答案评分 | Python 3.11+、pip/venv、unzip | 否 |
| 复现论文统计和绘图 | Python 3.12 环境及 `.[analysis]` | 否 |
| 运行自己的 agent | Linux amd64、Docker Engine、Compose 插件、Python 3.14 runner 及 `.[agents]` | 是 |

容器验证使用 Linux amd64、Docker Engine 28.3.3、Compose 5.1.2。发布的回放基础镜像是 amd64；原生 ARM64、macOS 和 Windows 的完整运行尚未验证，建议在 Linux amd64 服务器或虚拟机上运行。

Ubuntu/Debian 主机可先安装基础工具，确认 Python 不低于 3.11：

```bash
sudo apt-get update
sudo apt-get install -y git unzip curl ca-certificates python3 python3-venv
```

Docker Engine 和 Compose 插件按 [Docker 官方安装说明](https://docs.docker.com/engine/install/ubuntu/) 安装。安装后确认当前用户可以执行：

```bash
docker version
docker compose version
```

以下命令均在项目根目录执行。把 `/path/to/OpsGauge` 换成实际目录：

```bash
cd /path/to/OpsGauge
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
opsgauge --help
```

主机无需另装 Prometheus、DuckDB、Node.js 或 Claude Code 来运行任务；任务容器和 agent 适配器负责安装容器内依赖，但 DR 需要先完成第 3 节的辅助镜像构建。

## 2. 下载并恢复数据

从 [Zenodo 数据记录（审稿专用私密链接，无需登录）](https://zenodo.org/records/23959317?token=eyJhbGciOiJIUzUxMiJ9.eyJpZCI6IjRjYTg3YjNjLThjNjYtNDM1Mi1iODEyLTU4MjM3NDAxNGFmZSIsImRhdGEiOnt9LCJyYW5kb20iOiJhZDU2N2UwODRjZmI2ZTRmZDZmNjk1ODliM2NiZWU4ZiJ9.0n30p-ZXFMHE1wwyvEVrijRHSTwAxM6Q4gnixJfW2I0FYf6wT1MOidHTnllk-r45t7qmAEDNf4r60OYBRJIwYQ) 下载全部 55 个文件，放到 `/path/to/opsgauge-data` 这样的目录中。其中包括 53 个独立 ZIP 分包、`ARCHIVE-SHA256SUMS.txt` 和 `data-release-manifest.json`，共包含全部 122 个数据分片及一个回放镜像，合计约 12.85 GB。如果使用 Zenodo 的 **Download all** 按钮，先将下载的外层 ZIP 解压到该数据目录。然后在 OpsGauge 代码根目录核验分包、解压全部分包，再核验原始数据压缩包：

```bash
(cd /path/to/opsgauge-data && sha256sum -c ARCHIVE-SHA256SUMS.txt)
for part in /path/to/opsgauge-data/OpsGauge-data-part*.zip; do
  unzip -o "$part" -d .
done
sha256sum -c SHA256SUMS.txt
```

分包合起来会提供 `data/shards/`、`data/images/`、`SHA256SUMS.txt` 和 `data-release-manifest.json`；恢复完整数据集需要全部 53 个分包。审稿人可将 [匿名代码仓库](https://anonymous.4open.science/r/OpsGauge-5603/) 的代码 ZIP 与这些数据文件配合使用。通过 Git 下载代码时，在克隆命令前设置 `GIT_LFS_SKIP_SMUDGE=1`，再从上述 Zenodo 记录取得数据本体。展开后的证据约占 65 GiB，还需为下载文件、证据压缩包和 Docker 构建预留空间。

```bash
opsgauge restore-data
opsgauge verify --full-hash --output tests/reports/local-data-check.json
```

完整证据恢复到去重的 `data/objects/`。只查看索引、不展开完整数据时可以执行：

```bash
opsgauge list --task AD --profile native_raw
opsgauge verify --metadata-only --output tests/reports/local-index-check.json
```

`examples/` 已提供四个任务各一个案例、每例三种访问方式的环境，可以先用来检查启动，不必先展开完整数据集。

## 3. 准备四个任务、三种访问方式的容器依赖

| 任务 | File-based：`shell_raw` | Native-API：`native_raw` | Semantic-query：`umodel_semantic` |
|---|---|---|---|
| DR | 回放基础镜像 + DR Claude 辅助镜像 | 回放基础镜像 + DR Claude 辅助镜像 + Prometheus | 回放基础镜像 + DR Claude 辅助镜像 |
| AD | Python 任务镜像 | 回放基础镜像 + Prometheus | 回放基础镜像 |
| HA | Python 任务镜像 | Python API 镜像 + Prometheus | Python 查询服务镜像 |
| RCA | Python 任务镜像 | Python API 镜像 + Prometheus | Python 查询服务镜像 |

Python 镜像由任务自带的 Dockerfile 基于 `python:3.11-slim` 自动构建；需要指标回放时会自动拉取 `prom/prometheus:v3.10.0`。HA/RCA 的查询服务会在容器中安装 DuckDB、FastAPI、Uvicorn、Pydantic、PyYAML 和 pytz。系统命令由 Dockerfile 或 agent 初始化步骤安装。无需读者连接我们原来的内网监控服务，证据已经录制到本地回放环境。

先加载随发布目录提供的回放基础镜像：

```bash
docker load -i data/images/replay-base.docker.tar.gz
```

**所有 DR 访问方式都需要提前构建下面这个辅助镜像，包括准备使用 Codex 的 DR 运行：**

```bash
docker build --platform linux/amd64 \
  -t benchmark-claude-runtime:2.1.309-v1 containers/claude-runtime
docker run --rm --network none benchmark-claude-runtime:2.1.309-v1
```

该构建从公开来源下载 Node.js 22.23.4，核验下载文件 SHA256，再安装 npm 包 `@anthropic-ai/claude-code@2.1.309`；不需要模型密钥。镜像名称用于兼容现有 DR Dockerfile 的 `COPY --from`。提供的是安装配方，读者自行获取软件；不要求发布包附带 Claude Code 软件。它重建所需目录和固定的顶层版本，不保证历史镜像字节完全一致，详见 [构建说明](containers/claude-runtime/README.md)。

首次构建需要访问 Docker Hub、Debian 软件源、PyPI、nodejs.org 和 registry.npmjs.org；运行 agent 还需访问自己的模型服务。密钥只放在本地运行配置中，不写入镜像构建参数、任务数据或 Git 文件。

可以一次检查全部 12 个示例环境。该命令只构建、启动及检查就绪状态，不调用模型；结束后清理自己创建的容器和卷：

```bash
python scripts/check_example_environments.py \
  --release-root . --output-dir local-checks/docker
```

也可以手动启动一个环境，查看服务状态：

```bash
docker compose -p opsgauge-demo \
  -f examples/AD/native_raw/environment/docker-compose.yaml up --build -d --wait
docker compose -p opsgauge-demo \
  -f examples/AD/native_raw/environment/docker-compose.yaml ps
# 查看完毕后，清理这个示例的容器和卷。
docker compose -p opsgauge-demo \
  -f examples/AD/native_raw/environment/docker-compose.yaml down --volumes
```

以上只是环境检查，不会自动完成题目或产生 agent 成绩；不要与 Harbor 运行共用同一个 Compose 项目名称。

## 4. 安装 runner，选择正式案例

运行 agent 使用独立的 Python 3.14 环境。先通过自己的 Python 发行版安装 Python 3.14，也可以参考 [uv 的 Python 安装说明](https://docs.astral.sh/uv/guides/install-python/)。然后：

```bash
python3.14 -m venv .venv-runner
. .venv-runner/bin/activate
python -m pip install -e ".[agents]"
harbor run --help
```

runner 固定依赖 Harbor 0.21.0、Pydantic 2.13.4、HTTPX 0.28.1 和 LiteLLM 1.97.0。主机 Python 与容器 Python 分开管理。无需在主机另装 Codex/Claude CLI，仓库中的适配器会在 agent 容器内准备软件。下面的命令显式固定 harness 版本；历史实验各任务及访问方式的具体设置见 `configs/`，不能把一个示例命令当作所有历史运行的统一配置。

完整数据恢复后，列出并导出一个任务：

```bash
opsgauge list --task AD --profile native_raw
opsgauge materialize \
  --task-id AD--F035--L3--native_raw --output materialized/ad-native
```

使用其他任务时，把 `--task` 改为 DR/AD/HA/RCA、`--profile` 改为三种标识之一，并从列表中选取真实 task ID。导出的目录包含题目、环境、证据及评分材料。由 Harbor 管理题目与评分材料的隔离，不要把整个仓库或 evaluator 的 `tests/` 挂载给 agent。

## 5. 配置模型接口并运行

| Harness | 适配器 | 本地环境变量 | 接口要求 |
|---|---|---|---|
| Codex | `opsgauge.agents.codex_agent:CodexAgent` | `OPENAI_API_KEY`，按需设置 `OPENAI_BASE_URL` | 支持 Responses 协议 |
| Claude Code | `opsgauge.agents.claude_agent:ClaudeAgent` | `ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN`，按需设置 `ANTHROPIC_BASE_URL` | 支持 Anthropic Messages 协议 |

仅支持 Chat Completions 的接口不能直接替代 Codex 所需接口。使用兼容服务或网关时才设置对应 base URL。我们不提供论文中 Qwen/GLM 服务的账号或内网网关；读者使用自己的模型权限和协议兼容接口。`.env.example` 只列变量名，不会被 shell 自动加载。

先复制凭据模板，在本机编辑对应的 key 和兼容接口地址；`.env` 已被 Git 忽略：

```bash
cp .env.example .env
# 用本地编辑器填写 .env，只填写当前 provider 需要的变量。
```

以下命令通过 `--env-file .env` 加载配置。将 `YOUR_MODEL_ID` 替换为该服务实际识别的模型名：

```bash
harbor run --path materialized/ad-native --env-file .env \
  --agent opsgauge.agents.codex_agent:CodexAgent \
  --model YOUR_MODEL_ID --agent-kwarg version=0.160.0 \
  --n-concurrent 1 --jobs-dir runs/codex
```

Claude Code 同理，先设置它对应的环境变量：

```bash
harbor run --path materialized/ad-native --env-file .env \
  --agent opsgauge.agents.claude_agent:ClaudeAgent \
  --model YOUR_MODEL_ID --agent-kwarg version=2.1.309 \
  --n-concurrent 1 --jobs-dir runs/claude
```

只想检查软件安装、不调用模型时，在命令末尾加 `--install-only`。正常运行采用所选 `task.toml` 的预算。模型服务的权限、兼容性和额度需要读者自行提供。保留生成的 trial 目录、答案及工具/查询记录；完整 runner 参数见 [Harbor CLI](https://github.com/harbor-framework/docs/blob/main/cli/run.mdx)。

## 6. 使用正式评分入口

不要直接把任意 Harbor 原生结果当作发布口径下的最终分数。下面 `/path/to/TRIAL` 指包含 `result.json` 的单次执行目录，不是上层 job 目录：

```bash
opsgauge score --task-id AD--F035--L3--native_raw \
  --task-dir materialized/ad-native --trial /path/to/TRIAL \
  --output runs/ad-release-score.json
```

| 任务 | 评分需要的材料 |
|---|---|
| DR | `--trial /path/to/TRIAL`，包括答案与过程/工具记录 |
| AD | `--trial`；或 `--answer answer.json --progress progress.json` |
| HA/RCA | `--answer answer.json --query-trace query-events.jsonl`；有意省略查询派生证据时才使用 `--source-only` |

AD、HA/RCA 通常要求写入 `/logs/agent/answer.json`，AD 另写 `progress.json`；DR 按具体题面保存，通常为 `/app/answer.json`。HA/RCA Semantic-query 的查询事件写在 `/shared/umodel-trace.jsonl`，运行该 profile 时可用 Harbor 的 `--artifact /shared/umodel-trace.jsonl` 留存。Native-API 的审计格式与语义查询事件不同，应保留对应接口自己的审计记录，并使用评分器兼容的查询事件；不能把任意对话日志直接当作 query trace。输入要求详见 [评分说明](docs/scoring.md)。

评分不调用 LLM，输出 `reward`（0–1）、`score`（0–100）和分项明细。`--source-only` 可能改变 grounding 得分，不等价于完整查询轨迹评分。输出路径必须是新文件，已有成绩不会被覆盖。

## 7. 复现统计图和运行离线检查

固定版本的科学计算/绘图库使用 Python 3.12，单独建环境，不要直接装进 Python 3.14 的 agent runner：

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

统计分析使用 `results/` 中的已有成绩，不需要模型凭据。评分样例检查需要恢复证据数据。已验证 [12 个环境启动](tests/reports/container-startup-check.json)、[新 DR 镜像配方及三种访问方式](tests/reports/claude-runtime-build-check.json)，以及 [新建 runner 环境中两个适配器的安装](tests/reports/clean-runner-install-check.json)。这些检查没有调用模型，不代表重新跑了一轮实验或核验了读者自己的模型账号。

## 目录与数据说明

```text
src/opsgauge/  命令入口、评分器、数据工具和 agent 适配器
data/          案例索引、证据分片、回放镜像与来源记录
examples/      四个任务各一个案例的三种访问方式
containers/    读者可自行构建的辅助镜像配方
scripts/       环境核验脚本
configs/       实验参数与依赖说明
results/       最终逐案例成绩
analysis/      聚合分析与绘图代码
tests/         评分样例和核验报告
docs/          使用说明、评分契约和 Dataset Card
```

范围与标注见 [Dataset Card](docs/DATA_CARD.md)，数据处理见 [SANITIZATION](docs/SANITIZATION.md)，复现边界见 [REPRODUCIBILITY](docs/REPRODUCIBILITY.md)。证据中的 URL 别名是录制内容标签，不是需要连接的真实服务。Zenodo 中的证据压缩包按 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 发布；回放镜像中的第三方软件保留各自的许可证。仓库其余内容尚未指定许可证。
