# mine2digest

批量半自动文献检索 + 论文写作材料抽取。从检索获取（mining）到证据提炼（digest），
输出可直接支撑论文写作的结构化材料。

设计规格见 `交接.txt`。

## 安装

```bash
pip install -e .          # 核心（typer / pydantic / PyYAML）
pip install -e ".[all]"   # 含 pdf / fetch / 测试依赖
```

## 快速开始

```bash
mine2digest init
pytest -q
```

所有命令遵循统一契约：输入 JSON（stdin 或 `--file`），输出 JSON（stdout）。
成功 `{"ok": true, ...}`，失败 `{"ok": false, "reason": "..."}` 且退出码 1。
每次调用追加一行到工作区 `logs/cli.jsonl`。

## 工作区与资产

`mine2digest init` 在**当前工作目录**创建 `m2d/`（数据 + 产物），并写入标记
`m2d/.mine2digest`。`db/`、`papers/`、`inbox/`、`exports/`、`drafts/`、`outputs/`、
`logs/`、`tmp/` 都在此。**只读资产**（`config/labels.yaml`、`config/*_prompts/`、
`docs/tools.json`、`scripts/`）仍取自安装目录。

解析优先级：`MINE2DIGEST_WORKSPACE` > 从 cwd 向上找到的带标记 `m2d/` > `<cwd>/m2d`。
在项目根或其任意子目录运行命令都会命中同一工作区。可用 `init --workspace <dir>`
指定目录；`MINE2DIGEST_DB` / `MINE2DIGEST_LABELS` 仍可覆盖。内置默认
`labels.yaml` 与角色提示词已随包分发（`src/mine2digest/data/`），无需工作区自带。

## 命令状态

| 命令 | 迭代 | 状态 |
|---|---|---|
| `init` | 0 | 已实现 |
| `add-candidates` | 1b | 已实现 |
| `add-relations` | 1b | 已实现 |
| `next-candidates` | 1a | 已实现 |
| `submit-vote` | 1a | 已实现 |
| `tally-votes` | 1a | 已实现 |
| `next-fetch` | 1c | 已实现 |
| `report-fetch` | 1c | 已实现 |
| `ingest-manual` | 1c | 已实现 |
| `next-reading` | 1d | 已实现 |
| `add-card` | 1d | 已实现 |
| `verify-card` | 1d | 已实现 |
| `next-caveat` | 2 | 已实现 |
| `add-caveat` | 2 | 已实现 |
| `audit` | 2 | 已实现 |
| `submit-audit` | 2 | 已实现 |
| `reject-card` | 2 | 已实现 |
| `coverage` | 2 | 已实现 |
| `query` | 2 | 已实现 |
| `lookup` | 2 | 已实现 |
| `export` | 2 | 已实现 |
| `revive` | 2 | 已实现 |
| `templates extract` | 3 | 已实现 |
| `figures generate` | 3 | 已实现 |
| `draft` | 4 | 已实现 |
| `verify` | 4 | 已实现 |

## 作为 skill / 工具使用

产品形态是“技能 + CLI”，不是自治 agent，也不依赖 LangChain：

- 完整 skill：`.opencode/skills/mine2digest/SKILL.md`（opencode 自动加载；改后需重启）
- 可移植入口：`SKILL.md`
- 机器可读 schema：`docs/tools.json`（每个命令的入参与 stdin 的 pydantic schema）

多 agent 由宿主以**独立上下文**派发，主 agent 只派发与提交（详见 skill）：

| 角色 | 独立性 | 产物 |
|---|---|---|
| Screener A（PICO）/ B（规则表） | 必须双盲 | `Vote` → `submit-vote --agent-id A|B` |
| Reader | 每篇独立 | `CardFile` → `add-card` |
| Caveat | 每卡独立 | `Caveat` → `add-caveat` |
| Auditor | 与 Reader 隔离 | `AuditFile` → `submit-audit` |

