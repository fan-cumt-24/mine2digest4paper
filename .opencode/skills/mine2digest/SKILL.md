---
name: mine2digest
description: Use ONLY when the task involves the mine2digest repository or CLI — discover and dual-screen papers, fetch PDFs, extract evidence cards with locator checks, caveats/audit, coverage/query/export, draft/verify, templates and figures. Triggers on "mine2digest", "screen candidates", "evidence card", "coverage matrix", "literature digest". All artifacts land in a workspace folder (m2d/) created in the current working directory.
---

# mine2digest

把“文献检索 → 双 agent 筛选 → 取全文 → 证据卡 → 反证/审计 → 覆盖与导出”做成一条
**可审计、可回放**的流水线。本 skill 只描述契约与流程；确定性工作全部由 `mine2digest`
CLI 完成。

> 本文件是唯一全文。可移植入口见仓库根目录 `SKILL.md`；机器可读命令 schema 见
> `docs/tools.json`（由 `scripts/export_tools.py` 生成，勿手改）。

## 启动：先澄清需求（intake，必做）

**在跑任何数据命令之前**，先与用户把需求问清楚——除非用户已给出明确 brief，或明确说
“直接开始”。需求越模糊，越容易把 token 烧在错的方向上。

- 用宿主的提问能力（如 opencode 的 `question` 工具；没有就直接文字问），**一次只问 1–3 个
  最关键的**，逐步收敛，不要一口气堆一长串问题。
- 按需澄清（能省则省）：
  1. 研究主题 / 一句话目标；
  2. 研究问题或核心论点（想论证什么）；
  3. 目标节（`intro`/`related_work`/`method`/`experiments`/`discussion` 之一或多）；
  4. 文献范围：时间窗、venue/领域、语言、候选数量上限；
  5. 已有材料：BibTeX / DOI 列表 / 本地 PDF；
  6. 规模与预算：预计篇数，是否在意 token 与耗时；
  7. 交付物：只要证据卡/覆盖矩阵，还是要逐节草稿、（可选）图表；
  8. 是否允许放宽阈值（默认**不放宽**）。
- 把答案写入/更新**工作区 `config/experiment_brief.md`**（若还没 `init`，先记下，P0 建好
  工作区后立即落盘）。brief 是筛选的唯一依据。
- 给用户回述一版「我的理解 + 裁剪后的计划（P1–P8 取哪几步）与预期产物」，**确认后再进入 P0**。
- 用户催“随便跑”而需求仍不清时：点明风险（浪费 token、抓错文献、覆盖缺口），并给一个
  最小默认方案让用户点头，而不是硬跑。

## 工作区与资产（先读）

分两类路径，**不要混用**：

- **工作区 = 数据 + 产物**，默认 `<当前工作目录>/m2d/`。`mine2digest init` 会在当前
  工作目录创建它，并写入标记 `m2d/.mine2digest`。此后 `db/`、`papers/`、`inbox/`、
  `exports/`、`drafts/`、`outputs/`、`logs/`、`tmp/` 全部落在工作区内。**绝不把产物
  写进安装目录或 skill 目录。**
- **资产 = 安装目录（只读）**：`config/labels.yaml`、`config/*_prompts/`、`config/.env`、
  `docs/tools.json`、`scripts/`。全局安装时安装器注入 `ASSETS=<安装目录>`；下文以
  `<ASSETS>/...` 指代这些只读文件。

工作区解析优先级：`$env:MINE2DIGEST_WORKSPACE` > 从 cwd 向上找到的带标记 `m2d/` >
`<cwd>/m2d`。因此在项目根或其任意子目录运行命令都能命中同一工作区。覆盖：
`MINE2DIGEST_DB`（DB 路径）、`MINE2DIGEST_LABELS`（labels 路径）。

## 安装 / 位置

