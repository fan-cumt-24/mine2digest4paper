"""PyMuPDF 把指定页渲染成 PNG（供支持视觉的宿主模型读图抽取图表设计）。

与 extract.py 一致地兼容 `pymupdf` / 旧版 `fitz` 两个导入名。零新依赖。
"""

from __future__ import annotations

from pathlib import Path


def _pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:  # 旧版
        import fitz

        return fitz


def render_page(
    pdf_path: str | Path,
    page_no: int,
    out_png: str | Path,
    dpi: int = 150,
) -> Path:
    """把第 `page_no` 页（1 起）渲染成 PNG 写盘，返回输出路径（幂等：已存在则跳过）。"""
    out = Path(out_png)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    pymupdf = _pymupdf()
    zoom = max(float(dpi), 1.0) / 72.0
    with pymupdf.open(str(pdf_path)) as doc:
        if page_no < 1 or page_no > doc.page_count:
            raise ValueError(f"page {page_no} out of range (1..{doc.page_count})")
        page = doc[page_no - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        pixmap.save(str(out))
    return out