刷新 / 校验 schema：

```bash
python scripts/export_tools.py          # 生成 docs/tools.json
python scripts/export_tools.py --check  # CI 校验是否最新
```

### 全局安装（opencode）

```bash
python scripts/install_skill.py            # -> ~/.config/opencode/skills/mine2digest/SKILL.md
python scripts/install_skill.py --check    # 校验全局副本是否最新
python scripts/install_skill.py --uninstall
```

全局副本由仓库内 skill 生成，并在开头注入只读资产根 `ASSETS`（= 安装目录）。命令
在**当前工作目录**运行，`mine2digest init` 会在那里创建 `m2d/` 工作区；只有
`config/prompts/docs/scripts` 取自 `<ASSETS>/...`，产物一律留在工作区。更新 skill 后
重跑安装并重启 opencode；当前会话不会热加载。

## 配套脚本

```bash
# A1 种子发现（OpenAlex）-> 规范化 JSONL -> add-candidates
python scripts/openalex_seed.py --query "..." --limit 50 --out seeds.jsonl
mine2digest add-candidates --source openalex --file seeds.jsonl

# A2 引用扩展（Semantic Scholar 一跳）
python scripts/s2_expand.py --seed S2ID --out-prefix out
mine2digest add-candidates --source s2 --file out.candidates.jsonl
mine2digest add-relations --file out.relations.jsonl
```

脚本的联网部分 opt-in（`httpx` 惰性导入）；离线可用 `--from-file` 喂已抓取的原始响应。

### 获取流水线（A4）

```bash
# worker 依次尝试 Unpaywall -> arXiv -> OpenReview -> PMC，并回填结果
python scripts/fetch_worker.py --limit 5 --rounds 3 --worker w1

# 或手动：认领 -> 自己下载 -> 回填
mine2digest next-fetch --limit 10 --worker w1
mine2digest report-fetch --paper-id <id> --status success --path <pdf> --source arxiv
mine2digest report-fetch --paper-id <id> --status failed --source pmc --error timeout --retry

# 闭源 PDF：放进工作区 inbox/ 后人工投放
mine2digest ingest-manual --file m2d/inbox/xxx.pdf --doi 10.1/x --title "..."
```

失败论文汇总在工作区 `exports/fail_list.json`；成功论文归档到工作区 `papers/{slug}/`（`paper.pdf` + `meta.json`）。
`UNPAYWALL_EMAIL` 为 Unpaywall fetcher 必填。

OpenReview 是按标题搜索的兜底源：只接受**标题匹配**的笔记（归一化 + `difflib` 相似度
`>= 0.9`），否则报 `no openreview title match` 并继续后续 fetcher，避免抓到同名/近似标题的
错误 PDF（见“变更记录”）。

### 阅读与证据卡（B1–B2）

```bash
mine2digest next-reading --limit 5          # 带 merged_uses + 预提取页文本
mine2digest add-card --file cards.json      # 事务内建卡 + locator 硬门校验
mine2digest verify-card --card-id <id>      # 单独重跑 locator
```

`add-card` 的每张卡需 `source_span`（可带 `evidence_locator` 如 `"p.7, Table 3"`）；
locator 用 `rapidfuzz.partial_ratio >= 75` 校验，**任一卡失败则整批回滚**。
页文本缓存于 `papers/{slug}/extracted/page-{n}.txt`（PyMuPDF，幂等）。

### 反证、审计与输出（B3–B4 / C）