- 项目内：本文件（opencode 自动加载）。
- 全局：`python <ASSETS>/scripts/install_skill.py` 生成到
  `~/.config/opencode/skills/mine2digest/SKILL.md`，并注入 `ASSETS`。改后重启 opencode。

## 何时使用 / 不适用

使用：需要批量筛选论文、为论文各节抽取可引用的证据卡、发现覆盖缺口、导出写作材料。
不使用：单篇问答、与文献流水线无关的代码任务、需要自由编排的自治 agent 场景。

## 铁律（不可违反）

1. **SQLite 是唯一真相源，CLI 是唯一写入口。** 绝不直接改 DB；一切读写走 `mine2digest`。
2. **产物只落工作区 `m2d/`**，不得写进安装目录/skill 目录；先把工作区建好再跑数据命令。
3. **统一契约**：输入 JSON（stdin 或 `--file`），输出单个 JSON；成功 `{"ok": true, ...}`，
   失败 `{"ok": false, "reason": "..."}` 且退出码 1。失败时读工作区 `logs/cli.jsonl` 与
   `mine2digest lookup --paper-id <id>` 排查。
4. **`source_span` 必须逐字来自页文本**，禁止改写/翻译/拼接；locator 用
   `rapidfuzz.partial_ratio >= locator_fuzz_min`（默认 75）校验，**任一卡失败整批回滚**。
5. **不得擅自修改阈值**（`<ASSETS>/config/labels.yaml` 的 `thresholds`）。需要放宽只能人工改并说明。
6. **密钥只在 `<ASSETS>/config/.env`**（如 `UNPAYWALL_EMAIL`），绝不写进命令参数、日志、报告、技能。
7. **主 agent 只派发与提交，不代替角色投票/阅读**；A 的结论不得进入 B 的上下文。
8. **先澄清需求再执行**（见“启动：先澄清需求”）；brief 落盘并确认后才进入 P0。

## 资产与前置

| 资产 | 作用 |
|---|---|
| `<ASSETS>/config/labels.yaml` | 枚举（use_types/target_sections）、权重、阈值（只读） |
| 工作区 `config/experiment_brief.md` | 研究主题/问题/论点，筛选依据（`init` 生成，人可编辑） |
| `<ASSETS>/config/screening_prompts/agent_a.md`、`agent_b.md` | 双筛选角色提示词 |
| `<ASSETS>/config/reading_prompts/reader.md`、`caveat.md`、`auditor.md` | 阅读/反证/审计提示词 |
| `<ASSETS>/docs/tools.json` | 每个命令的入参 schema 与 stdin schema |

前置：`pip install -e ".[all]"`；在**当前工作目录**执行 `mine2digest init`。若
`mine2digest` 不在 PATH，用 `python -m mine2digest.cli`。环境变量：`MINE2DIGEST_WORKSPACE`、
`MINE2DIGEST_DB`、`MINE2DIGEST_LABELS`、`UNPAYWALL_EMAIL`、`MINE2DIGEST_PAPERBANANA`（详见 README）。

## 状态机

```
candidate → screened → queued → fetching → fetched → confirming → confirmed / rejected
     └→ low_priority / discarded            fetched → (失败) fetch_failed → queued
low_priority → queued / candidate / fetched        discarded → candidate
```
状态迁移只能经 `db.transition()`；不要绕过 CLI 改状态。

## 多 agent 角色与独立性

| 角色 | 独立性 | 产物 | 提交命令 |
|---|---|---|---|
| Screener A（PICO） | **必须**，与 B 双盲 | 每篇一个 `Vote` | `submit-vote --agent-id A` |
| Screener B（规则表） | **必须**，与 A 双盲 | 每篇一个 `Vote` | `submit-vote --agent-id B` |
| Reader | 每篇独立（可并行） | `CardFile` | `add-card` |
| Caveat | 每卡独立 | `Caveat` | `add-caveat --card-id <id>` |
| Auditor | **与 Reader 隔离** | `AuditFile` | `submit-audit` |

