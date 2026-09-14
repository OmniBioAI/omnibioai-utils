"""Targeted tests closing the remaining coverage gaps left by
tests/test_high_coverage.py - one test per still-uncovered branch, kept
separate so each gap's rationale is easy to find and review independently.
"""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name):
    return importlib.import_module(name)


class FakeIndex:
    def __init__(self, dim):
        self.d, self.ntotal, self.rows = dim, 0, []

    def add(self, arr):
        self.rows.append(arr)
        self.ntotal += len(arr)


class FakePlaywright:
    """Minimal `with sync_playwright() as p: ...` stand-in shared by the
    browser-automation scripts' main()/do_login() flows."""

    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def chromium(self):
        page = self.page

        class Chromium:
            def launch(self, **kwargs):
                class Browser:
                    def new_context(self, **kwargs):
                        class Context:
                            def new_page(self):
                                return page

                            def storage_state(self, **kwargs):
                                pass

                        return Context()

                    def close(self):
                        pass

                return Browser()

        return Chromium()


# ── delete_packages_browser.py ────────────────────────────────────────────────

def test_delete_find_open_dialog_count_exception_continues(monkeypatch):
    d = load("delete_packages_browser")
    page = MagicMock()
    bad = MagicMock()
    bad.count.side_effect = RuntimeError("boom")
    good = MagicMock()
    good.count.return_value = 1
    good.first.is_visible.return_value = True
    page.locator.return_value.filter.side_effect = [bad, bad, bad, good]
    assert d.find_open_dialog(page, "hint") is good.first


def test_delete_find_open_dialog_returns_none_when_all_candidates_fail(monkeypatch):
    d = load("delete_packages_browser")
    page = MagicMock()
    bad = MagicMock()
    bad.count.side_effect = RuntimeError("boom")
    page.locator.return_value.filter.side_effect = [bad, bad, bad, bad]
    assert d.find_open_dialog(page, "hint") is None


def test_delete_main_packages_file_missing(monkeypatch, tmp_path):
    d = load("delete_packages_browser")
    monkeypatch.setattr(d, "AUTH_STATE_FILE", str(tmp_path / "auth"))
    monkeypatch.setattr(d, "PACKAGES_FILE", str(tmp_path / "missing_packages"))
    Path(d.AUTH_STATE_FILE).write_text("x")
    monkeypatch.setattr(sys, "argv", ["x"])
    with pytest.raises(SystemExit):
        d.main()


# ── agent_tool_selection_eval.py ──────────────────────────────────────────────

def test_agent_load_corpus_missing_tools_dir(tmp_path):
    a = load("agent_tool_selection_eval")
    with pytest.raises(SystemExit):
        a.load_corpus(None, str(tmp_path / "no_such_dir"), None, None, [], False)


def test_agent_load_corpus_prefers_tes_api_url(monkeypatch):
    a = load("agent_tool_selection_eval")
    monkeypatch.setattr(a, "load_corpus_from_api", lambda url, tags: [{"tool_id": "x"}])
    result = a.load_corpus(None, "unused-dir", None, "http://tes", [], False)
    assert result == [{"tool_id": "x"}]


# ── download_references.py ────────────────────────────────────────────────────

def test_download_only_flag_and_unknown_assembly(monkeypatch, tmp_path, capsys):
    d = load("download_references")
    monkeypatch.setattr(d, "ASSEMBLY_MAP", {})
    monkeypatch.setattr(
        sys, "argv",
        ["x", "--base-dir", str(tmp_path), "--species", "human", "--only", "genome"],
    )
    d.main()
    out = capsys.readouterr().out
    assert "--only filter" in out
    assert "Unknown assembly" in out


# ── make_public_browser.py ────────────────────────────────────────────────────

def test_make_public_blank_line_in_org_file_is_skipped(monkeypatch, tmp_path):
    m = load("make_public_browser")
    monkeypatch.setattr(m, "ORG_PACKAGES_FILE", str(tmp_path / "org.txt"))
    (tmp_path / "org.txt").write_text("a\tprivate\n\nb\tpublic\n")
    assert m.load_candidate_packages("x") == ["a"]


