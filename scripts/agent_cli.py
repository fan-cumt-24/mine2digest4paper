"""最小 agent harness：stdin 收整段 prompt，调外部 CLI，stdout 吐模型 JSON。

用途：充当 `MINE2DIGEST_AGENT_CMD`，让 live 测试/编排脚本以“外部 agent”方式驱动
真实 LLM。prompt 走 stdin/临时文件，避免大 prompt 超命令行长度限制与引号转义问题。

后端：
- `opencode`（默认）：`opencode run`，解析 `--format json` 事件流里的 `text`；
- `codex`：`codex exec`，用 `-o` 取最终消息（更稳，推荐）。

设计要点：
- 自动剥离 markdown 代码围栏；
- 不把任何密钥写进命令行参数。

环境变量：
- MINE2DIGEST_AGENT_BACKEND  opencode | codex（默认 opencode）
- MINE2DIGEST_AGENT_MODEL    opencode 模型（默认 opencode/deepseek-v4.1-flash-free）
- MINE2DIGEST_CODEX_MODEL    codex 模型（默认用 codex 自身配置）
- MINE2DIGEST_OPENCODE       opencode 可执行路径（默认自动探测）
- MINE2DIGEST_CODEX          codex 可执行路径（默认自动探测）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from mine2digest import common  # noqa: E402

common.load_env_file()  # 自动加载 skill 目录 .env（后端/模型等）

DEFAULT_MODEL = "opencode/deepseek-v4.1-flash-free"
INSTRUCTION = (
    "Follow the attached prompt exactly. Do not use any tools; just read the attachment and answer. "
    "Output ONLY the JSON object it requests, with no prose and no markdown code fences."
)


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def extract_text(raw: bytes) -> str:
    """从 opencode 的 JSON-lines 事件流里抽出模型文本。

    若整段是事件流但没有任何 `text` 事件（模型报错/被限流），返回空串，
    由调用方判为非成功，避免把原始事件误当成模型输出。
    """
    text = _decode(raw)
    parts: list[str] = []
    saw_event = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        saw_event = True
        if obj.get("type") == "text":
            parts.append((obj.get("part") or {}).get("text", ""))
    if parts:
        return "".join(parts)
    return "" if saw_event else text.strip()


def strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _opencode_exe() -> str:
    return os.environ.get("MINE2DIGEST_OPENCODE") or shutil.which("opencode") or "opencode"


def _codex_exe() -> str:
    return os.environ.get("MINE2DIGEST_CODEX") or shutil.which("codex") or "codex"


def run_opencode(prompt: str, model: str, timeout: int, agent: str | None = None) -> str:
    exe = _opencode_exe()
    with tempfile.TemporaryDirectory(prefix="m2d_agent_") as tmp:
        prompt_file = Path(tmp) / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        cmd = [
            exe,
            "run",
            INSTRUCTION,
            "-m",
            model,
            "--pure",
            "--format",
            "json",
            "--dir",
            tmp,
            "-f",
            str(prompt_file),
        ]
        if agent:
            cmd.extend(["--agent", agent])
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"agent timeout after {timeout}s\n")
            raise SystemExit(1)
        dump = os.environ.get("MINE2DIGEST_AGENT_DUMP")
        if dump:
            Path(dump).write_bytes(proc.stdout)
            Path(dump + ".err").write_bytes(proc.stderr)
        if proc.returncode != 0:
            sys.stderr.write(_decode(proc.stderr)[-2000:] or f"exit {proc.returncode}\n")
            raise SystemExit(1)
        return extract_text(proc.stdout)


def run_codex(prompt: str, model: str | None, timeout: int) -> str:
    """用 `codex exec` 跑一次：prompt 走 stdin，最终消息用 `-o` 取出。"""
    exe = _codex_exe()
    with tempfile.TemporaryDirectory(prefix="m2d_codex_") as tmp:
        out_file = Path(tmp) / "last.txt"
        cmd = [
            exe,
            "exec",
            *(["-m", model] if model else []),
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "--ephemeral",
            "-C",
            tmp,
            "-o",
            str(out_file),
            "-",
        ]
        try:
            proc = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"agent timeout after {timeout}s\n")
            raise SystemExit(1)
        dump = os.environ.get("MINE2DIGEST_AGENT_DUMP")
        if dump:
            Path(dump).write_bytes(proc.stdout)
            Path(dump + ".err").write_bytes(proc.stderr)
        if proc.returncode != 0:
            sys.stderr.write(_decode(proc.stderr)[-2000:] or f"exit {proc.returncode}\n")
            raise SystemExit(1)
        if not out_file.exists():
            return ""
        return out_file.read_text(encoding="utf-8", errors="replace").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="minimal opencode-backed agent harness")
    parser.add_argument("--backend", default=os.environ.get("MINE2DIGEST_AGENT_BACKEND", "opencode"))
    parser.add_argument("--model", default=None, help="覆盖默认模型（codex 默认用自身配置）")
    parser.add_argument(
        "--agent",
        default=os.environ.get("MINE2DIGEST_OPENCODE_AGENT", "summary"),
        help="opencode agent（默认 summary：禁工具、纯文本作答）",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--raw", action="store_true", help="不剥代码围栏，仅输出模型文本")
    args = parser.parse_args(argv)

    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.stderr.write("empty prompt\n")
        return 2

    if args.backend.lower() == "codex":
        model = args.model or os.environ.get("MINE2DIGEST_CODEX_MODEL")
        text = run_codex(prompt, model, args.timeout)
    else:
        model = args.model or os.environ.get("MINE2DIGEST_AGENT_MODEL", DEFAULT_MODEL)
        text = run_opencode(prompt, model, args.timeout, args.agent or None)
    if not text.strip():
        sys.stderr.write(f"no model text in {args.backend} output (error or rate-limited?)\n")
        return 1
    sys.stdout.write(text if args.raw else strip_fences(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
