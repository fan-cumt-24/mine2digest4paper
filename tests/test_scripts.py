"""发现脚本的离线单测（不联网，httpx 惰性导入）。"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def openalex():
    return _load("openalex_seed")


@pytest.fixture(scope="module")
def s2():
    return _load("s2_expand")


def test_openalex_build_params(openalex):
    params = openalex.build_params("rag", 25, filters="type:article", mailto="me@x.com")
    assert params == {"search": "rag", "per-page": 25, "filter": "type:article", "mailto": "me@x.com"}


def test_openalex_normalize_work(openalex):
    raw = json.loads((FIXTURES / "openalex_raw.json").read_text(encoding="utf-8"))
    recs = [openalex.normalize_work(w) for w in raw["results"]]

    first = recs[0]
    assert first["openalex_id"] == "W2741809807"
    assert first["paper_id"] == "W2741809807"
    assert first["doi"] == "https://doi.org/10.7717/peerj.4375"
    assert first["abstract"] == "We study open access"
    assert first["venue"] == "PeerJ"
    assert first["source"] == "openalex"

    assert recs[1]["abstract"] is None


def test_openalex_normalized_output_is_valid_jsonl(openalex, isolated):
    """脚本产物能直接喂给 add-candidates。"""
    from typer.testing import CliRunner

    from mine2digest.cli import app

    raw = json.loads((FIXTURES / "openalex_raw.json").read_text(encoding="utf-8"))
    lines = "\n".join(json.dumps(openalex.normalize_work(w)) for w in raw["results"])
    result = CliRunner().invoke(app, ["add-candidates", "--source", "openalex"], input=lines)
    assert json.loads(result.stdout)["inserted"] == 2


def test_s2_qualifies(s2):
    assert s2.qualifies({"isInfluential": True, "intents": []})
    assert s2.qualifies({"isInfluential": False, "intents": ["methodology"]})
    assert s2.qualifies({"isInfluential": False, "intents": ["result"]})
    assert not s2.qualifies({"isInfluential": False, "intents": ["background"]})
    assert not s2.qualifies({"isInfluential": False, "intents": []})


def test_s2_parse_references_filters_edges(s2):
    raw = json.loads((FIXTURES / "s2_references.json").read_text(encoding="utf-8"))
    cands, rels = s2.parse_s2_response(raw, "SEED", "references")

    assert [r["dst_id"] for r in rels] == ["S2A", "S2C"]  # background 的 S2B 被过滤
    assert all(r["src_id"] == "SEED" for r in rels)
    assert [c["s2_id"] for c in cands] == ["S2A", "S2C"]
    assert cands[0]["doi"] == "10.1/x"


def test_s2_parse_citations_direction(s2):
    raw = {"data": [{"isInfluential": True, "citedPaper": {}, "citingPaper": {"paperId": "P9"}}]}
    cands, rels = s2.parse_s2_response(raw, "SEED", "citations")
    assert rels[0] == {
        "src_id": "P9",
        "dst_id": "SEED",
        "relation": "cites",
        "is_influential": True,
        "contexts": [],
    }