def test_make_public_do_login(monkeypatch, tmp_path, capsys):
    m = load("make_public_browser")
    page = MagicMock()
    monkeypatch.setattr(m, "sync_playwright", lambda: FakePlaywright(page))
    monkeypatch.setattr(m, "AUTH_STATE_FILE", str(tmp_path / "auth"))
    monkeypatch.setattr("builtins.input", lambda: "")
    m.do_login()
    assert "Session saved" in capsys.readouterr().out


def test_make_public_debug_radio_exception_and_no_dialogs(monkeypatch, capsys):
    m = load("make_public_browser")
    page = MagicMock()
    page.url = "u"
    page.title.return_value = "t"
    page.screenshot.return_value = None
    page.get_by_role.return_value.all_text_contents.return_value = []
    page.locator.return_value.all_text_contents.return_value = []
    radio = MagicMock()
    radio.get_attribute.side_effect = RuntimeError("boom")
    page.get_by_role.return_value.all.return_value = [radio]
    page.locator.return_value.all.return_value = []  # no [role=dialog] elements
    m.dump_debug_info(page, "pkg")
    assert "no [role=dialog] element found" in capsys.readouterr().out


def test_make_public_debug_dialog_inner_text_exception(monkeypatch, capsys):
    m = load("make_public_browser")
    page = MagicMock()
    page.url = "u"
    page.title.return_value = "t"
    page.screenshot.return_value = None
    page.get_by_role.return_value.all_text_contents.return_value = []
    page.locator.return_value.all_text_contents.return_value = []
    page.get_by_role.return_value.all.return_value = []
    dialog = MagicMock()
    dialog.inner_text.side_effect = RuntimeError("unreadable")
    page.locator.return_value.all.return_value = [dialog]
    m.dump_debug_info(page, "pkg")
    assert "could not read dialog" in capsys.readouterr().out


def test_make_public_find_open_dialog_count_exception_continues(monkeypatch):
    m = load("make_public_browser")
    page = MagicMock()
    bad = MagicMock()
    bad.count.side_effect = RuntimeError("boom")
    good = MagicMock()
    good.count.return_value = 1
    good.first.is_visible.return_value = True
    page.locator.return_value.filter.side_effect = [bad, bad, bad, good]
    assert m.find_open_dialog(page, "hint") is good.first


def test_make_public_find_open_dialog_returns_none_when_all_candidates_fail(monkeypatch):
    m = load("make_public_browser")
    page = MagicMock()
    bad = MagicMock()
    bad.count.side_effect = RuntimeError("boom")
    page.locator.return_value.filter.side_effect = [bad, bad, bad, bad]
    assert m.find_open_dialog(page, "hint") is None


def test_make_public_revert_always_private_exception(monkeypatch, tmp_path, capsys):
    m = load("make_public_browser")
    monkeypatch.setattr(m, "AUTH_STATE_FILE", str(tmp_path / "auth"))
    Path(m.AUTH_STATE_FILE).write_text("x")
    monkeypatch.setattr(m, "sync_playwright", lambda: FakePlaywright(MagicMock()))
    monkeypatch.setattr(m, "set_package_visibility", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["x", "--revert-always-private", "--delay", "0"])
    m.main()
    assert "error: boom" in capsys.readouterr().out


def test_make_public_transient_retry_then_permanent_error(monkeypatch, tmp_path, capsys):
    m = load("make_public_browser")
    monkeypatch.setattr(m, "AUTH_STATE_FILE", str(tmp_path / "auth"))
    Path(m.AUTH_STATE_FILE).write_text("x")
    monkeypatch.setattr(m, "ORG_PACKAGES_FILE", str(tmp_path / "org"))
    Path(m.ORG_PACKAGES_FILE).write_text("pkgA\tprivate\npkgB\tprivate\n")
    monkeypatch.setattr(m, "LOG_FILE", str(tmp_path / "log"))
    monkeypatch.setattr(m, "sync_playwright", lambda: FakePlaywright(MagicMock()))
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)

    calls = {"pkgA": 0}

    def fake_set_package_public(page, org, name):
        if name == "pkgA":
            calls["pkgA"] += 1
            if calls["pkgA"] < 2:
                raise RuntimeError("net::ERR_CONNECTION_RESET")
            return "changed"
        raise RuntimeError("permanent failure")

    monkeypatch.setattr(m, "set_package_public", fake_set_package_public)
    monkeypatch.setattr(sys, "argv", ["x", "--continue-on-fail", "--delay", "0"])
    m.main()
    out = capsys.readouterr().out
    assert "network hiccup" in out
    assert "error: permanent failure" in out


