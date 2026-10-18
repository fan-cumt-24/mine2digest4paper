"""Live 端到端测试编排（P0→P7）。

真跑网络（OpenAlex / S2 / Unpaywall）+ 真 LLM（经 scripts/agent_cli.py）。
全部产物落在独立工作区 live/，不污染仓库运行目录。

安全：邮箱等密钥只从环境变量读取，绝不写入命令行参数或报告。
用法：
    python scripts/live_e2e.py            # 默认工作区 live/
    python scripts/live_e2e.py --fresh    # 先清空工作区再跑
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
LABELS = REPO / "config" / "labels.yaml"
PROMPTS = REPO / "config" / "screening_prompts"
READ_PROMPTS = REPO / "config" / "reading_prompts"
FIXTURES = REPO / "tests" / "fixtures"

TOPIC = "基于 CLIP 的多模态泛化应用"
SEED_QUERY = "CLIP multimodal generalization"
OPENALEX_LIMIT = 8
FETCH_LIMIT = 3
AUDIT_RATE = 0.5
AUDIT_MAX = 2
AGENT_TIMEOUT = 300

WS = REPO / "live"
STATE = WS / "report_state.json"


# ---------------------------------------------------------------- env / report


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_credentials() -> None:
    """密钥固化在 skill 目录 .env；再回退旧位置 config/.env。"""
    sys.path.insert(0, str(SRC))
    from mine2digest import common

    if common.load_env_file() is None:
    load_credentials()


def redact(text: str) -> str:
    secret = os.environ.get("UNPAYWALL_EMAIL")
    if secret:
        text = text.replace(secret, "***")
    return text


def base_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["MINE2DIGEST_LABELS"] = os.environ.get("MINE2DIGEST_LABELS", str(LABELS))
    env["MINE2DIGEST_WORKSPACE"] = os.environ.get("MINE2DIGEST_WORKSPACE", str(WS))
    return env


LIVE_CONF_MIN = 0.35


def prepare_labels() -> Path:
    """live 专用 labels 覆盖：仅下调 vote_confidence_min，保证烟测能走到 fetch/read。

    生产 `config/labels.yaml` 不变；覆盖文件放在独立工作区 `live/labels.yaml`。
    """
    import yaml

    cfg = yaml.safe_load(LABELS.read_text(encoding="utf-8"))
    cfg.setdefault("thresholds", {})["vote_confidence_min"] = LIVE_CONF_MIN
    out = WS / "labels.yaml"
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.environ["MINE2DIGEST_LABELS"] = str(out)
    return out


# ---------------------------------------------------------------- subprocess


def cli(args: list[str], input_text: str | None = None) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-m", "mine2digest.cli", *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=base_env(),
        cwd=str(WS),
    )
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError:
        body = {"ok": False, "reason": "non-json stdout", "raw": proc.stdout[:400]}
    return proc.returncode, body


def run_script(name: str, args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / name), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=base_env(),
        cwd=str(WS),
    )
    return proc.returncode, proc.stdout, proc.stderr


def agent(prompt: str, timeout: int = AGENT_TIMEOUT) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "agent_cli.py")],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=base_env(),
        cwd=str(WS),
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------- parsing


def parse_json_loose(text: str):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty agent output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in agent output")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced JSON in agent output")


def load_prompt(path: Path, payload) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2) if not isinstance(payload, str) else payload
    return path.read_text(encoding="utf-8") + "\n\n## INPUT\n" + body + "\n"


# ---------------------------------------------------------------- report state

REPORT: list[dict] = []


def record(phase: str, name: str, status: str, detail: str = "", **extra) -> None:
    REPORT.append({"phase": phase, "name": name, "status": status, "detail": detail, **extra})
    print(f"[{phase}] {name}: {status} {detail}"[:300])


def _load_state() -> list[dict]:
    if not STATE.exists():
        return []
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def write_report(model: str) -> Path:
    seen: dict[tuple[str, str], int] = {}
    for i, r in enumerate(REPORT):
        seen[(r["phase"], r["name"])] = i
    rows = [r for i, r in enumerate(REPORT) if seen[(r["phase"], r["name"])] == i]

    lines = [
        "# Live E2E Test Report",
        "",
        f"- topic: {TOPIC}",
        f"- agent backend/model: `{model}`",
        f"- workspace: `{WS}`",
        "",
        "| phase | step | status | detail |",
        "|---|---|---|---|",
    ]
    for r in rows:
        detail = redact(str(r.get("detail", ""))).replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| {r['phase']} | {r['name']} | {r['status']} | {detail} |")
    out = REPO / "LIVE_TEST_REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    STATE.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------- phases


def phase0() -> None:
    WS.mkdir(parents=True, exist_ok=True)
    code, body = cli(["init"])
    record("P0", "init", "pass" if body.get("ok") else "fail", f"schema={body.get('schema_version')}")

    prompt = 'Output ONLY this JSON, no prose, no fences: {"ok": true, "n": 7}'
    rc, out, err = agent(prompt)
    ok = rc == 0 and '"ok": true' in out and '"n": 7' in out
    record("P0", "harness-selftest", "pass" if ok else "fail", redact(out.strip())[:120])


def phase1() -> None:
    seeds = WS / "seeds.jsonl"
    rc, out, err = run_script("openalex_seed.py", ["--query", SEED_QUERY, "--limit", str(OPENALEX_LIMIT), "--out", str(seeds)])
    if rc != 0:
        record("P1", "openalex_seed", "fail", redact(err.strip())[:200])
        return
    count = len([ln for ln in seeds.read_text(encoding="utf-8").splitlines() if ln.strip()])
    record("P1", "openalex_seed", "pass" if count else "fail", f"{count} records")
    if not count:
        return

    code, body = cli(["add-candidates", "--source", "openalex", "--file", str(seeds)])
    record("P1", "add-candidates", "pass" if body.get("ok") else "fail",
           f"inserted={body.get('inserted')} updated={body.get('updated')}")
    shutil.copyfile(seeds, FIXTURES / "live_openalex_seeds.jsonl")

    rc, out, err = run_script("s2_expand.py", ["--seed", "DOI:10.1145/3292500.3330701", "--direction", "references",
                                               "--limit", "20", "--out-prefix", str(WS / "s2")])
    if rc != 0:
        status = "warn" if "429" in err else "fail"
        record("P1", "s2_expand", status, "rate-limited (keyless S2)" if status == "warn" else redact(err.strip())[:200])
        return
    cand = WS / "s2.candidates.jsonl"
    rel = WS / "s2.relations.jsonl"
    n_cand = len([x for x in cand.read_text(encoding="utf-8").splitlines() if x.strip()]) if cand.exists() else 0
    record("P1", "s2_expand", "pass", f"candidates={n_cand}")
    if cand.exists():
        cli(["add-candidates", "--source", "s2", "--file", str(cand)])
    if rel.exists():
        code, body = cli(["add-relations", "--file", str(rel)])
        record("P1", "add-relations", "pass" if body.get("ok") else "fail", f"upserted={body.get('upserted')}")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _live_agents() -> list[str]:
    raw = os.environ.get("MINE2DIGEST_LIVE_AGENTS", "A,B")
    return [a.strip().upper() for a in raw.split(",") if a.strip()]


def _ask_vote(cand: dict, prompt_file: Path, attempts: int | None = None):
    """向 agent 要一条 Vote；模型偶发空输出/限流时退避重试。"""
    import time

    if attempts is None:
        attempts = _env_int("MINE2DIGEST_LIVE_ATTEMPTS", 3)
    last = "unknown"
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 * attempt)
        rc, out, err = agent(load_prompt(prompt_file, cand))
        if rc != 0:
            last = redact(err.strip())[:120] or f"exit {rc}"
            continue
        try:
            return parse_json_loose(out), None
        except Exception as exc:  # noqa: BLE001
            last = f"{exc}; raw={redact(out)[:120]}"
    return None, last


def _vote_for_agent(agent_id: str, prompt_file: Path) -> None:
    limit = _env_int("MINE2DIGEST_LIVE_CANDIDATES", 6)
    code, nc = cli(["next-candidates", "--agent-id", agent_id, "--limit", str(limit)])
    candidates = nc.get("candidates", []) if nc.get("ok") else []
    if not candidates:
        record("P2", f"agent-{agent_id}", "fail", "no candidates")
        return
    accepted, rejected, bad = [], [], []
    for cand in candidates:
        obj, err = _ask_vote(cand, prompt_file)
        if obj is None:
            bad.append({"paper_id": cand["paper_id"], "error": err})
            continue
        code, sv = cli(["submit-vote", "--agent-id", agent_id], input_text=json.dumps({"votes": [obj]}, ensure_ascii=False))
        if sv.get("ok") and sv.get("accepted"):
            accepted.append(obj)
        else:
            rejected.append({"paper_id": cand["paper_id"], "reason": redact(str(sv.get("reason", "")))[:120]})
    status = "pass" if accepted else "fail"
    record("P2", f"agent-{agent_id}", status,
           f"accepted={len(accepted)}/{len(candidates)} rejected={len(rejected)} errors={len(bad)}")
    if accepted:
        (WS / f"agent_{agent_id.lower()}_votes.json").write_text(
            json.dumps({"votes": accepted}, ensure_ascii=False, indent=2), encoding="utf-8")
        (FIXTURES / f"live_agent_{agent_id.lower()}.json").write_text(
            json.dumps({"votes": accepted}, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in (bad + rejected)[:3]:
        record("P2", f"agent-{agent_id}-reject", "warn", json.dumps(r, ensure_ascii=False))


def phase2() -> None:
    for agent_id in _live_agents():
        _vote_for_agent(agent_id, PROMPTS / f"agent_{agent_id.lower()}.md")
    code, tv = cli(["tally-votes"])
    record("P2", "tally-votes", "pass" if tv.get("ok") else "fail",
           f"processed={tv.get('processed')} fetch={tv.get('fetch')} low={tv.get('low_priority')} discard={tv.get('discard')}")


def phase3() -> None:
    rc, out, err = run_script("fetch_worker.py", ["--limit", str(FETCH_LIMIT), "--rounds", "1", "--worker", "live"])
    record("P3", "fetch_worker", "pass" if rc == 0 else "fail", redact((out or err).strip().splitlines()[-1] if (out or err).strip() else "")[:120])

    import pymupdf

    checked = opened = 0
    for pdf in (WS / "papers").glob("*/paper.pdf"):
        checked += 1
        try:
            with pymupdf.open(str(pdf)) as doc:
                if doc.page_count >= 1:
                    opened += 1
        except Exception:  # noqa: BLE001
            pass
    status = "pass" if (checked and opened == checked) else ("warn" if opened else "fail")
    record("P3", "pdf-integrity", status, f"valid={opened}/{checked}")


def phase4() -> None:
    code, nr = cli(["next-reading", "--limit", "1"])
    papers = nr.get("papers", []) if nr.get("ok") else []
    if not papers:
        record("P4", "next-reading", "warn", "no fetched paper to read")
        return
    paper = papers[0]
    payload = {
        "paper_id": paper["paper_id"],
        "confirmed_uses": paper.get("confirmed_uses", []),
        "pages": paper.get("pages", []),
    }
    rc, out, err = agent(load_prompt(READ_PROMPTS / "reader.md", payload))
    if rc != 0:
        record("P4", "reader-agent", "fail", redact(err.strip())[:200])
        return
    try:
        cards = parse_json_loose(out)
    except Exception as exc:  # noqa: BLE001
        record("P4", "reader-agent", "fail", f"parse: {exc}; raw={redact(out)[:150]}")
        return
    record("P4", "reader-agent", "pass", f"cards={len(cards.get('cards', [])) if isinstance(cards, dict) else '?'}")
    code, ac = cli(["add-card"], input_text=json.dumps(cards, ensure_ascii=False))
    record("P4", "add-card", "pass" if ac.get("ok") else "fail",
           f"count={ac.get('count')} reason={redact(str(ac.get('reason', '')))[:120]}")
    if ac.get("ok"):
        (FIXTURES / "live_reader_cards.json").write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


def phase5() -> None:
    code, nc = cli(["next-caveat", "--limit", "1"])
    cards = nc.get("cards", []) if nc.get("ok") else []
    if cards:
        card = cards[0]
        rc, out, err = agent(load_prompt(READ_PROMPTS / "caveat.md", {"card": card, "pages": card.get("pages", [])}))
        if rc == 0:
            code, res = cli(["add-caveat", "--card-id", card["card_id"]], input_text=out)
            record("P5", "add-caveat", "pass" if res.get("ok") else "fail", redact(str(res.get("reason", "")))[:120])
        else:
            record("P5", "caveat-agent", "fail", redact(err.strip())[:150])
    else:
        record("P5", "next-caveat", "warn", "no confirmed card")

    code, au = cli(["audit", "--rate", str(AUDIT_RATE)])
    audit_cards = au.get("cards", [])[:AUDIT_MAX] if au.get("ok") else []
    results = []
    for card in audit_cards:
        rc, out, err = agent(load_prompt(READ_PROMPTS / "auditor.md", {"card": card, "pages": card.get("pages", [])}))
        if rc == 0:
            try:
                results.append(parse_json_loose(out))
            except Exception:  # noqa: BLE001
                pass
    if results:
        code, sa = cli(["submit-audit"], input_text=json.dumps({"results": results}, ensure_ascii=False))
        record("P5", "submit-audit", "pass" if sa.get("ok") else "fail",
               f"updated={sa.get('updated')} reliability={redact(json.dumps(sa.get('reliability', {}), ensure_ascii=False))[:120]}")
    else:
        record("P5", "audit", "warn", f"sampled={len(audit_cards)} but no valid auditor output")


def phase6() -> None:
    code, body = cli(["templates", "extract"])
    record("P6", "templates", "pass" if body.get("ok") else "fail", f"count={body.get('count')}")

    code, ex = cli(["figures", "extract"])
    record("P6", "figures extract", "pass" if ex.get("ok") else "fail",
           f"count={ex.get('count')} figure={ex.get('figure_count')} table={ex.get('table_count')}")

    intent = WS / "figure_intent.txt"
    intent.write_text(
        "Overview of a CLIP multi-modal pipeline: text encoder, image encoder, cross-modal alignment.",
        encoding="utf-8",
    )
    code, sg = cli(["figures", "suggest", "--input", str(intent), "--fig-id", "fig-live"])
    record("P6", "figures suggest", "pass" if sg.get("ok") else "fail",
           f"refs={sg.get('references')} reason={redact(str(sg.get('reason', '')))[:80]}")


def phase7() -> None:
    code, body = cli(["export"])
    record("P7", "export", "pass" if body.get("ok") else "fail", f"files={len(body.get('exports', []))}")
    code, cov = cli(["coverage"])
    record("P7", "coverage", "pass" if cov.get("ok") else "fail", f"sections={len(cov.get('sections', []))}")


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="live end-to-end orchestrator")
    parser.add_argument("--fresh", action="store_true", help="先清空工作区")
    parser.add_argument("--phases", default="0,1,2,3,4,5,6,7")
    args = parser.parse_args(argv)

    load_env_file(REPO / "config" / ".env")
    if args.fresh and WS.exists():
        shutil.rmtree(WS, ignore_errors=True)
    WS.mkdir(parents=True, exist_ok=True)
    prepare_labels()

    REPORT[:] = _load_state()
    backend = os.environ.get("MINE2DIGEST_AGENT_BACKEND", "opencode")
    if backend.lower() == "codex":
        model = os.environ.get("MINE2DIGEST_CODEX_MODEL") or "codex-default"
    else:
        model = os.environ.get("MINE2DIGEST_AGENT_MODEL", "opencode/deepseek-v4.1-flash-free")
    phases = {int(p) for p in args.phases.split(",") if p.strip()}
    steps = {0: phase0, 1: phase1, 2: phase2, 3: phase3, 4: phase4, 5: phase5, 6: phase6, 7: phase7}

    for i in sorted(phases):
        fn = steps.get(i)
        if not fn:
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            record(f"P{i}", "exception", "fail", redact(f"{type(exc).__name__}: {exc}")[:200])

    out = write_report(model)
    print(f"\nreport: {out}")
    fails = sum(1 for r in REPORT if r["status"] == "fail")
    print(f"summary: {len(REPORT)} steps, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