**派发协议（opencode / 支持 subagent 的宿主）**

1. 主 agent 先跑数据命令拿到载荷，写入工作区 `tmp/`，例如
   `tmp/screen_A.json`、`tmp/read_<paper_id>.json`。
2. 用 `task(subagent_type="general")` 每个角色一个任务，prompt 包含：
   - 角色提示词文件绝对路径（`<ASSETS>/config/...`）+ 载荷文件路径；
   - “只输出请求的 JSON，不使用工具改文件”；
   - 明确禁止跨角色共享：A 任务不得包含 B 的输出；auditor 任务只给卡 + 页文本。
3. 子任务返回 JSON 后，**主 agent 负责写临时文件并调用对应 CLI**（唯一写入者）。
4. A/B 可在同一轮并行派发，但 prompt 彼此独立；`tally-votes` 会要求双方都投过票。

**无 subagent 的兜底**：起两个独立进程（`opencode run` / `codex exec`）分别执行 A、B，
进程边界即隔离。绝不“先 A 后 B”在同一上下文里连续扮演。

## 主流程（P0–P8）

**P0 建工作区**（intake 已完成或用户已给 brief）
```bash
mine2digest init            # 在当前工作目录创建 m2d/ 并初始化 DB
# 随后把 intake 答案写入/更新 m2d/config/experiment_brief.md
```

**P1 发现**
```bash
python <ASSETS>/scripts/openalex_seed.py --query "CLIP multimodal generalization" --limit 50 --out tmp/seeds.jsonl
mine2digest add-candidates --source openalex --file tmp/seeds.jsonl
python <ASSETS>/scripts/s2_expand.py --seed DOI:... --direction references --limit 50 --out-prefix tmp/s2
mine2digest add-candidates --source s2 --file tmp/s2.candidates.jsonl
mine2digest add-relations --file tmp/s2.relations.jsonl
```
检索词用**英文**命中率更高；中文主题写在 `<工作区>/config/experiment_brief.md`。

**P2 双 agent 筛选**
```bash
mine2digest next-candidates --agent-id A --limit 50   # -> 写 tmp/screen_A.json
mine2digest next-candidates --agent-id B --limit 50   # -> 写 tmp/screen_B.json
# 各派一个独立子任务，产出 Vote JSON -> tmp/votes_A.json / tmp/votes_B.json
mine2digest submit-vote --agent-id A --file tmp/votes_A.json
mine2digest submit-vote --agent-id B --file tmp/votes_B.json
mine2digest tally-votes
```
`Vote` 字段：`paper_id`、`topic_relevant`、`uses[]`（`type` 必须在 `labels.yaml` 的
`use_types` 内、`target_section` 在 `target_sections` 内、`confidence∈[0,1]`；
同一 `(type,target_section)` 不得重复）。

**P3 取全文**
```bash
python <ASSETS>/scripts/fetch_worker.py --limit 5 --rounds 3 --worker w1
# 或手动：next-fetch -> 下载 -> report-fetch；闭源 PDF 放工作区 inbox/ 后 ingest-manual
```
成功归档 `<工作区>/papers/{slug}/paper.pdf` + `meta.json`；失败汇总 `<工作区>/exports/fail_list.json`。

**P4 阅读建卡**（载荷离线化，省 token）
```bash
mine2digest next-reading --limit 5 --out-dir tmp   # 每篇写 tmp/read-<paper_id>.json，只回清单
# 每篇一个 reader 子任务：让子任务自行 Read tmp/read-<paper_id>.json，主 agent 不要读页文本
mine2digest add-card --file tmp/cards_<paper_id>.json
```
`next-reading` 用确定性词面检索**预选页**并限字符数（`reader_top_k`/`reader_char_budget`，
可 `--top-k/--char-budget` 覆盖）；**locator 仍对全文校验**，只是模型看到的更少。
`add-card` 事务内逐条插入并校验 locator；失败整批回滚。

