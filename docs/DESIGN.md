# mine2digest 设计说明

本文是原 `交接.txt` 的整理版，描述系统定位、核心决策与实现契约。
使用方式见 [`README.md`](../README.md)，完整工作流见 [`SKILL.md`](../.opencode/skills/mine2digest/SKILL.md)。

## 定位

**批量半自动文献检索 + 论文写作材料抽取**：从 mining 到 digest，输出可直接支撑写作的
结构化材料（证据卡、覆盖矩阵、结构模板、图表设计建议、逐节草稿）。

产品形态是 **agent skill + CLI**，不是自治 agent，也不依赖 LangChain。

## 五条设计原则

1. **无人工校准**。唯一人工动作是把自动获取失败的闭源 PDF 放进 `inbox/`；
   其余判断由规则 + 双 agent 完成。
2. **用途向量，不是相关性标量**。每篇论文的产出是「在论文哪一节、以什么角色被消费」，
   而不是「相关/不相关」。
3. **可追溯的丢弃**。被丢弃或降级的论文保留元信息与投票日志，写作阶段可按 `paper_id` 回溯。
4. **SQLite 是唯一真相源，CLI 是唯一写入口**。agent 只通过 `mine2digest <command>` 读写，
   stdin/`--file` 进，stdout 出 JSON。
5. **A/B 并行，C 部分并行**。A 流式产出 `fetched`，B 流式消费；C1/C3 可与 B 并行，
   C2/C4 必须等证据卡基本齐。

## 流程与并行

```text
A1 种子发现（OpenAlex）
   ↓
A2 引用扩展（Semantic Scholar 一跳，只留 influential/methodology/result）
   ↓
A3 双 agent 用途分档（A 与 B 独立投票 → merge_votes 合并）
   ↓
A4 自动获取（Unpaywall → arXiv → OpenReview → PMC）
   │
   ├── 成功 → papers/{slug}/paper.pdf → status=fetched ──┐
   └── 失败 → fail_list.json → 人工放 inbox/ → ingest-manual
                                                          ↓
B1 全文阅读（多篇并发）→ 用途级证据卡
   ↓
B2 locator 自动校验（同步，失败整批回滚）—— 硬门
   ↓
B3 反证 agent（用途级追问 caveat）
   ↓
B4 审计 agent（抽样，只标记不阻断，内化为 source_reliability 权重）
   ↓
C1 结构模板 ──┐（可与 B 并行，provisional 标记）
C3 图表建议 ──┤（可与 B 并行，不依赖 B）
C2 覆盖矩阵 ──┘（等 B 的卡基本齐；空格回溯 low_priority）
   ↓
C4 逐节草稿 + evidence_ref 回溯校验（节间并行，节内先绑定证据再打磨）
```

**背压**：`next-fetch` 只取 `queued`，`next-reading` 只取 `fetched`。B 拉不到就空转等待，
A 产出后自动被消费，不需要消息队列或调度器。

## 用途标签体系

枚举、权重、阈值全部集中在 `config/labels.yaml`（人编辑，agent 只读）。

- `use_types`：`support_claim`、`baseline`、`method_borrow`、`experiment_design`、
  `gap_evidence`、`contradict`、`style_sample`、`background`
- `target_sections`：`intro`、`related_work`、`method`、`experiments`、`discussion`
- `priority_weights`：fetch 队列排序 = 确认用途类型权重之和
- `thresholds`：投票阈值、locator 阈值、审计比例/窗口、复活赛参数、页文本预算

投票 schema：

```json
{
  "paper_id": "...",
  "topic_relevant": true,
  "uses": [
    {
      "type": "baseline",
      "target_section": "experiments",
      "anchor": "这篇为我的论文做什么",
      "confidence": 0.8
    }
  ]
}
```

硬约束：`type` / `target_section` 必须在枚举内，`submit-vote` 时校验，非法直接拒绝；
同一 `(type, target_section)` 不重复。