```bash
# 反证：取尚无 caveat 的已确认卡（带页文本），写回 caveat + 置信度
mine2digest next-caveat --limit 5
mine2digest add-caveat --card-id <id> --file caveat.json

# 审计：随机抽样 -> 人工/agent 复核 -> 回填并更新 source_reliability
mine2digest audit --rate 0.1
mine2digest submit-audit --file audit.json    # {"results":[{"card_id":..,"passes":..,"reason":..}]}
mine2digest reject-card --card-id <id> --reason "out of scope"

# 输出：覆盖矩阵 / 查询 / 单篇回溯 / 导出
mine2digest coverage                          # section × type；空格标 reclaimable
mine2digest query --type baseline --section experiments --min-confidence 0.5
mine2digest lookup --paper-id <id>            # 全信息回溯（含被丢弃/驳回用途）
mine2digest export                            # 全部只读视图 -> exports/（--view 单个）

# 复活赛：low_priority → queued（折扣入队）/ discarded → candidate（重筛）
mine2digest revive --gaps                                                  # 覆盖空格对应的 low_priority
mine2digest revive --type baseline --section experiments --limit 10
mine2digest revive --paper-id <id>                                         # 显式复活单篇
```

- `query` 排序 = `confidence × source_reliability.weight`；`source_weight` 缺省 1.0。
- `submit-audit` 按论文 source 聚合最近 `audit_window` 条样本，**按基准幂等重算**：
  `pass_rate >= audit_pass_min → 1.0`，否则 `max(0.5, 0.9)`；重复运行结果不变。
- `coverage` 输出 labels 全部 `use_types`（含 `background`）；空格的 `reclaimable`
  由 `low_priority` 论文的 screening 快照判定。
- `revive` 最多 `revival_max_rounds` 轮，优先级 `×revival_discount`，留痕于 `revival_log`：
  - `--gaps` / `--type+--section`：`low_priority → queued`（`--gaps` 自动跳过 `background` 空格）。
  - `--paper-id`：`discarded → candidate`（清旧票与合并快照以便重筛）或 `low_priority → queued`。

### 结构模板与图表（C1 / C3）

```bash
# C1 结构模板：style_sample 已确认卡优先；不足时回退到“目标 venue”的 fetched 论文
mine2digest templates extract
mine2digest templates extract --section intro --section method
# -> outputs/structure_templates.json
#    字段：target_section / source_paper_id / card_id / source_span / venue / confidence / provisional

# C3 图表：包装 PaperBanana CLI
mine2digest figures generate --input method.txt --caption "Pipeline overview" --fig-id fig-1
# -> outputs/figures/{fig_id}.png + .src.py + .manifest.json
```

- `templates extract`：每个 section 取最佳 `style_sample` 已确认卡（`provisional=false`）；
  无则用"目标 venue"（已确认 style_sample 卡里最常见的 venue）的 `fetched` 论文（`provisional=true`）。
  被 B 整体证伪（`status=rejected`）的来源不会被选。
- `figures generate` 调用 `paperbanana generate --input <f> --caption <c>`（可执行名由
  环境变量 `MINE2DIGEST_PAPERBANANA` 覆盖，可含参数），在临时目录运行后把 `*.png` /
  `*.src.py` 规范化重命名为 `{fig_id}.png` / `{fig_id}.src.py`，并写 `{fig_id}.manifest.json`。

### 逐节草稿与回溯校验（C4）

```bash
# 证据绑定骨架 + 打磨 prompt（默认同时产出）
mine2digest draft --section intro
# -> drafts/intro.md           每条 claim 带 [[card:<id>]]，列出 Gaps / Style Reference
# -> drafts/intro.prompt.md    给宿主 agent 的打磨指令 + 骨架

# 可选：直接调用外部命令生成成稿（stdin=prompt，stdout=Markdown）
mine2digest draft --section intro --polish
# 命令取 --polish-cmd 或 MINE2DIGEST_DRAFT_CMD，产物 drafts/intro.polished.md

# 回溯校验：所有 [[card:<id>]] 必须可解析
mine2digest verify --file drafts/intro.md
mine2digest verify --file drafts/intro.polished.md --require-refs
```

- `draft` 只读取 `templates` / `evidence_cards` / `papers`；模板源若已被证伪
  （`status=rejected`）会被跳过，文件缺失则 `template=null` 不阻断。