**P5 反证与审计**（同样离线化，页文本按论文去重）
```bash
mine2digest next-caveat --limit 5 --out-dir tmp    # 每篇写 tmp/caveat-<paper_id>.json（含 pages + cards）
mine2digest add-caveat --card-id <id> --file tmp/caveat_<id>.json
mine2digest audit --rate 0.1              # -> 每张抽样卡一个 auditor 子任务（隔离）
mine2digest submit-audit --file tmp/audit.json
mine2digest reject-card --card-id <id> --reason "..."
```
反证页预算 `caveat_top_k`/`caveat_char_budget`，优先并入该卡 `evidence_locator` 命中的页。
`submit-audit` 按 source 聚合最近 `audit_window` 条样本，**按基准幂等重算**权重。

**P6 模板与图表**
```bash
mine2digest templates extract                      # -> <工作区>/outputs/structure_templates.json
mine2digest figures generate --input method.txt --caption "..." --fig-id fig-1
```
`figures generate` 包装 PaperBanana（`MINE2DIGEST_PAPERBANANA` 可覆盖可执行）。

**P7 覆盖、查询、导出、复活**
```bash
mine2digest coverage                       # section × type；空格标 reclaimable
mine2digest query --type baseline --section experiments --min-confidence 0.5
mine2digest export                         # -> <工作区>/exports/（--view 单个）
mine2digest revive --gaps                  # low_priority → queued（跳过 background 空格）
mine2digest revive --paper-id <id>         # discarded → candidate / low_priority → queued
```

**P8 草稿与回溯校验**
```bash
mine2digest draft --section intro          # -> <工作区>/drafts/intro.md + drafts/intro.prompt.md
# 默认：主 agent 用 task(general) 读 .prompt.md，产出 drafts/intro.polished.md
# 或：mine2digest draft --section intro --polish   # 需配置 MINE2DIGEST_DRAFT_CMD
mine2digest verify --file drafts/intro.polished.md --require-refs
```
打磨措辞**不得删除、改写或臆造** `[[card:<id>]]`；`Gaps` 不得编造内容填补；
`verify` 通过才可交付（无效 ref → `ok:false` 且 exit 1）。

## 命令索引

`init`、`add-candidates`、`add-relations`、`next-candidates`、`submit-vote`、
`tally-votes`、`next-fetch`、`report-fetch`、`ingest-manual`、`next-reading`、
`add-card`、`verify-card`、`next-caveat`、`add-caveat`、`audit`、`submit-audit`、
`reject-card`、`coverage`、`query`、`lookup`、`export`、`revive`、
`templates extract`、`figures generate`、`draft`、`verify`。

精确入参、required、stdin schema 以 `<ASSETS>/docs/tools.json` 为准。

## 失败处理

- `{"ok": false, "reason": ...}`：先看 `reason`，再查工作区 `logs/cli.jsonl` 与
  `mine2digest lookup --paper-id <id>`。
- `add-card` 回滚：某张卡 locator 未过；缩小 `source_span` 到逐字原文后重试。
- 枚举/字段错误：对照 `<ASSETS>/config/labels.yaml` 与 `docs/tools.json` 的 stdin schema。
- 网络限流（OpenAlex/S2 429）：脚本内已退避重试；仍失败则稍后重跑，勿改阈值。

## 资产自检

运行前确认：`<ASSETS>/config/labels.yaml`、五个角色提示词、`docs/tools.json` 均存在；
工作区 `config/experiment_brief.md` 已按主题填写。若 schema 过期：
```bash
python <ASSETS>/scripts/export_tools.py          # 重新生成
python <ASSETS>/scripts/export_tools.py --check  # 校验是否最新
```

## 注意

- 产物一律留在工作区；不要把运行目录 `Set-Location` 到安装目录。
- 修改本 skill 或 `.opencode` 下任何文件后，需**重启 opencode** 才会生效。
