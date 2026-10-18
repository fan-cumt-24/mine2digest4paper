"""全局 skill 安装脚本：渲染、安装、校验、卸载（不触碰真实 home）。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import install_skill  # noqa: E402


def test_render_injects_assets_root_and_keeps_frontmatter(tmp_path):
    text = install_skill.render(repo_root=tmp_path)
    assert text.startswith("---\n")
    assert "name: mine2digest" in text
    assert f"ASSETS（只读资产根）: `{tmp_path.resolve()}`" in text
    assert "## 工作区与资产（先读）" in text


def test_install_check_uninstall(tmp_path):
    dest = tmp_path / "skills" / "mine2digest" / "SKILL.md"

    assert install_skill.main(["--dest", str(dest)]) == 0
    assert dest.exists()
    assert install_skill.main(["--dest", str(dest), "--check"]) == 0

    dest.write_text("stale", encoding="utf-8")
    assert install_skill.main(["--dest", str(dest), "--check"]) == 1

    assert install_skill.main(["--dest", str(dest)]) == 0
    assert install_skill.main(["--dest", str(dest), "--uninstall"]) == 0
    assert not dest.exists()


def test_check_missing_is_stale(tmp_path):
    dest = tmp_path / "missing" / "SKILL.md"
    assert install_skill.main(["--dest", str(dest), "--check"]) == 1
