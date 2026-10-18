# Live E2E Test Report

- topic: 基于 CLIP 的多模态泛化应用
- agent backend/model: `opencode/deepseek-v4.1-flash-free`
- workspace: `D:\project_learning\mine2digest\live`

| phase | step | status | detail |
|---|---|---|---|
| P0 | init | pass | schema=3 |
| P0 | harness-selftest | pass | {"ok": true, "n": 7} |
| P2 | agent-A | pass | accepted=2/2 rejected=0 errors=0 |
| P2 | agent-B | pass | accepted=2/2 rejected=0 errors=0 |
| P2 | tally-votes | pass | processed=2 fetch=0 low=2 discard=0 |
| P3 | fetch_worker | pass |  |
| P3 | pdf-integrity | fail | valid=0/0 |
| P4 | next-reading | warn | no fetched paper to read |
| P5 | next-caveat | warn | no confirmed card |
| P5 | audit | warn | sampled=0 but no valid auditor output |
| P6 | templates | pass | count=0 |
| P6 | figures | skip | paperbanana not installed |
| P7 | export | pass | files=7 |
| P7 | coverage | pass | sections=5 |
| P1 | openalex_seed | pass | 8 records |
| P1 | add-candidates | pass | inserted=8 updated=0 |
| P1 | s2_expand | pass | candidates=15 |
| P1 | add-relations | pass | upserted=15 |
