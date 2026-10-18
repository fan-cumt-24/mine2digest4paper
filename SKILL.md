---
name: mine2digest
description: Use when running the mine2digest literature pipeline — dual-agent screening, PDF fetch, evidence cards with locator checks, caveats/audit, coverage/query/export. Portable entry point; full workflow lives in .opencode/skills/mine2digest/SKILL.md.
---

# mine2digest（可移植入口）

完整工作流在 [`.opencode/skills/mine2digest/SKILL.md`](.opencode/skills/mine2digest/SKILL.md)，
命令 schema 在 [`docs/tools.json`](docs/tools.json)。运行前先读完整版。

快速规则：

- SQLite 是唯一真相源，`mine2digest` CLI 是唯一写入口；不要直接改 DB。
- 统一契约：JSON in / JSON out；成功 `{"ok": true, ...}`，失败 `{"ok": false, "reason": ...}` 且退出码 1。
- 筛选必须由 **两个独立上下文** 分别执行 A（PICO）与 B（规则表），互不共享中间结论。
- `source_span` 必须逐字来自页文本；locator 默认 `partial_ratio >= 75`，任一卡失败整批回滚。
- 阈值只在 `config/labels.yaml`；密钥固化在 skill 目录的 `.env`（CLI 自动加载），不得写入工作区/日志/命令参数。

最小流程：

```bash
mine2digest init
python scripts/openalex_seed.py --query "CLIP multimodal generalization" --out seeds.jsonl
mine2digest add-candidates --source openalex --file seeds.jsonl
mine2digest next-candidates --agent-id A --limit 50   # 各自独立子任务投票
mine2digest submit-vote --agent-id A --file tmp/votes_A.json
mine2digest submit-vote --agent-id B --file tmp/votes_B.json
mine2digest tally-votes
python scripts/fetch_worker.py --limit 5 --rounds 3
mine2digest next-reading --limit 5 && mine2digest add-card --file tmp/cards.json
mine2digest coverage && mine2digest export
mine2digest draft --section intro            # 证据绑定草稿 + 打磨 prompt
mine2digest verify --file drafts/intro.md    # 校验 [[card:<id>]] 回溯有效性
```

修改本文件或 `.opencode` 下文件后需重启 opencode。
