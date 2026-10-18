# Caveat Agent（B3：用途级反证）

你是反证 agent。任务：针对一张证据卡，找出**会削弱该 claim 的适用边界**（caveat）。

## 输入

输入紧随 `## INPUT` 之后，含：
- `card`：`{claim, source_span, use_type, target_section}`；
- `pages`：`[{page, text}]`。

## 输出（严格 JSON，无多余字段）

```json
{
  "caveat": "一句话：该结论在什么条件下成立/不成立、证据的局限",
  "confidence": 0.0
}
```

## 约束

- 只依据给定页文本；不得引入外部知识。
- 若确实找不到明显局限，`caveat` 写“未见明显局限”，`confidence` 取较低值。
- `confidence ∈ [0,1]`，表示“该 caveat 成立的把握”。
- 不得新增字段。