# ── prepare_real_data_facs.py ─────────────────────────────────────────────────

def test_prepare_reservoir_sampling_replaces_existing_entry(tmp_path):
    p = load("prepare_real_data_facs")
    import gzip
    vcf = tmp_path / "x.vcf.gz"
    lines = ["# header"]
    for i in range(1, 6):
        lines.append(f"1\t{i}\t.\tA\tT\t.\t.\tCLNSIG=Pathogenic")
    lines.append("1\t6\t.\tC\tG\t.\t.\tCLNSIG=Benign")
    with gzip.open(vcf, "wt") as f:
        f.write("\n".join(lines))
    buckets = p.parse_clinvar_variants(vcf, n_per_class=1, seed=1)
    assert len(buckets["Pathogenic"]) == 1
    # seed=1 makes the reservoir-sampling replacement (bucket[j] = variant)
    # trigger twice (variants at pos "2" and pos "4"), so the surviving
    # entry is not the first one seen (pos "1").
    assert buckets["Pathogenic"][0]["pos"] == "4"


def test_prepare_extract_gerp_scalar_value():
    p = load("prepare_real_data_facs")
    assert p.extract_gerp({"dbnsfp": {"gerp++_rs": 3}}) == 3


def test_prepare_main_exits_on_empty_bucket(monkeypatch, tmp_path):
    p = load("prepare_real_data_facs")
    monkeypatch.setattr(p, "download_clinvar", lambda *a, **k: None)
    monkeypatch.setattr(p, "parse_clinvar_variants", lambda *a, **k: {"Pathogenic": [], "Benign": []})
    monkeypatch.setattr(
        sys, "argv",
        ["x", "--out-dir", str(tmp_path), "--clinvar-cache", str(tmp_path / "c.vcf.gz")],
    )
    with pytest.raises(SystemExit):
        p.main()


# ── pubmed/reindex_one_shard.py ──────────────────────────────────────────────────────

