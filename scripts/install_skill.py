"""把 mine2digest skill 安装到 opencode 全局 skills 目录。

单一真相源是仓库内的 `.opencode/skills/mine2digest/SKILL.md`；本脚本生成全局副本，
并在正文前注入只读资产根 `ASSETS`。命令在**当前工作目录**运行（`mine2digest init`
创建 `m2d/` 工作区），产物留在工作区，只有 config/prompts/docs/scripts 取自 ASSETS。

用法：
    python scripts/install_skill.py                # 安装到 ~/.config/opencode/skills/mine2digest/SKILL.md
    python scripts/install_skill.py --check        # 校验全局副本是否最新
    python scripts/install_skill.py --uninstall    # 卸载
    python scripts/install_skill.py --dest <path>  # 自定义目标（测试用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO / ".opencode" / "skills" / "mine2digest" / "SKILL.md"
DEFAULT_DEST = Path.home() / ".config" / "opencode" / "skills" / "mine2digest" / "SKILL.md"


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[: i + 1]), "".join(lines[i + 1 :])
    return "", text


def render(repo_root: Path | None = None) -> str:
    text = CANONICAL.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    root = str((repo_root or REPO).resolve())
    header = (
        "\n> **Global install.** ASSETS（只读资产根）: `" + root + "`\n"
        "> 命令在**当前工作目录**运行：先 `mine2digest init` 建 `m2d/` 工作区，"
        "产物（db/papers/exports/drafts/outputs/logs）全部留在工作区。\n"
        "> 只读资产取 `<ASSETS>/...`（`config/labels.yaml`、`config/*_prompts/`、"
        "`docs/tools.json`、`scripts/`）；绝不把产物写进 ASSETS。\n"
    )
    return (frontmatter + header + body).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="install mine2digest skill globally")
    parser.add_argument("--dest", default=str(DEFAULT_DEST))
    parser.add_argument("--check", action="store_true", help="仅校验全局副本是否最新")
    parser.add_argument("--uninstall", action="store_true", help="删除全局副本")
    args = parser.parse_args(argv)

    dest = Path(args.dest)
    if args.uninstall:
        if dest.exists():
            dest.unlink()
        try:
            dest.parent.rmdir()
        except OSError:
            pass
        sys.stdout.write(f"removed {dest}\n")
        return 0

    text = render()
    if args.check:
        current = dest.read_text(encoding="utf-8") if dest.exists() else ""
        if current != text:
            sys.stderr.write(f"{dest} is stale; run: python scripts/install_skill.py\n")
            return 1
        sys.stdout.write(f"{dest} is up to date\n")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    sys.stdout.write(f"installed {dest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
