"""阶段 C3 · 图表生成：包装 PaperBanana CLI，规范化产物到 outputs/figures/。"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import common

FIGURES_DIR = "outputs/figures"
DEFAULT_EXE = "paperbanana"


def figures_dir() -> Path:
    return common.wpath("outputs", "figures")


def _exe_prefix() -> list[str]:
    """可执行前缀：默认 `paperbanana`，可由 MINE2DIGEST_PAPERBANANA 覆盖（可含参数）。"""
    return shlex.split(os.environ.get("MINE2DIGEST_PAPERBANANA", DEFAULT_EXE)) or [DEFAULT_EXE]


def _default_fig_id(input_path: Path) -> str:
    stem = re.sub(r"[^0-9a-zA-Z]+", "-", input_path.stem).strip("-").lower() or "figure"
    return f"fig-{stem}"


def _invoke(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _collect(tmp: Path) -> dict:
    pngs = sorted(tmp.glob("*.png"))
    if not pngs:
        raise RuntimeError("paperbanana produced no .png output")
    srcs = sorted(tmp.glob("*.src.py")) or sorted(tmp.glob("*.py"))
    manifests = sorted(tmp.glob("*.manifest.json"))
    return {
        "png": pngs[0],
        "src": srcs[0] if srcs else None,
        "manifest": manifests[0] if manifests else None,
    }


def generate_figure(
    input_file: str,
    caption: str,
    fig_id: str | None = None,
    out_dir: str | Path | None = None,
) -> dict:
    caption = (caption or "").strip()
    if not caption:
        raise ValueError("caption is required")
    input_path = Path(input_file)
    if not input_path.is_file():
        raise ValueError(f"input file not found: {input_file}")

    out = Path(out_dir) if out_dir else figures_dir()
    out.mkdir(parents=True, exist_ok=True)
    fig_id = fig_id or _default_fig_id(input_path)

    cmd = _exe_prefix() + ["generate", "--input", str(input_path.resolve()), "--caption", caption]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        proc = _invoke(cmd, tmp)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"paperbanana failed (exit {proc.returncode}): {detail}")
        produced = _collect(tmp_path)

        png_dest = out / f"{fig_id}.png"
        shutil.copyfile(produced["png"], png_dest)
        src_dest = None
        if produced["src"] is not None:
            src_dest = out / f"{fig_id}.src.py"
            shutil.copyfile(produced["src"], src_dest)

        embedded = None
        if produced["manifest"] is not None:
            try:
                embedded = json.loads(produced["manifest"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                embedded = None

    manifest = {
        "fig_id": fig_id,
        "caption": caption,
        "input": str(input_path),
        "command": cmd,
        "png": str(png_dest),
        "src": str(src_dest) if src_dest else None,
        "generated_at": common.iso_now(),
        "paperbanana": embedded,
    }
    manifest_dest = out / f"{fig_id}.manifest.json"
    manifest_dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return common.ok(
        fig_id=fig_id,
        png=str(png_dest),
        src=str(src_dest) if src_dest else None,
        manifest=str(manifest_dest),
    )