- 打磨措辞时**不得删除、改写或臆造** `[[card:<id>]]`；`Gaps` 不得编造内容填补。
- `verify` 默认查 DB：card 存在、未 rejected、论文未 rejected、`locator_status=verified`
  且 `match_ratio >= locator_fuzz_min`；`--recheck` 重跑页文本匹配（需 PDF）。
  有无效 ref 时 `{"ok": false, "reason": "invalid evidence_ref"}` 且退出码 1。
- `--require-refs` 会要求 `## Claims` 下每条顶层 bullet 都带 ref。

## 环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `MINE2DIGEST_DB` | DB 路径覆盖 | `db/mine2digest.sqlite` |
| `MINE2DIGEST_LABELS` | labels.yaml 路径覆盖 | `config/labels.yaml` |
| `OPENALEX_API_KEY` | OpenAlex 认证（脚本用，可选） | 无 |
| `S2_API_KEY` | Semantic Scholar（脚本用，可选） | 无 |
| `UNPAYWALL_EMAIL` | Unpaywall 联系邮箱（1c 用） | 无 |
| `MINE2DIGEST_PAPERBANANA` | PaperBanana 可执行（3 用，可含参数） | `paperbanana` |
| `MINE2DIGEST_DRAFT_CMD` | `draft --polish` 的外部命令（stdin→stdout） | 无 |
| `MINE2DIGEST_DRAFT_TIMEOUT` | 打磨命令超时秒数 | `300` |

## Live 烟测（可选，非产品主路径）

`scripts/live_e2e.py` 串 P0→P7，真跑 OpenAlex / S2 / Unpaywall 与外部 agent，产出
`LIVE_TEST_REPORT.md`。用于回归与边界验证，产品主路径仍是上面的 skill + CLI。

```bash
# 推荐 codex 后端（opencode 免费额度可能被账户级 403）
export MINE2DIGEST_AGENT_BACKEND=codex        # PowerShell: $env:MINE2DIGEST_AGENT_BACKEND="codex"
python scripts/live_e2e.py --fresh --phases 0,1,2,3,4,5,6,7
```

可调：`MINE2DIGEST_LIVE_AGENTS`（默认 `A,B`）、`MINE2DIGEST_LIVE_CANDIDATES`（默认 6）、
`MINE2DIGEST_LIVE_ATTEMPTS`（默认 3）。凭据只放 `config/.env`（已 gitignore）。

## 测试分层（尤其是 agent 调用侧）

agent 是外部进程，只能通过 CLI 交互，因此测的是**契约面**而非“agent 本身”：

| 文件 | 层 | 内容 |
|---|---|---|
| `test_merge.py` | 单元 | `merge_votes` 纯函数边界 |
| `test_screening_cli.py` | 契约 | 进程内 CLI 端到端 |
| `test_agent_contract.py` | 集成 | 伪 agent 以**子进程**跑 拉取→投票→tally，只读 stdout/写 stdin |
| `test_prompt_schema.py` | 一致性 | prompt 示例能被模型解析、字段无漂移、JSON Schema 稳定、黄金样本回归 |
| `test_reading.py` | 契约 | 阅读/建卡 + locator 硬门回滚 |
| `test_audit.py` | 契约 | 反证 / 审计抽样 / 权重更新 / 驳回 |
| `test_query.py` | 契约 | 覆盖矩阵 / 查询排序 / 回溯 / 导出 / 复活赛 |
| `test_templates.py` | 契约 | 结构模板：已确认优先 + venue 回退 provisional |
| `test_figures.py` | 契约 | PaperBanana 包装 + 产物规范化（注入 stub 可执行） |
| `test_agent_live.py` | 集成(可选) | 真调 LLM，校验输出过 schema |
| `test_tools_schema.py` | 一致性 | `docs/tools.json` 与 CLI 命令/参数同步 |
| `test_skill.py` | 一致性 | skill 覆盖全部命令/角色、资产存在、无密钥泄露 |