def _reindex_common_mocks(monkeypatch, r, inp, stage):
    monkeypatch.setattr(r, "INPUT_DIR", inp)
    monkeypatch.setattr(r, "STAGING_DIR", stage)
    monkeypatch.setattr(r, "BATCH_SIZE", 1)
    monkeypatch.setattr(r.faiss, "IndexFlatIP", lambda d: FakeIndex(d))
    monkeypatch.setattr(r.faiss, "write_index", lambda obj, path: Path(path).write_text("fake"))
    model = MagicMock(device="cuda")
    model.parameters.return_value = iter([SimpleNamespace(dtype="float16")])
    model.encode.return_value = np.ones((1, r.DIMENSION), dtype=np.float32)
    monkeypatch.setattr(r, "SentenceTransformer", lambda *a, **k: model)
    monkeypatch.setattr(r.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(r.torch.cuda, "get_device_name", lambda _: "fake")
    monkeypatch.setattr(r.torch.cuda, "mem_get_info", lambda: (1, 2))
    monkeypatch.setattr(r.torch.cuda, "synchronize", lambda: None)


def test_reindex_empty_batch_skipped_then_index_dimension_mismatch(monkeypatch, tmp_path):
    r = load("pubmed.reindex_one_shard")
    inp, stage = tmp_path / "in", tmp_path / "stage"
    inp.mkdir()
    (inp / "1.txt").write_text(" ")  # blank -> its own empty batch (BATCH_SIZE=1) -> "continue"
    (inp / "2.txt").write_text("hello")
    _reindex_common_mocks(monkeypatch, r, inp, stage)
    monkeypatch.setattr(r.faiss, "read_index", lambda path: SimpleNamespace(d=r.DIMENSION + 1, ntotal=0))
    with pytest.raises(RuntimeError, match="Invalid index dimension"):
        r.main()


def test_reindex_ntotal_mismatch(monkeypatch, tmp_path):
    r = load("pubmed.reindex_one_shard")
    inp, stage = tmp_path / "in2", tmp_path / "stage2"
    inp.mkdir()
    (inp / "1.txt").write_text("hello")
    _reindex_common_mocks(monkeypatch, r, inp, stage)
    monkeypatch.setattr(r.faiss, "read_index", lambda path: SimpleNamespace(d=r.DIMENSION, ntotal=999))
    with pytest.raises(RuntimeError, match="FAISS vector count and PMID count do not match"):
        r.main()


# ── pubmed/reindex_one_shard_ollama.py ───────────────────────────────────────────────

def test_reindex_ollama_empty_batch_skipped_then_ntotal_mismatch(monkeypatch, tmp_path):
    r = load("pubmed.reindex_one_shard_ollama")
    inp, stage = tmp_path / "in", tmp_path / "stage"
    inp.mkdir()
    (inp / "1.txt").write_text(" ")  # blank -> its own empty batch (BATCH_SIZE=1) -> "continue"
    (inp / "2.txt").write_text("hello")
    monkeypatch.setattr(r, "INPUT_DIR", inp)
    monkeypatch.setattr(r, "STAGING_DIR", stage)
    monkeypatch.setattr(r, "BATCH_SIZE", 1)
    monkeypatch.setattr(r.faiss, "IndexFlatIP", lambda d: FakeIndex(d))
    monkeypatch.setattr(r.faiss, "normalize_L2", lambda x: x)
    monkeypatch.setattr(r.faiss, "write_index", lambda obj, path: Path(path).write_text("fake"))
    monkeypatch.setattr(r.faiss, "read_index", lambda path: SimpleNamespace(d=r.DIMENSION, ntotal=999))
    response = Mock()
    response.json.return_value = {"embeddings": [[1.0] * r.DIMENSION]}
    monkeypatch.setattr(r.requests, "post", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="FAISS count and PMID count differ"):
        r.main()


# ── setup_beta_project.py ─────────────────────────────────────────────────────

def test_setup_link_issues_skips_missing_node_id(monkeypatch):
    s = load("setup_beta_project")
    monkeypatch.setattr(s, "gql", Mock(side_effect=AssertionError("should not be called")))
    s.link_issues_to_project("proj", [{"repo": "r", "number": 1}], dry_run=False)


def test_setup_main_warns_on_token_owner_mismatch(monkeypatch):
    s = load("setup_beta_project")
    monkeypatch.setattr(s, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(s, "gql", lambda *a: {"viewer": {"id": "id", "login": "someone-else"}})
    monkeypatch.setattr(s, "setup_labels", lambda *a: None)
    monkeypatch.setattr(s, "create_all_issues", lambda *a: [])
    monkeypatch.setattr(sys, "argv", ["x", "--issues-only", "--dry-run"])
    s.main()


# ── pubmed/sync_pubmed_updates.py ────────────────────────────────────────────────────

def test_sync_parse_xml_skips_article_missing_pmid(tmp_path):
    s = load("pubmed.sync_pubmed_updates")
    import gzip
    xml = (
        "<PubmedArticleSet>"
        "<PubmedArticle><MedlineCitation></MedlineCitation></PubmedArticle>"
        "<PubmedArticle><MedlineCitation>"
        "<PMID>1</PMID><Article><ArticleTitle>T</ArticleTitle>"
        "<Abstract><AbstractText>Some text</AbstractText></Abstract></Article>"
        "</MedlineCitation></PubmedArticle>"
        "</PubmedArticleSet>"
    )
    path = tmp_path / "updates.xml.gz"
    with gzip.open(path, "wt") as f:
        f.write(xml)
    result = s.parse_xml(path)
    assert len(result) == 1
    assert result[0]["pmid"] == "1"
