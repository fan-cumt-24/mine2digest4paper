"""从 mine2digest CLI 生成机器可读的 tool schema（docs/tools.json）。

不依赖任何 agent 框架：输出中立的 JSON Schema，供任意 function-calling 宿主挂载。
- `parameters` 由 Typer/Click 的命令参数自动推导；
- `stdin` 由 pydantic 模型生成（submit-vote/add-card/add-caveat/submit-audit）。

用法：
    python scripts/export_tools.py            # 写入 docs/tools.json
    python scripts/export_tools.py --check    # 只校验是否最新（CI/测试用）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import click
import click.types as click_types

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_OUT = REPO / "docs" / "tools.json"

STDIN_SCHEMAS = {
    "submit-vote": ("VoteFile", "mine2digest.models", "VoteFile"),
    "add-card": ("CardFile", "mine2digest.models", "CardFile"),
    "add-caveat": ("Caveat", "mine2digest.models", "Caveat"),
    "submit-audit": ("AuditFile", "mine2digest.models", "AuditFile"),
    "figures add-notes": ("FigureNoteFile", "mine2digest.models", "FigureNoteFile"),
}

JSONL_STDIN = {
    "add-candidates": {
        "format": "jsonl",
        "description": "每行一个候选对象，至少含 title；可选 doi/openalex_id/arxiv_id/s2_id/year/venue/source/abstract。",
    },
    "add-relations": {
        "format": "jsonl",
        "description": "每行一条引用边，必含 src_id/dst_id/relation；可选 is_influential/contexts。",
    },
}


def _json_type(param: click.Parameter) -> dict:
    ptype = param.type
    if isinstance(ptype, type(click_types.BOOL)):
        schema: dict = {"type": "boolean"}
    elif isinstance(ptype, type(click_types.INT)):
        schema = {"type": "integer"}
    elif isinstance(ptype, type(click_types.FLOAT)):
        schema = {"type": "number"}
    else:
        schema = {"type": "string"}
    if getattr(param, "multiple", False):
        return {"type": "array", "items": schema}
    return schema


def _param_schema(param: click.Parameter) -> dict:
    schema = _json_type(param)
    if param.help:
        schema["description"] = param.help
    if getattr(param, "opts", None):
        schema["argument"] = param.opts[0]
    if not param.required and param.default is not None:
        schema["default"] = param.default
    return schema


def _walk(command: click.Command, prefix: str = "") -> list[tuple[str, click.Command]]:
    if isinstance(command, click.Group):
        out: list[tuple[str, click.Command]] = []
        for name, sub in command.commands.items():
            path = f"{prefix} {name}".strip()
            out.extend(_walk(sub, path))
        return out
    return [(prefix, command)]


def _stdin_schema(name: str) -> dict | None:
    if name in JSONL_STDIN:
        return JSONL_STDIN[name]
    if name not in STDIN_SCHEMAS:
        return None
    import importlib

    model_name, module_path, attr = STDIN_SCHEMAS[name]
    module = importlib.import_module(module_path)
    model = getattr(module, attr)
    return {"model": model_name, "schema": model.model_json_schema()}


def build_tools_document() -> dict:
    import typer.main

    from mine2digest.cli import app

    root = typer.main.get_command(app)
    tools = []
    for name, command in _walk(root):
        params = [p for p in command.params if p.name not in ("help",) and not getattr(p, "is_eager", False)]
        properties = {p.name: _param_schema(p) for p in params if p.name}
        required = [p.name for p in params if p.required and p.name]
        help_text = (command.help or "").strip()
        entry: dict = {
            "name": name,
            "description": help_text,
            "status": "stub" if help_text.startswith("[stub]") else "ready",
            "executor": {"type": "cli", "command": ["mine2digest", *name.split()]},
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }
        stdin = _stdin_schema(name)
        if stdin:
            entry["stdin"] = stdin
        tools.append(entry)

    tools.sort(key=lambda t: t["name"])
    return {
        "_generated_by": "scripts/export_tools.py",
        "_note": "Do not edit by hand. Regenerate with: python scripts/export_tools.py",
        "contract": {
            "invocation": "mine2digest <name> [flags]",
            "stdin": "JSON payload when --file is omitted (see tool.stdin)",
            "stdout": "single JSON object",
            "success": {"ok": True},
            "failure": {"ok": False, "reason": "<string>"},
            "exit_codes": {"0": "ok", "1": "failure"},
        },
        "tools": tools,
    }


def render(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="export mine2digest tool schema")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true", help="仅校验现有文件是否最新")
    args = parser.parse_args(argv)

    text = render(build_tools_document())
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            sys.stderr.write(f"{out} is stale; run: python scripts/export_tools.py\n")
            return 1
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {out} ({len(text)} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
