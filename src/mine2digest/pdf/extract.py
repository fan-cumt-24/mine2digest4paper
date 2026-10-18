"""PyMuPDF 按页提取文本，落盘 papers/{slug}/extracted/page-{n}.txt（幂等）。"""

from __future__ import annotations

from pathlib import Path

EXTRACTED_DIR = "extracted"


def _pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:  # 旧版
        import fitz

        return fitz


def extract_pages(pdf_path: str | Path, out_dir: str | Path) -> list[Path]:
    """逐页提取文本；已存在的页文件跳过（幂等）。返回页文件路径列表。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pymupdf = _pymupdf()
    written: list[Path] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for index, page in enumerate(doc, start=1):
            target = out / f"page-{index}.txt"
            if not target.exists():
                target.write_text(page.get_text(), encoding="utf-8")
            written.append(target)
    return written


def load_page_texts(extracted_dir: str | Path) -> dict[int, str]:
    """读取缓存的页文本，返回 {page_no: text}。"""
    out = Path(extracted_dir)
    texts: dict[int, str] = {}
    if not out.exists():
        return texts
    for path in out.glob("page-*.txt"):
        try:
            number = int(path.stem.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        texts[number] = path.read_text(encoding="utf-8")
    return texts


def ensure_extracted(paper_dir: str | Path) -> list[Path]:
    """确保论文文本已提取（幂等），返回页文件列表；无 PDF 时返回空。"""
    paper_dir = Path(paper_dir)
    out = paper_dir / EXTRACTED_DIR
    existing = sorted(out.glob("page-*.txt")) if out.exists() else []
    if existing:
        return existing
    pdf = paper_dir / "paper.pdf"
    if not pdf.exists():
        return []
    return extract_pages(pdf, out)
