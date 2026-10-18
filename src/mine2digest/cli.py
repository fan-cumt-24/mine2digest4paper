"""mine2digest CLI 入口。

统一契约：输入 JSON（stdin 或 --file），输出 JSON（stdout）。
成功 {"ok": true, ...}；失败 {"ok": false, "reason": "..."} 且退出码 1。
每次调用追加一行到 logs/cli.jsonl。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

import typer

from . import common, db, log
from .commands import (
    a_discover,
    a_fetch,
    a_screen,
    b_audit,
    b_read,
    c_draft,
    c_figures,
    c_query,
    c_templates,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, help="mine2digest CLI")
templates_app = typer.Typer(no_args_is_help=True, help="C1 结构模板抽取")
figures_app = typer.Typer(no_args_is_help=True, help="C3 图表生成（PaperBanana 包装）")
app.add_typer(templates_app, name="templates")
app.add_typer(figures_app, name="figures")


def _run(name: str, args: dict, fn, log_path: str | None = None) -> None:
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - 统一转成结构化错误
        result = common.err(str(exc), error_type=type(exc).__name__)
    log.append(
        {
            "ts": common.iso_now(),
            "command": name,
            "args": args,
            "ok": bool(result.get("ok")),
            "reason": result.get("reason"),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 3),
        },
        path=log_path,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, default=str))
    if not result.get("ok"):
        raise typer.Exit(code=1)


@app.command()
def init(
    workspace: Optional[str] = typer.Option(
        None, "--workspace", help="工作区目录（缺省: env 或 <cwd>/m2d）"
    ),
) -> None:
    """初始化工作区与数据库（幂等）：建目录骨架、写标记、拷贝 brief 模板、建 DB。"""

    ws = Path(workspace) if workspace else common.workspace()

    def _do() -> dict:
        ws.mkdir(parents=True, exist_ok=True)
        (ws / common.WORKSPACE_MARKER).write_text(
            f"mine2digest workspace\ncreated_at: {common.iso_now()}\n", encoding="utf-8"
        )
        for sub in ("db", "logs", "papers", "inbox", "exports", "drafts", "outputs", "tmp", "config"):
            (ws / sub).mkdir(exist_ok=True)
        brief = ws / "config" / "experiment_brief.md"
        template = common.PACKAGE_DATA / "experiment_brief.md"
        if not brief.exists() and template.exists():
            brief.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        db_file = os.environ.get("MINE2DIGEST_DB") or str(ws / "db" / "mine2digest.sqlite")
        conn = db.connect(db_file)
        try:
            db.init_db(conn)
            return common.ok(workspace=str(ws), db=db_file, schema_version=db.SCHEMA_VERSION)
        finally:
            conn.close()

    _run("init", {"workspace": workspace}, _do, log_path=str(ws / "logs" / "cli.jsonl"))


@app.command("add-candidates")
def add_candidates(
    file: Optional[str] = typer.Option(None, "--file", help="规范化候选 JSONL（缺省读 stdin）"),
    source: Optional[str] = typer.Option(None, "--source", help="批次来源标识"),
) -> None:
    """批量插入候选论文（JSONL），跨源去重，已存在只更新 updated_at。"""
    _run(
        "add-candidates",
        {"file": file, "source": source},
        lambda: a_discover.add_candidates(file, source),
    )


@app.command("add-relations")
def add_relations(
    file: Optional[str] = typer.Option(None, "--file", help="引用边 JSONL（缺省读 stdin）"),
) -> None:
    """批量写入引用边（INSERT OR REPLACE）。"""
    _run("add-relations", {"file": file}, lambda: a_discover.add_relations(file))


@app.command("next-candidates")
def next_candidates(
    agent_id: str = typer.Option(..., "--agent-id", help="投票 agent 标识"),
    limit: int = typer.Option(50, "--limit", help="最多返回条数"),
) -> None:
    """取未被该 agent 投票的候选论文（带 citation_contexts）。"""
    _run(
        "next-candidates",
        {"agent_id": agent_id, "limit": limit},
        lambda: a_screen.next_candidates(agent_id, limit),
    )


@app.command("submit-vote")
def submit_vote(
    agent_id: str = typer.Option(..., "--agent-id", help="投票 agent 标识"),
    file: Optional[str] = typer.Option(None, "--file", help="投票 JSON 文件（缺省读 stdin）"),
) -> None:
    """提交（或幂等覆盖）某 agent 对论文的用途投票。"""
    _run(
        "submit-vote",
        {"agent_id": agent_id, "file": file},
        lambda: a_screen.submit_vote(agent_id, file),
    )


@app.command("tally-votes")
def tally_votes(
    limit: Optional[int] = typer.Option(None, "--limit", help="最多合并条数"),
) -> None:
    """合并双 agent 投票，写 screening_merged / confirmed_uses / fetch_queue 并迁移状态。"""
    _run("tally-votes", {"limit": limit}, lambda: a_screen.tally_votes(limit))


@app.command("next-fetch")
def next_fetch(
    limit: int = typer.Option(10, "--limit", help="最多认领条数"),
    worker: Optional[str] = typer.Option(None, "--worker", help="worker 标识"),
) -> None:
    """原子认领 fetch_queue 中的论文（status=queued -> fetching）。"""
    _run("next-fetch", {"limit": limit, "worker": worker}, lambda: a_fetch.next_fetch(limit, worker))


@app.command("report-fetch")
def report_fetch(
    paper_id: str = typer.Option(..., "--paper-id"),
    status: str = typer.Option(..., "--status", help="success | failed"),
    path: Optional[str] = typer.Option(None, "--path", help="成功时的 PDF 路径"),
    source: Optional[str] = typer.Option(None, "--source", help="来源 fetcher 名"),
    error: Optional[str] = typer.Option(None, "--error", help="失败原因"),
    retry: bool = typer.Option(False, "--retry", help="失败后清 claimed_at 允许重试"),
    version: str = typer.Option("preprint", "--version", help="版本标签"),
) -> None:
    """回填获取结果：成功归档 papers/，失败写 fetch_attempts。"""
    _run(
        "report-fetch",
        {"paper_id": paper_id, "status": status, "source": source, "retry": retry},
        lambda: a_fetch.report_fetch(paper_id, status, path, source, error, retry, version),
    )


@app.command("ingest-manual")
def ingest_manual(
    file: str = typer.Option(..., "--file", help="inbox 中的 PDF 路径"),
    doi: Optional[str] = typer.Option(None, "--doi"),
    title: Optional[str] = typer.Option(None, "--title"),
    version: str = typer.Option("preprint", "--version"),
    slug: Optional[str] = typer.Option(None, "--slug", help="自定义 papers 子目录名"),
) -> None:
    """把 inbox 中的人工投放 PDF 归档并置为 fetched。"""
    _run(
        "ingest-manual",
        {"file": file, "doi": doi, "title": title},
        lambda: a_fetch.ingest_manual(file, doi, title, version, slug),
    )


@app.command("next-reading")
def next_reading(
    limit: int = typer.Option(5, "--limit", help="最多返回条数"),
) -> None:
    """取 status=fetched 的论文，带 merged_uses / disagreement / 预提取页文本。"""
    _run("next-reading", {"limit": limit}, lambda: b_read.next_reading(limit))


@app.command("add-card")
def add_card(
    file: Optional[str] = typer.Option(None, "--file", help="证据卡 JSON（缺省读 stdin）"),
) -> None:
    """事务内逐条插入证据卡 + 同步 locator 校验；任一失败整批回滚。"""
    _run("add-card", {"file": file}, lambda: b_read.add_card(file))


@app.command("verify-card")
def verify_card(
    card_id: str = typer.Option(..., "--card-id"),
) -> None:
    """单独重跑某张卡的 locator 校验。"""
    _run("verify-card", {"card_id": card_id}, lambda: b_read.verify_card(card_id))


@app.command("next-caveat")
def next_caveat(
    limit: int = typer.Option(5, "--limit"),
) -> None:
    """取 caveat IS NULL 且 fulltext_confirmed 的卡（带页文本）。"""
    _run("next-caveat", {"limit": limit}, lambda: b_audit.next_caveat(limit))


@app.command("add-caveat")
def add_caveat(
    card_id: str = typer.Option(..., "--card-id"),
    file: Optional[str] = typer.Option(None, "--file", help="caveat JSON（缺省读 stdin）"),
) -> None:
    """把反证结果绑定到具体证据卡。"""
    _run("add-caveat", {"card_id": card_id, "file": file}, lambda: b_audit.add_caveat(card_id, file))


@app.command("audit")
def audit(
    rate: Optional[float] = typer.Option(None, "--rate", help="抽样比例（缺省取 labels）"),
) -> None:
    """随机抽样 fulltext_confirmed 卡，写 audit_samples。"""
    _run("audit", {"rate": rate}, lambda: b_audit.run_audit(rate))


@app.command("submit-audit")
def submit_audit(
    file: Optional[str] = typer.Option(None, "--file", help="审计结果 JSON（缺省读 stdin）"),
) -> None:
    """回填审计结果并更新 source_reliability。"""
    _run("submit-audit", {"file": file}, lambda: b_audit.submit_audit(file))


@app.command("reject-card")
def reject_card(
    card_id: str = typer.Option(..., "--card-id"),
    reason: Optional[str] = typer.Option(None, "--reason"),
) -> None:
    """把证据卡置为 rejected。"""
    _run("reject-card", {"card_id": card_id, "reason": reason}, lambda: b_audit.reject_card(card_id, reason))


@app.command("coverage")
def coverage(
    section: Optional[List[str]] = typer.Option(None, "--section", help="限定节，可多次"),
) -> None:
    """覆盖矩阵（section × type），空格标 reclaimable。"""
    _run("coverage", {"section": section}, lambda: c_query.coverage(section))


@app.command("query")
def query(
    use_type: str = typer.Option(..., "--type"),
    section: str = typer.Option(..., "--section"),
    min_confidence: float = typer.Option(0.0, "--min-confidence"),
) -> None:
    """按 (section, type) 拉证据卡（带 caveat），按 source 可信度加权排序。"""
    _run(
        "query",
        {"type": use_type, "section": section, "min_confidence": min_confidence},
        lambda: c_query.query(use_type, section, min_confidence),
    )


@app.command("lookup")
def lookup(
    paper_id: str = typer.Option(..., "--paper-id"),
) -> None:
    """单篇全信息回溯（含被丢弃/驳回的用途）。"""
    _run("lookup", {"paper_id": paper_id}, lambda: c_query.lookup(paper_id))


@app.command("export")
def export(
    view: Optional[str] = typer.Option(None, "--view", help="单个视图名；缺省导出全部"),
) -> None:
    """从 DB 导出只读视图到 exports/。"""
    _run("export", {"view": view}, lambda: c_query.export(view))


@app.command("revive")
def revive(
    use_type: Optional[str] = typer.Option(None, "--type"),
    section: Optional[str] = typer.Option(None, "--section"),
    gaps: bool = typer.Option(False, "--gaps", help="自动复活所有覆盖空格对应的 low_priority 论文"),
    paper_id: Optional[List[str]] = typer.Option(
        None, "--paper-id", help="显式复活指定论文（discarded→candidate 重筛 / low_priority→queued）"
    ),
    limit: Optional[int] = typer.Option(None, "--limit"),
) -> None:
    """复活赛：low_priority→queued（折扣入队）或 discarded→candidate（重筛）。"""
    _run(
        "revive",
        {"type": use_type, "section": section, "gaps": gaps, "paper_id": paper_id, "limit": limit},
        lambda: c_query.revive(use_type, section, gaps, limit, paper_id),
    )


@templates_app.command("extract")
def templates_extract(
    section: Optional[List[str]] = typer.Option(None, "--section", help="限定节，可多次"),
) -> None:
    """抽取结构模板：style_sample 已确认优先，不足时 venue 回退（provisional）。"""
    _run("templates extract", {"section": section}, lambda: c_templates.extract_templates(section))


@figures_app.command("generate")
def figures_generate(
    input_file: str = typer.Option(..., "--input", help="方法/图表描述文本文件"),
    caption: str = typer.Option(..., "--caption", help="图注"),
    fig_id: Optional[str] = typer.Option(None, "--fig-id", help="图 id（默认由输入文件名推导）"),
    out: Optional[str] = typer.Option(None, "--out", help="输出目录（默认 outputs/figures）"),
) -> None:
    """包装 PaperBanana 生成图，产物规范化为 {fig_id}.png/.src.py/.manifest.json。"""
    _run(
        "figures generate",
        {"input": input_file, "caption": caption, "fig_id": fig_id},
        lambda: c_figures.generate_figure(input_file, caption, fig_id, out),
    )


@app.command("draft")
def draft(
    section: str = typer.Option(..., "--section", help="目标节（labels.target_sections 之一）"),
    min_confidence: float = typer.Option(0.0, "--min-confidence", help="证据卡最低置信度"),
    out_dir: Optional[str] = typer.Option(None, "--out-dir", help="草稿输出目录（缺省 <ws>/drafts）"),
    polish: bool = typer.Option(False, "--polish", help="调用外部命令把 prompt 打磨成成稿"),
    polish_cmd: Optional[str] = typer.Option(
        None, "--polish-cmd", help="覆盖 MINE2DIGEST_DRAFT_CMD"
    ),
) -> None:
    """读结构模板 + 覆盖矩阵 + 证据卡，生成证据绑定草稿与打磨 prompt。"""
    _run(
        "draft",
        {
            "section": section,
            "min_confidence": min_confidence,
            "out_dir": out_dir,
            "polish": polish,
            "polish_cmd": polish_cmd,
        },
        lambda: c_draft.draft(section, min_confidence, out_dir, polish, polish_cmd),
    )


@app.command("verify")
def verify(
    file: str = typer.Option(..., "--file", help="待校验的草稿 Markdown"),
    require_refs: bool = typer.Option(
        False, "--require-refs", help="Claims 下每条顶层 bullet 必须有 evidence_ref"
    ),
    recheck: bool = typer.Option(False, "--recheck", help="重跑 locator（需要 PDF）"),
) -> None:
    """独立校验草稿里所有 evidence_ref 的回溯有效性。"""
    _run(
        "verify",
        {"file": file, "require_refs": require_refs, "recheck": recheck},
        lambda: c_draft.verify(file, require_refs, recheck),
    )


if __name__ == "__main__":
    app()