## 双 agent 合并（`merge.py`）

| 情况 | 动作 |
|---|---|
| 双方都有该用途，confidence 均 ≥ `vote_confidence_min` | 确认，`needs_fulltext_confirm=false` |
| 双方都有，一高一低（低者 < 阈值） | 保留高置信版本，`needs_fulltext_confirm=true`，仍确认 |
| 双方都有但都低于阈值 | 进 `disagreement` |
| 仅一方标注 | 进 `disagreement` |
| 存在非 `background` 的确认用途 | `decision=fetch`，`priority=Σ weights` |
| 仅有 `background` 确认，或存在 disagreement，或任一方 `topic_relevant` | `low_priority` |
| 双方都无用途且都 `topic_relevant=false` | `discard` |

`merge_votes` 是纯函数，所有判断集中在此，便于单测。

## 数据模型

SQLite（WAL），主要表：

- `papers`：`paper_id/doi/title/abstract/year/venue/source/status/revival_rounds`
- `paper_ids`：跨源去重键（`doi` → `openalex` → `s2` → `arxiv` → `title` 的 sha1）
- `relations`：引用边（`is_influential`、`contexts`）
- `screening_votes`：每 agent 每篇一行（`UNIQUE(paper_id, agent_id)`）
- `screening_merged`：合并快照（`decision`、`priority`、`disagreement`）
- `confirmed_uses`：规范化的用途向量（覆盖矩阵直接 join）
- `fetch_queue` / `fetch_attempts`：认领与获取尝试
- `evidence_cards`：证据卡（`stage`、`locator_status`、`reject_reason`）
- `locator_checks`：`match_ratio` 与命中文本
- `audit_samples` / `source_reliability`：审计与来源权重
- `state_transitions` / `revival_log`：状态与复活留痕

状态机（只能经 `db.transition()`）：

```text
candidate → screened → queued → fetching → fetched → confirming → confirmed / rejected
     └→ low_priority / discarded      fetching → fetch_failed → queued
low_priority → queued / candidate / fetched        discarded → candidate
```

关键索引：`papers(status)`、`screening_merged(decision, priority DESC)`、
`evidence_cards(target_section, use_type, stage)`、`state_transitions(paper_id, at)`。

## CLI 契约

输入 JSON（stdin 或 `--file`），输出单个 JSON；成功 `{"ok": true, ...}`，
失败 `{"ok": false, "reason": "..."}` 且退出码 1。每次调用追加 `logs/cli.jsonl`。
精确 schema 见 [`docs/tools.json`](tools.json)（由 `scripts/export_tools.py` 生成）。

| 阶段 | 命令 |
|---|---|
| 工作区 | `init` |
| A 发现 | `add-candidates`、`add-relations` |
| A 筛选 | `next-candidates`、`submit-vote`、`tally-votes` |
| A 获取 | `next-fetch`、`report-fetch`、`ingest-manual` |
| B 阅读 | `next-reading`、`add-card`、`verify-card` |
| B 反证/审计 | `next-caveat`、`add-caveat`、`audit`、`submit-audit`、`reject-card` |
| C 输出 | `coverage`、`query`、`lookup`、`export`、`revive` |
| C 模板/图表 | `templates extract`、`figures extract`、`figures suggest`、`figures add-notes`、`figures verify` |
| C 写作 | `draft`、`verify` |
| 维护 | `clean` |

## 并发与一致性

- 写路径一律 `BEGIN IMMEDIATE`（`db.tx()`）；读路径不包事务。
- 双 agent 同时投票：`UNIQUE(paper_id, agent_id)` 保证各一行；`tally-votes` 只处理双方都投过的。
- 多论文并行 `add-card`：每篇一个事务，locator 校验在事务内，失败回滚整批。
- fetch 认领：`UPDATE ... RETURNING` 原子认领，`claimed_at/claimed_by` 留痕。
- 崩溃恢复：WAL + `papers.status` 状态机，按状态补跑对应 `next-*` 即可。
- 页文本缓存：`papers/{slug}/extracted/page-{n}.txt`，首次提取后复用，不重解 PDF。

