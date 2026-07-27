# Personal Context Node (PCN)

> 本地优先的「音频 → 上下文」个人记忆系统。

把每天佩戴 DJI Mic 录下的音频，在**本机**完成 语音活动检测(VAD) → 语音识别(ASR) → 会话切分 → 摘要/记忆候选 → 同步到 Obsidian 供人工审核 → 原始证据归档到 NAS。原始音频、ASR、转写、统计与本地存储**全部留在本地**；只有 LLM 文本处理可选本地或云端，且只接收文本，永远不发送原始音频。

经过人工确认的「记忆卡片」用本地 Ed25519 私钥签名成事件，可在未来的小团队模式中按需交换。

---

## 目录

- [架构概览](#架构概览)
- [环境要求](#环境要求)
- [快速开始（5 分钟跑通）](#快速开始5-分钟跑通)
- [启用真实 ASR（FunASR / SenseVoice）](#启用真实-asrfunasr--sensevoice)
- [使用指南](#使用指南)
- [定时运行（macOS launchd）](#定时运行macos-launchd)
- [Docker 远程部署](#docker-远程部署)
- [配置说明](#配置说明)
- [数据、隐私与安全](#数据隐私与安全)
- [开发与测试](#开发与测试)
- [故障排查](#故障排查)
- [目录结构](#目录结构)

---

## 架构概览

端口与适配器（ports-and-adapters）结构：领域代码不直接依赖 FunASR / Obsidian / NAS / launchd / 某个 LLM SDK，所有集成都在适配器后面，方便替换与离线测试。

```
DJI Mic 录音 (WAV)
      │  ingest        导入 + 体积/mtime/sha256 证据登记
      ▼
  audio_files ──▶ vad ──▶ asr ──▶ session_derive ──▶ summarize_session
                                                        │
                                          daily_generate ─▶ obsidian_publish ─▶ 人工审核
                                                        │
                                              archive (NAS, 哈希校验后才删本地)
```

| 能力 | 默认（开箱即用） | 可选真实后端 |
| --- | --- | --- |
| VAD | `energy` / `mock`（纯本地） | `funasr`（FunASR VAD）/ `command` |
| ASR | `mock`（确定性占位） | `funasr` SenseVoice（本地权重）/ `command` |
| LLM | `rule_based`（纯本地，**无需 API key**） | `command`（本地或云端文本包装脚本） |
| 归档 | `filesystem`（本地/挂载 NAS） | `command`（rsync 风格） |
| 调度 | 手动 `pcn process run` | macOS `launchd` |

所有外部命令适配器都有**可配置超时**，挂死的模型命令会被连同整个进程组终止，不会让流水线无限挂起。

---

## 环境要求

- macOS（launchd 调度针对 macOS；核心 CLI 跨平台）
- Python **3.11+**
- [uv](https://docs.astral.sh/uv/)（包/虚拟环境管理）
- Node.js 18+（仅构建 Web 控制台时需要）
- 可选：FunASR 运行时（真实中文 ASR，约 900MB 模型，本地缓存）
- 可选：挂载的 NAS 路径（原始音频归档）

---

## 快速开始（5 分钟跑通）

默认后端不需要任何 API key，用内置 `sample_data` 即可端到端跑通。

```bash
# 1. 安装依赖
uv sync

# 2. （可选）跑测试确认环境正常
uv run pytest -q            # 540+ 用例

# 3. 准备配置：复制示例并按需修改本地路径
cp config/local.example.toml config/local.toml
#   编辑 config/local.toml：把 obsidian_vault 指到一个【专用】vault（不要用已有的 Supcon 库）

# 4. 初始化工作区（建数据库、密钥、Obsidian 目录结构）
uv run pcn init \
  --data-dir data \
  --obsidian-vault /path/to/PersonalContext

# 5. 健康检查
uv run pcn doctor --config config/local.toml --source-dir sample_data
```

### 启动 Web 控制台

```bash
./scripts/start-web.sh config/local.toml
# 不传参数时默认使用 .tmp/acceptance/config.toml
```

脚本会在 `web/dist` 缺失时自动构建前端，然后在 **http://127.0.0.1:8765/app/** 启动控制台（仅绑定 127.0.0.1）。打开后点「导入」即可驱动整条流水线。

### 命令行一把梭

不想用 UI，可以一条命令端到端（默认 mock/规则后端）：

```bash
uv run pcn run-all --config config/local.toml --source-dir sample_data --mock
```

> 完整的分步 smoke 命令（每一步的预期输出）见 [`RUNBOOK.md`](RUNBOOK.md)。

---

## 启用真实 ASR（FunASR / SenseVoice）

ASR 在**本机**用 PyTorch 推理，不调云端。模型首次会从 ModelScope 下载到 `~/.cache/modelscope/`，之后离线复用。

```bash
# 1. 安装 funasr 运行时（torch / torchaudio / modelscope）
uv sync --extra funasr

# 2. 用 funasr 后端冒烟一条真实录音（首次会下载约 900MB 模型）
uv run --extra funasr pcn model-smoke \
  --config config/funasr.example.toml \
  --audio-path sample_data/TX01_MIC001_20260607_155539_orig.wav \
  --data-dir .tmp/funasr-smoke-data \
  --obsidian-vault .tmp/funasr-smoke-vault
#   期望输出含 status=ok / model_name=... / transcript_segments=<n>
```

在 `config/local.toml` 里启用真实后端：

```toml
[vad]
backend = "funasr"            # 或保留 energy

[asr]
backend = "command"
command = '"/abs/path/.venv/bin/python3" scripts/funasr_sensevoice_wrapper.py --model iic/SenseVoiceSmall --language zh'
```

> 想换模型缓存位置：设环境变量 `MODELSCOPE_CACHE=/your/path`。
> Docker 部署时把宿主机 `~/.cache/modelscope` 挂进容器即可避免重复下载。

启动真实任务前先 `uv run pcn doctor --config config/local.toml`：当 VAD/ASR 后端为 `funasr` 时，输出 `funasr_runtime=ok` 才说明当前环境能 `import funasr`。

---

## 使用指南

### A. Web 控制台流程

1. **导入** —— 控制台左侧「设备」面板列出 `[device.dji_mic_3].root_path` 或 `/Volumes` 下识别到的录音源，点「导入」开始拷贝并入库（自动去重、文件名冲突自动加后缀）。
2. **处理** —— 导入后后台 worker 推进 VAD → ASR → 会话切分 → 摘要 → 日报；运行状态、进度、失败任务（含目标 id / 重试次数 / 错误信息 + 重试按钮）实时显示。
3. **查看** —— 运行结束后日期列表自动刷新，点日期 → 会话查看转写、说话人、观点候选；点音频可回放（失败会弹提示）。
4. **审核确认** —— 记忆候选只读展示，确认/拒绝在 Obsidian 完成（见下）。

界面键盘可达、窄屏自适应；后端不可用时显示可重试的错误页。

### B. 命令行流水线（分步）

```bash
C="--config config/local.toml"
uv run pcn ingest import      $C --source-dir sample_data   # 导入
uv run pcn preprocess         $C                             # VAD 切分
uv run pcn transcribe         $C                             # ASR 转写
uv run pcn summarize          $C --day 2026-06-07            # 摘要 + 记忆候选
uv run pcn obsidian publish   $C --date 2026-06-07           # 写 Obsidian 审核稿
# 或交给任务队列逐步推进：
uv run pcn process run        $C        # 领取并执行下一个任务（可反复调用）
uv run pcn process status     $C        # 查看任务状态
uv run pcn process retry      $C --task-id task_...   # 重试（立即可领取）
```

### C. 人工审核（Obsidian）

PCN 把待确认内容写进专用 vault，结构如下：

| 目录 | 内容 |
| --- | --- |
| `10_Daily/` | 每日笔记 |
| `20_Conversations/` | 会话笔记（不嵌入完整转写） |
| `30_Memory_Candidates/` | 待确认记忆候选 |
| `40_Confirmed_Memory/` | 已确认记忆 |
| `90_System/Speaker_Review/` | 说话人映射审核 |

把候选行的 `- [ ]` 改成 `- [x]`（行尾可加 `| edit: 修改后文本` / `| reject` / `| defer` / `| exclude_from_memory`），然后：

```bash
uv run pcn obsidian sync-review --config config/local.toml --date 2026-06-07
#   勾选项被确认成已签名的 memory_card.created 事件（本地 Ed25519 私钥）
uv run pcn memory verify --config config/local.toml   # 校验签名与哈希链
```

> 编辑后 `edit_grace_seconds`（默认 120s）内的文件会被跳过，避免读到你还在写的内容。

### D. 归档到 NAS

```bash
uv run pcn archive run --config config/local.toml --archive-root /Volumes/NAS/PersonalContext
```

只有**哈希校验通过**才会把音频标记为 `archived`；清理本地副本时若路径越界或本地哈希与归档不符则**拒绝删除**（绝不删唯一副本）。

---

## 定时运行（macOS launchd）

```bash
# 1. 生成 plist 模板（ingest / process / daily / archive / web）
uv run pcn launchd-write-plists \
  --config config/local.toml \
  --output-dir build/launchd \
  --working-directory "$PWD"

# 2. 预览要执行的 launchctl 命令（默认 dry-run）
uv run pcn launchd-install --plist-dir build/launchd

# 3. 真正安装（拷贝到 ~/Library/LaunchAgents 并 bootstrap）
uv run pcn launchd-install --plist-dir build/launchd --execute

# 卸载（同样默认 dry-run，需 --execute）
uv run pcn launchd-uninstall
```

生成的 plist 已自包含：用绝对路径 `uv`、带 `PATH` 环境变量（含 uv 自身目录）、安装时创建日志目录；定时 ingest 不写死源目录，默认走设备发现。

---

## Docker 远程部署

这一节是一条可直接复制执行的 Linux 服务器部署路径。默认使用 mock ASR + 本地规则摘要，目的是先验证服务、持久化和远程访问；它不会产生真实转写。真实模型见后文。

### 1. 服务器准备

- 一台 Linux 服务器（建议 4 核 / 8 GB 内存 / 20 GB 可用磁盘起步；真实 ASR 需要更多内存和模型空间）
- Git
- Docker Engine 24+ 与 Docker Compose v2
- 本机能通过 SSH 登录服务器

服务器上执行：

```bash
git clone https://github.com/PaulOctopusZLWB/v2c.git
cd v2c

# 这些目录持久化保存输入、数据库/密钥、Obsidian Markdown 和归档。
mkdir -p runtime/{input,data,obsidian,archive}

# 构建并后台启动；首次构建会安装前后端依赖。
docker compose up -d --build

# 等待 health=healthy，并检查 API。
docker compose ps
curl --fail http://127.0.0.1:8765/api/health
```

`compose.yaml` 默认只把端口发布到服务器的 `127.0.0.1`，不会直接暴露到公网。PCN 当前没有 HTTP 登录认证，**不要**把端口改成 `0.0.0.0:8765:8765`。

### 2. 从本机安全访问

在你自己的电脑上建立 SSH 隧道（保持此终端运行）：

```bash
ssh -N -L 8765:127.0.0.1:8765 <服务器用户>@<服务器地址>
```

然后打开 [http://127.0.0.1:8765/app/](http://127.0.0.1:8765/app/)。若本机 8765 已占用，可用 `-L 18765:127.0.0.1:8765`，再访问 `http://127.0.0.1:18765/app/`。

### 3. 放入音频与查看数据

默认目录映射如下：

| 服务器目录 | 容器路径 | 用途 |
| --- | --- | --- |
| `runtime/input/` | `/input`（只读） | 待导入 WAV/M4A |
| `runtime/data/` | `/data` | SQLite、原始/工作音频、身份私钥、日志 |
| `runtime/obsidian/` | `/obsidian` | PCN 专用 Obsidian vault |
| `runtime/archive/` | `/archive` | 已校验归档 |

把音频上传到服务器的 `runtime/input/` 后，在 Web 控制台「导入」页执行导入。也可在服务器上运行诊断：

```bash
docker compose exec personal-context-node \
  uv run --frozen --no-dev pcn doctor \
  --config /app/config/deploy.toml \
  --source-dir /input \
  --archive-root /archive
```

如数据目录不在仓库旁边，新建一个 `.env`（不要提交）：

```dotenv
PCN_WEB_PORT=8765
PCN_CONFIG_FILE=./config/remote.example.toml
PCN_INPUT_DIR=/srv/pcn/input
PCN_DATA_DIR=/srv/pcn/data
PCN_OBSIDIAN_DIR=/srv/pcn/obsidian
PCN_ARCHIVE_DIR=/srv/pcn/archive
```

这些宿主机目录必须预先创建，并保证 Docker 有读写权限；输入目录只需读权限。

### 4. 启用真实 ASR

默认远程配置 [`config/remote.example.toml`](config/remote.example.toml) 使用 mock 后端。要在服务器上启用 FunASR：

```bash
PCN_INSTALL_FUNASR=true docker compose build personal-context-node
docker compose run --rm personal-context-node \
  doctor --config config/funasr.example.toml
```

然后复制 `config/funasr.example.toml` 为 `config/deploy.toml`，将 Linux 服务器的 `[asr].device` 从 `mps` 改为 `cpu`（NVIDIA/CUDA 部署需按宿主机驱动另行配置），并在 `.env` 设置 `PCN_CONFIG_FILE=./config/deploy.toml`。该文件已被 `.gitignore` 排除。模型首次启动会下载；生产环境建议额外挂载持久化模型缓存。任何云端 LLM key 都只通过服务器环境变量注入，不写进 TOML、镜像或 Git。

### 5. 更新、日志、备份和回滚

```bash
# 更新到 GitHub main
git pull --ff-only
docker compose up -d --build

# 日志与停止
docker compose logs -f --tail=200
docker compose down

# 备份（先停写，再备份整个持久化目录）
docker compose stop
tar -czf "pcn-backup-$(date +%F).tgz" runtime/data runtime/obsidian runtime/archive
docker compose start
```

回滚时切回已知可用的 Git commit，再执行 `docker compose up -d --build`。代码回滚不会自动回滚 SQLite；重大升级前必须先备份 `runtime/data/`。

`.dockerignore` 排除了本地运行数据、密钥、缓存和本机构建产物；镜像会在独立构建阶段生成 Web 控制台，因此远程机器无需单独安装 Node.js。

---

## 配置说明

复制 `config/local.example.toml` → `config/local.toml`。路径相对 **配置文件所在目录** 解析（`~` 会展开）。常用字段：

```toml
[paths]
data_dir = "data"                                  # SQLite + 原始/工作音频
obsidian_vault = "/path/to/PersonalContext"        # 专用 vault（勿用 Supcon 库）
nas_archive_root = "/Volumes/NAS/PersonalContext"

[device.dji_mic_3]
enabled = true
volume_root = "/Volumes"
volume_name_patterns = ["DJI*", "MIC*", "NO NAME"] # DJI 储存可能挂成 "NO NAME"
root_path = "sample_data"                          # 固定源目录；删掉则自动发现 /Volumes

[vad]
backend = "energy"          # energy | mock | funasr | command
max_chunk_ms = 120000       # 单片上限（已下调以约束内存峰值）

[asr]
backend = "mock"            # mock | funasr | command

[llm]
backend = "rule_based"      # rule_based(无需 key) | mock | command

[archive]
backend = "filesystem"      # filesystem | command（rsync 风格）
# command = "rsync -a {source_path} {archive_path}"

[commands]
timeout_seconds = 3600      # 外部命令(ASR/VAD/LLM/归档)超时，必须为正数
```

CLI 上显式传的 `--data-dir` / `--obsidian-vault` 会覆盖配置文件；不传则用配置值。

---

## 数据、隐私与安全

- **本地优先**：原始音频、ASR、转写、统计、SQLite 全部留本地。
- **LLM 只收文本**：command LLM 适配器会剥离段落里的原始音频路径再发送。
- **签名记忆**：确认的记忆卡片用本地 Ed25519 私钥（`data/keys/pcn_ed25519.key`，`0600`）签名；`owner_did` 即公钥派生，绑定身份哈希链。
- **失败即拒删**：归档清理对路径越界 / 哈希不符的本地 raw 拒绝删除。
- **Vault 隔离**：内置安全检查禁止把 PCN 目录结构写进受保护的 Supcon vault，请务必用**专用** PersonalContext 库。

---

## 开发与测试

```bash
# Python 后端
uv run pytest -q

# Web 控制台
cd web && npm install && npm test && npm run build
```

后端 1100+ 用例、前端 300+ 用例。Web 端到端走真实后端的冒烟用例为 `tests/test_web_e2e.py`（`uv run pytest -q tests/test_web_e2e.py`）。

---

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `funasr_runtime=missing` | `uv sync --extra funasr`，并确认运行 wrapper 的 Python 是装了 funasr 的那个 |
| ASR 任务 `failed_retryable` 含 `timed out` | 命令挂死被超时终止；调大 `[commands].timeout_seconds` 或检查模型/依赖 |
| 端口 8765 被占用 | `start-web.sh` 会自动 `pkill` 旧进程；或手动 `lsof -ti TCP:8765 | xargs kill` |
| 设备/导入没反应 | `pcn doctor --config ... --source-dir <路径>`；DJI 储存常挂成 `/Volumes/NO NAME` |
| 想从零重来 | 停服务 → 删 `data/`（或对应 data_dir）→ `pcn init` → 重启 |
| 任务卡住要手动恢复 | `pcn process retry --task-id ...` / `pcn process rerun --task-type ... --target-id ...` |

`pcn doctor` 汇总健康、待办/失败任务、近期失败 job、记忆校验、源/归档可用性等诊断。

---

## 目录结构

```
.
├── src/personal_context_node/   # 核心：core(ports) + adapters + cli + web
├── web/                         # React + Vite 控制台
├── scripts/                     # start-web.sh、funasr/asr/llm wrapper 示例
├── config/                      # local.example.toml / funasr.example.toml
├── sample_data/                 # 内置示例录音
├── tests/                       # pytest 用例
├── Dockerfile / compose.yaml
├── ARCHITECTURE.md / SYSTEM_DESIGN_CN.md / IMPLEMENTATION_PLAN.md / RUNBOOK.md
└── README.md
```

更深入的设计与逐步 smoke 见 `SYSTEM_DESIGN_CN.md`、`ARCHITECTURE.md` 与 `RUNBOOK.md`。