```bash
pytest -q                 # 全部（live 默认 skip）
pytest -m integration     # 仅需 MINE2DIGEST_AGENT_CMD 的真实 agent 冒烟
```

## 核心约定

- SQLite 是唯一真相源，CLI 是唯一写入口；agent 只读写 CLI 的 stdout JSON。
- 写操作一律 `BEGIN IMMEDIATE`（`db.tx()`）。
- 状态迁移只能经 `db.transition()`（内建状态机守卫）。
- 用途向量落在 `confirmed_uses` 表（`screening_merged` 只存投票合并快照）。

## 变更记录

### 2026-10-18

**修复：OpenReview fetcher 抓错 PDF**（`src/mine2digest/fetchers/openreview.py`）

- 现象：`fetchers/openreview.py` 按标题搜索后直接取第一条带 PDF 的笔记，不校验标题。
  搜索 API 返回近似题名时（如目标 “Conditional Prompt Learning for Vision-Language Models”
  的第一条是 “Multi-modal Mutual-Guidance Conditional Prompt Learning…”），会把**别的论文**
  当成本篇下载。
- 影响：证据卡 `reject_reason` 出现“fetched PDF 实为 <他文>…证据归属错误”。阅读 agent 能正确
  拦截（`stage=rejected`），但抓取层不应产生错误归属。
- 修复：新增 `titles_match()`（归一化 + `difflib.SequenceMatcher` 比率 `>= 0.9`）；
  `parse_pdf_url(payload, title=...)` / `try_fetch` 只接受标题匹配的笔记，否则返回
  `no openreview title match` 并交由后续 fetcher（arxiv/unpaywall）或干净失败。
- 测试：`tests/test_fetchers.py` 增补 4 个离线用例；全套 `156 passed, 1 skipped`。

**新增：工作区（workspace）模型 —— 产物不再写进安装目录**

- 新增 `mine2digest init` 在当前工作目录创建 `m2d/` 工作区（`db/papers/inbox/exports/drafts/outputs/logs/tmp/config`）
  并写入标记 `m2d/.mine2digest`；`init --workspace <dir>` 可指定目录。
- 解析优先级：`MINE2DIGEST_WORKSPACE` > 从 cwd 向上找到的带标记 `m2d/` > `<cwd>/m2d`。
  `MINE2DIGEST_DB` / `MINE2DIGEST_LABELS` 仍为最高优先覆盖。
- 所有数据/产物路径（DB、日志、papers、exports、drafts、outputs/figures、fail_list）
  改由 `common.workspace()/wpath()` 解析，落到工作区；只读资产（labels、角色提示词、
  schema、脚本）随包分发（`src/mine2digest/data/`）并从安装目录读取，缺失时回退到内置默认。
- skill 与 `scripts/install_skill.py` 同步：全局安装改为注入 `ASSETS=<安装目录>`，
  命令在**当前工作目录**运行（不再要求 `cd` 到仓库根）。
- 测试：新增 `tests/test_workspace.py`（默认 `m2d/`、标记向上查找、内置 labels 回退、
  `init` 骨架、导出落工作区、`--workspace`）；`conftest`/`live_e2e` 显式设工作区。
  全套 `163 passed, 1 skipped`。

**实测复核（skill 驱动的真实一轮）**

- 规模：183 种子 → 双 agent 各筛 50（A/B 一致，全 topic_relevant）→ 合并 45 fetch / 5 low_priority
  → 下载 20 成功 / 25 失败 → 23 张卡（18 confirmed / 5 rejected），locator 全部 `verified`
  → 审计 2/2 通过 → 导出 7 个视图。
- 结论：主链路可用；5 张 rejected 均源自上述 OpenReview 抓错 PDF，修复后不再发生。
- 遗留：本轮筛选上限 50，仍有 133 篇 `candidate` 未筛；25 篇 `fetch_failed`（多为无 OA 的
  IEEE/CVPR）需重试或 `ingest-manual`。