## 工作区

产物与数据全部在 `<cwd>/m2d/`（`init` 创建并写标记 `.mine2digest`）；
只读资产（labels、角色提示词、schema、脚本）来自安装目录。
解析优先级：`MINE2DIGEST_WORKSPACE` > 向上查找带标记的 `m2d/` > `<cwd>/m2d`。

## 关键实现细节

- `add-candidates`：去重键 `doi` → `openalex_id` → `s2_id` → `sha1(title.lower())`；
  已存在记录只更新 `updated_at`，不覆盖 `status`。
- fetcher 统一接口 `try_fetch(paper) -> (success, path_or_url, error)`；OpenReview 要求
  标题相似度 ≥ 0.9，避免抓到同名/近似的错误 PDF。
- `extract.py`：PyMuPDF 按页提取并缓存；`locator.py` 解析 `"p.7, Table 3"` 定位页码，
  失败则全文扫描，`partial_ratio >= locator_fuzz_min` 通过并记录 `match_ratio`。
- `add-card`：枚举校验 → 逐条插入 → 同步 locator 校验 → 失败 `ROLLBACK`。
- `next-reading` / `next-caveat`：确定性词面检索预选页 + 字符预算（`reader_*` / `caveat_*`），
  locator 仍对全文校验；`--out-dir` 把载荷离线化，编排端不再读全文。
- `audit`：随机抽样；`submit-audit` 按论文 source 聚合最近 `audit_window` 条样本，
  按基准幂等重算权重（`pass_rate >= audit_pass_min → 1.0`，否则 `max(0.5, 0.9)`）。
- `coverage`：用 labels 枚举补全空格（`n=0` 显式输出），空格查 `low_priority` 标 `reclaimable`。
- `templates extract`：`style_sample` 已确认优先；不足时回退到目标 venue 的 `fetched` 论文
  （`provisional=true`）；来源论文 `status=rejected` 则跳过。
- `figures`：抽取图注/表注 + 引用上下文，按相关度给设计建议；只允许引用 `[fig:<ref>]`，
  `figures verify` 校验引用存在。
- `draft`：每条 claim 绑定 `[[card:<id>]]`；`verify` 校验 card 存在、未 rejected、
  论文未 rejected、`locator_status=verified` 且 `match_ratio >= locator_fuzz_min`。

## 环境变量

| 变量 | 用途 |
|---|---|
| `MINE2DIGEST_WORKSPACE` / `MINE2DIGEST_DB` / `MINE2DIGEST_LABELS` | 路径覆盖 |
| `MINE2DIGEST_ENV` | `.env` 文件路径覆盖 |
| `OPENALEX_API_KEY` / `S2_API_KEY` | 可选，提高限额 |
| `UNPAYWALL_EMAIL` | Unpaywall 必填 |
| `MINE2DIGEST_DRAFT_CMD` / `MINE2DIGEST_FIGURE_CMD` | `--polish` 外部命令 |

密钥固化在 skill 目录的 `.env`（CLI/脚本自动加载），不进入工作区、日志或 brief。

## 历史决策

- `agent_ids` 已移入 `config/labels.yaml` 并由 `submit-vote` 校验，避免 A/B 标识漂移。
- 原 `figures generate`（PaperBanana 包装）已替换为「图表语料 + 设计建议」，移除外部二进制依赖。
- 产物从安装目录迁移到工作区模型，安装目录只保留只读资产。
- 阅读/反证载荷离线化 + 页预选，单篇阅读输入从 ~52k 字符降到 ≤12k。

## 待办

- `live_e2e.py` 仍是回归工具而非产品主路径；报告见 `LIVE_TEST_REPORT.md`。
- 无 OA 的闭源论文（IEEE/CVPR 等）需重试或 `ingest-manual`。
