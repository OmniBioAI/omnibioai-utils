import importlib
import json
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


class FakeLocator:
    def __init__(self, count=1, visible=True, error=None):
        self._count, self._visible, self._error = count, visible, error
        self.first = self
    def count(self): return self._count
    def is_visible(self): return self._visible
    def filter(self, **kwargs): return self
    def __getattr__(self, name):
        if self._error:
            raise self._error
        return lambda *a, **k: None


def test_browser_helpers_and_delete_paths(monkeypatch, tmp_path):
    d = load("delete_packages_browser")
    monkeypatch.setattr(d, "LOG_FILE", str(tmp_path / "done.log"))
    assert d.load_done_log() == set()
    d.mark_done("a/b")
    assert d.load_done_log() == {"a/b"}
    page = MagicMock()
    page.locator.return_value = FakeLocator(1, True)
    assert d.find_open_dialog(page, "x") is not None

    page.title.return_value = "Page not found"
    assert d.delete_package(page, "org", "a/b") == "page_not_found"
    page.reset_mock(); page.title.return_value = "Your Packages"; page.url = "https://github.com/org/packages"
    assert d.delete_package(page, "org", "a/b") == "page_not_found"
    page.reset_mock(); page.title.return_value = "Settings"; page.url = "https://github.com/users/org/packages/container/a%2Fb/settings"
    page.get_by_role.return_value.click.side_effect = d.PWTimeout("no button")
    monkeypatch.setattr(d, "dump_debug_info", Mock())
    assert d.delete_package(page, "org", "a/b") == "no_delete_button"


def test_browser_delete_confirmation_fallbacks(monkeypatch):
    d = load("delete_packages_browser")
    page = MagicMock(); page.title.return_value = "Settings"; page.url = "/settings"
    button = page.get_by_role.return_value
    button.click.side_effect = [None, d.PWTimeout("scoped submit"), None]
    dialog = MagicMock()
    dialog.get_by_role.return_value.first.fill.side_effect = d.PWTimeout("scoped textbox")
    monkeypatch.setattr(d, "find_open_dialog", lambda *_: dialog)
    monkeypatch.setattr(d, "dump_debug_info", Mock())
    assert d.delete_package(page, "org", "pkg/name") == "deleted"
    assert page.locator.called


def test_delete_main_dry_run_abort_and_missing(monkeypatch, tmp_path, capsys):
    d = load("delete_packages_browser")
    monkeypatch.setattr(d, "AUTH_STATE_FILE", str(tmp_path / "auth"))
    monkeypatch.setattr(d, "PACKAGES_FILE", str(tmp_path / "packages"))
    monkeypatch.setattr(d, "LOG_FILE", str(tmp_path / "log"))
    monkeypatch.setattr(sys, "argv", ["x"])
    with pytest.raises(SystemExit): d.main()
    Path(d.AUTH_STATE_FILE).write_text("state")
    Path(d.PACKAGES_FILE).write_text("one\ntwo\none\n")
    monkeypatch.setattr(sys, "argv", ["x", "--owner", "acme"])
    d.main(); assert "would delete" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["x", "--confirm-delete"])
    monkeypatch.setattr("builtins.input", lambda: "NO")
    d.main(); assert "Aborted" in capsys.readouterr().out


class FakePlaywright:
    def __init__(self, page): self.page = page
    def __enter__(self): return self
    def __exit__(self, *args): pass
    @property
    def chromium(self):
        page = self.page
        class Chromium:
            def launch(self, **kwargs):
                class Browser:
                    def new_context(self, **kwargs):
                        class Context:
                            def new_page(self): return page
                        return Context()
                    def close(self): pass
                return Browser()
        return Chromium()


def test_public_helpers_and_visibility_paths(monkeypatch, tmp_path):
    m = load("make_public_browser")
    monkeypatch.setattr(m, "ORG_PACKAGES_FILE", str(tmp_path / "org.txt"))
    (tmp_path / "org.txt").write_text("a\tprivate\nb\tpublic\nomnibioai-app\tprivate\nbad\n")
    assert m.load_candidate_packages("x") == ["a"]
    monkeypatch.setattr(m, "LOG_FILE", str(tmp_path / "done")); m.mark_done("a"); assert m.load_done_log() == {"a"}
    page = MagicMock(); page.locator.return_value = FakeLocator(1, True)
    assert m.find_open_dialog(page, "x") is not None
    page.title.return_value = "Page not found"
    assert m.set_package_visibility(page, "o", "p") == "page_not_found"
    page.reset_mock(); page.title.return_value = "Settings"; page.get_by_text.return_value.count.return_value = 1
    assert m.set_package_visibility(page, "o", "p") == "already_public"


def test_public_visibility_confirmation_and_main(monkeypatch, tmp_path, capsys):
    m = load("make_public_browser")
    page = MagicMock(); page.title.return_value = "Settings"; page.url = "/settings"
    page.get_by_text.return_value.count.return_value = 0
    page.get_by_role.return_value.click.side_effect = [None, m.PWTimeout("dialog submit"), None]
    dialog = MagicMock(); dialog.locator.return_value.check.side_effect = m.PWTimeout("radio")
    monkeypatch.setattr(m, "find_open_dialog", lambda *_: dialog)
    monkeypatch.setattr(m, "dump_debug_info", Mock())
    assert m.set_package_public(page, "org", "pkg") == "changed"
    monkeypatch.setattr(m, "AUTH_STATE_FILE", str(tmp_path / "auth")); Path(m.AUTH_STATE_FILE).write_text("x")
    monkeypatch.setattr(m, "ORG_PACKAGES_FILE", str(tmp_path / "org.txt")); (tmp_path / "org.txt").write_text("pkg\tprivate\n")
    monkeypatch.setattr(m, "LOG_FILE", str(tmp_path / "log"))
    monkeypatch.setattr(m, "sync_playwright", lambda: FakePlaywright(page))
    monkeypatch.setattr(m, "set_package_public", lambda *_: "changed")
    monkeypatch.setattr(sys, "argv", ["x", "--delay", "0"])
    m.main(); assert "Summary" in capsys.readouterr().out


class FakeIndex:
    def __init__(self, dim): self.d, self.ntotal, self.rows = dim, 0, []
    def add(self, arr): self.rows.append(arr); self.ntotal += len(arr)


def test_reindex_cuda_success_and_validation(monkeypatch, tmp_path):
    r = load("reindex_one_shard")
    inp, stage = tmp_path / "in", tmp_path / "stage"; inp.mkdir()
    (inp / "1.txt").write_text("hello"); (inp / "2.txt").write_text(" ")
    monkeypatch.setattr(r, "INPUT_DIR", inp); monkeypatch.setattr(r, "STAGING_DIR", stage)
    index = FakeIndex(r.DIMENSION); monkeypatch.setattr(r.faiss, "IndexFlatIP", lambda d: index)
    monkeypatch.setattr(r.faiss, "write_index", lambda obj, path: Path(path).write_text("fake"))
    monkeypatch.setattr(r.faiss, "read_index", lambda path: index)
    model = MagicMock(device="cuda"); model.parameters.return_value = iter([SimpleNamespace(dtype="float16")])
    model.encode.return_value = np.ones((1, r.DIMENSION), dtype=np.float32)
    monkeypatch.setattr(r, "SentenceTransformer", lambda *a, **k: model)
    monkeypatch.setattr(r.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(r.torch.cuda, "get_device_name", lambda _: "fake")
    monkeypatch.setattr(r.torch.cuda, "mem_get_info", lambda: (1, 2)); monkeypatch.setattr(r.torch.cuda, "synchronize", lambda: None)
    r.main()
    assert json.loads((stage / "pmid_map.json").read_text()) == ["1"]
    assert json.loads((stage / "metadata.json").read_text())["vectors"] == 1


def test_reindex_cuda_error_branches(monkeypatch, tmp_path):
    r = load("reindex_one_shard"); monkeypatch.setattr(r.torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="CUDA"): r.main()
    monkeypatch.setattr(r.torch.cuda, "is_available", lambda: True); empty = tmp_path / "empty"; empty.mkdir()
    monkeypatch.setattr(r, "INPUT_DIR", empty); monkeypatch.setattr(r, "STAGING_DIR", tmp_path / "stage")
    monkeypatch.setattr(r.torch.cuda, "get_device_name", lambda _: "fake"); monkeypatch.setattr(r.torch.cuda, "mem_get_info", lambda: (1, 2))
    with pytest.raises(RuntimeError, match="No abstracts"): r.main()


def test_reindex_ollama_success_and_errors(monkeypatch, tmp_path):
    r = load("reindex_one_shard_ollama"); inp, stage = tmp_path / "in", tmp_path / "stage"; inp.mkdir()
    (inp / "1.txt").write_text("hello"); (inp / "2.txt").write_text(" ")
    monkeypatch.setattr(r, "INPUT_DIR", inp); monkeypatch.setattr(r, "STAGING_DIR", stage)
    index = FakeIndex(r.DIMENSION); monkeypatch.setattr(r.faiss, "IndexFlatIP", lambda d: index); monkeypatch.setattr(r.faiss, "normalize_L2", lambda x: x)
    monkeypatch.setattr(r.faiss, "write_index", lambda obj, path: Path(path).write_text("fake")); monkeypatch.setattr(r.faiss, "read_index", lambda path: index)
    response = Mock(); response.json.return_value = {"embeddings": [[1.0] * r.DIMENSION]}; monkeypatch.setattr(r.requests, "post", Mock(return_value=response))
    r.main(); assert json.loads((stage / "pmid_map.json").read_text()) == ["1"]
    empty = tmp_path / "empty"; empty.mkdir(); monkeypatch.setattr(r, "INPUT_DIR", empty)
    with pytest.raises(RuntimeError, match="No abstracts"): r.main()
    bad = Mock(); bad.json.return_value = {"embeddings": [[1.0, 2.0]]}; monkeypatch.setattr(r.requests, "post", Mock(return_value=bad)); monkeypatch.setattr(r, "INPUT_DIR", inp)
    with pytest.raises(RuntimeError, match="Unexpected dimension"): r.main()

def test_prepare_real_data_branches(monkeypatch, tmp_path):
    p = load("prepare_real_data_facs")
    cached = tmp_path / "cached.gz"; cached.write_bytes(b"x")
    p.download_clinvar(cached, False)
    class Resp:
        def __init__(self): self.parts = [b"a", b"b"]
        def read(self, n): return self.parts.pop(0) if self.parts else b""
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(p.urllib.request, "urlopen", lambda *a, **k: Resp())
    fresh = tmp_path / "nested" / "new.gz"; p.download_clinvar(fresh, True); assert fresh.read_bytes() == b"ab"
    vcf = tmp_path / "x.vcf.gz"
    lines = ["# header", "bad", "1\t1\t.\tAT\tG\t.\t.\tCLNSIG=Pathogenic", "1\t2\t.\tA\tT\t.\t.\tNOPE", "1\t3\t.\tA\tT\t.\t.\tCLNSIG=Conflicting", "1\t4\t.\tA\tT\t.\t.\tCLNSIG=Pathogenic", "1\t5\t.\tC\tG\t.\t.\tCLNSIG=Benign"]
    import gzip
    with gzip.open(vcf, "wt") as f: f.write("\n".join(lines))
    got = p.parse_clinvar_variants(vcf, 1, 1); assert set(got) == {"Pathogenic", "Benign"}
    assert p.to_hgvs("7", "1", "A", "T") == "chr7:g.1A>T"; assert p.to_hgvs("chr7", "1", "A", "T").startswith("chr7")
    assert p.extract_score({"x": []}, "x") is None; assert p.extract_score({"x": 1}, "x") == 1; assert p.extract_score({"x": "bad"}, "x.y") is None
    assert p.extract_gerp({}) is None; assert p.extract_gerp({"dbnsfp": {"gerp++_rs": []}}) is None; assert p.extract_gerp({"dbnsfp": {"gerp++_rs": [2]}}) == 2
    resp = Mock(); resp.json.return_value = [{"query": "chr1:g.1A>T"}]; monkeypatch.setattr(p.requests, "post", lambda *a, **k: resp)
    assert p.annotate_batch(["x"]) == {"chr1:g.1A>T": {"query": "chr1:g.1A>T"}}
    monkeypatch.setattr(p.requests, "post", Mock(side_effect=RuntimeError("down"))); monkeypatch.setattr(p.time, "sleep", lambda _: None)
    assert p.annotate_batch(["x"], 2) == {}
    monkeypatch.setattr(p, "annotate_batch", lambda ids: {})
    rows = p.annotate_variants({"Pathogenic": [{"chrom":"1","pos":"1","ref":"A","alt":"T"}], "Benign": []}, 10)
    assert rows[0]["pathogenicity"] == "Pathogenic"


def test_agent_eval_remaining_paths(monkeypatch, tmp_path, capsys):
    a = load("agent_tool_selection_eval")
    monkeypatch.setattr(a, "yaml", None)
    with pytest.raises(SystemExit): a.load_corpus(None, str(tmp_path / "missing"), None, None, [], False)
    monkeypatch.setattr(a, "yaml", Mock())
    with pytest.raises(SystemExit): a.load_corpus(str(tmp_path / "missing.yaml"), None, None, None, [], False)
    assert a.cosine_sim(np.zeros(2), np.ones(2)) == 0.0
    response = Mock(); response.json.return_value = {"embedding": [1, 2]}; monkeypatch.setattr(a.requests, "post", lambda *x, **y: response)
    assert a.ollama_embed("http://x", "m", "t").dtype == np.float32
    response.json.return_value = {"message": {}}; assert a.call_ollama_chat_with_tools("http://x", "m", "p", [a.MOCK_TOOLS[0]])[0] is None
    result = a.ItemResult("i", "c", "p", "expected"); result.in_shortlist = False; a.print_summary([result]); assert "not in shortlist" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path / "out.json")])
    monkeypatch.setattr(a, "load_corpus", lambda *x: a.MOCK_TOOLS[:1]); monkeypatch.setattr(a, "run_eval", lambda *x: [a.ItemResult("i", "c", "p", "x")])
    a.main(); assert (tmp_path / "out.json").exists()


def test_setup_beta_project_boundaries(monkeypatch, capsys):
    s = load("setup_beta_project")
    response = Mock(); response.json.return_value = {"data": {"x": 1}}; monkeypatch.setattr(s.requests, "post", lambda *x, **y: response); assert s.gql("q") == {"x": 1}
    response.json.return_value = {"errors": ["bad"]}
    with pytest.raises(RuntimeError): s.gql("q", {"x": 1})
    response.json.return_value = {"ok": 1}; assert s.rest_post("/x", {}) == {"ok": 1}; monkeypatch.setattr(s.requests, "get", lambda *x, **y: response); assert s.rest_get("/x") == {"ok": 1}
    assert s.create_project("id", True) == "DRY_RUN_PROJECT_ID"; assert s.add_single_select_field("p", "f", ["a"], True).startswith("DRY")
    monkeypatch.setattr(s, "gql", lambda *x: {"createProjectV2": {"projectV2": {"id":"p", "number":1, "url":"u"}}}); assert s.create_project("id")[0] == "p"
    assert s.create_project_fields("p") is None
    monkeypatch.setattr(s, "rest_get", lambda *x: {})
    assert s.ensure_label("r", "l", "c") is None
    def fail_get(*x): raise s.requests.HTTPError(response=SimpleNamespace(status_code=404))
    monkeypatch.setattr(s, "rest_get", fail_get); monkeypatch.setattr(s, "rest_post", lambda *x: {})
    s.ensure_label("r", "l", "c"); monkeypatch.setattr(s, "rest_post", Mock(side_effect=RuntimeError("x"))); s.ensure_label("r", "l", "c")
    assert s.create_issue("r", "title", "body", "Low", "cat", [], True)["number"] == 0
    monkeypatch.setattr(s, "rate_limited_call", lambda *x: {"number": 1}); monkeypatch.setattr(s.time, "sleep", lambda _: None); assert s.create_issue("r","t","b","Low","c",[])['number'] == 1
    monkeypatch.setattr(s, "ISSUES", s.ISSUES[:1]); monkeypatch.setattr(s, "create_issue", lambda *x: {"number":0,"node_id":"DRY_NODE","html_url":"DRY"}); assert len(s.create_all_issues(True)) == 1
    s.link_issues_to_project("p", [{"repo":"r","number":1}, {"repo":"r","number":2,"node_id":"DRY_NODE"}], True)
    monkeypatch.setattr(s, "gql", Mock(side_effect=[{}, RuntimeError("bad")])); s.link_issues_to_project("p", [{"repo":"r","number":1,"node_id":"n"},{"repo":"r","number":2,"node_id":"n2"}])
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run"]); monkeypatch.setattr(s, "GITHUB_TOKEN", "token"); monkeypatch.setattr(s, "gql", lambda *x: {"viewer":{"id":"id", "login":s.OWNER}}); monkeypatch.setattr(s, "setup_labels", lambda *x: None); monkeypatch.setattr(s, "create_all_issues", lambda *x: []); s.main()


def test_download_and_sync_remaining_branches(monkeypatch, tmp_path):
    d = load("download_references"); dl = d.ReferenceDownloader(tmp_path)
    monkeypatch.setattr(d.subprocess, "run", lambda *x, **y: None); assert dl.download_file("u", tmp_path / "x", "x") is True
    assert dl.registry["downloads"] if "downloads" in dl.registry else True
    dl.registry = {}; dl.flush_registry(); assert (tmp_path / d.REGISTRY_FILENAME).exists()
    s = load("sync_pubmed_updates"); state_file = tmp_path / "state"; monkeypatch.setattr(s, "STATE_FILE", state_file); state_file.write_text('{"x": 1}'); assert s.load_state()["x"] == 1
    class BadFTP:
        def login(self): pass
        def cwd(self, x): pass
        def retrlines(self, x, cb): pass
        def quit(self): raise RuntimeError("closed")
    monkeypatch.setattr(s.ftplib, "FTP", lambda *_: BadFTP()); assert s.get_all_update_files() == []
    bad = tmp_path / "bad.gz"; bad.write_bytes(b"bad"); assert s.parse_xml(bad) == []
    monkeypatch.setattr(s, "load_state", lambda: {"files_processed": []}); monkeypatch.setattr(s, "get_all_update_files", lambda: ["bad"]); monkeypatch.setattr(s, "download_file", lambda _: bad); monkeypatch.setattr(s, "parse_xml", lambda _: (_ for _ in ()).throw(RuntimeError("parse"))); s.main()

def test_browser_debug_and_failure_branches(monkeypatch, capsys):
    for name in ("delete_packages_browser", "make_public_browser"):
        mod = load(name); page = MagicMock(); page.url = "u"; page.title.return_value = "t"
        page.screenshot.side_effect = RuntimeError("shot"); page.get_by_role.return_value.all_text_contents.side_effect = RuntimeError("buttons")
        page.locator.return_value.all_text_contents.side_effect = RuntimeError("heads")
        page.get_by_role.return_value.all.side_effect = RuntimeError("radios")
        mod.dump_debug_info(page, "a/b")
        assert "DEBUG" in capsys.readouterr().out
    d = load("delete_packages_browser"); page = MagicMock(); page.title.return_value = "Settings"; page.url = "/settings"; page.get_by_role.return_value.click.return_value = None
    monkeypatch.setattr(d, "find_open_dialog", lambda *_: None); monkeypatch.setattr(d, "dump_debug_info", lambda *x: None); assert d.delete_package(page,"o","p") == "no_dialog"
    dialog = MagicMock(); monkeypatch.setattr(d, "find_open_dialog", lambda *_: dialog); dialog.get_by_role.return_value.first.fill.side_effect = d.PWTimeout("x"); page.locator.return_value.last.fill.side_effect = d.PWTimeout("x"); assert d.delete_package(page,"o","p") == "no_confirm_textbox"
    dialog.get_by_role.return_value.first.fill.side_effect = None; dialog.get_by_role.return_value.first.fill.return_value = None; dialog.get_by_role.return_value.click.side_effect = d.PWTimeout("x"); page.get_by_role.return_value.click.side_effect = [None, d.PWTimeout("x")] ; assert d.delete_package(page,"o","p") == "submit_button_not_found"


def test_browser_main_processing_paths(monkeypatch, tmp_path, capsys):
    d = load("delete_packages_browser"); Path(tmp_path / "auth").write_text("x"); Path(tmp_path / "packages").write_text("one\ntwo\n")
    for attr, val in (("AUTH_STATE_FILE", str(tmp_path/"auth")), ("PACKAGES_FILE", str(tmp_path/"packages")), ("LOG_FILE", str(tmp_path/"dlog"))): monkeypatch.setattr(d, attr, val)
    monkeypatch.setattr(d, "sync_playwright", lambda: FakePlaywright(MagicMock())); monkeypatch.setattr(d, "delete_package", Mock(side_effect=["deleted", "error: bad"])); monkeypatch.setattr(d, "mark_done", Mock()); monkeypatch.setattr(d.time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["x", "--confirm-delete", "--delay", "0"]); monkeypatch.setattr("builtins.input", lambda: "DELETE"); d.main(); assert "failed: 1" in capsys.readouterr().out
    monkeypatch.setattr(d, "delete_package", Mock(side_effect=RuntimeError("net::ERR_CONNECTION"))); monkeypatch.setattr(sys, "argv", ["x", "--confirm-delete", "--continue-on-fail", "--delay", "0"]); d.main()
    m = load("make_public_browser"); Path(tmp_path / "org").write_text("x"); monkeypatch.setattr(m, "AUTH_STATE_FILE", str(tmp_path/"auth")); monkeypatch.setattr(m, "ORG_PACKAGES_FILE", str(tmp_path/"org")); monkeypatch.setattr(m, "LOG_FILE", str(tmp_path/"vlog")); monkeypatch.setattr(m, "sync_playwright", lambda: FakePlaywright(MagicMock())); monkeypatch.setattr(m, "set_package_visibility", Mock(side_effect=["already_private"] * len(m.ALWAYS_PRIVATE))); monkeypatch.setattr(sys, "argv", ["x", "--revert-always-private", "--delay", "0"]); m.main()
    monkeypatch.setattr(m, "set_package_public", Mock(side_effect=["already_public", "error: bad"])); (tmp_path/"org").write_text("a\tprivate\nb\tprivate\n"); monkeypatch.setattr(sys, "argv", ["x", "--continue-on-fail", "--delay", "0"]); m.main()


def test_public_failure_return_branches(monkeypatch):
    m = load("make_public_browser"); page = MagicMock(); page.title.return_value = "Settings"; page.url = "/settings"; page.get_by_text.return_value.count.return_value = 0; monkeypatch.setattr(m, "dump_debug_info", lambda *x: None)
    page.get_by_role.return_value.click.side_effect = m.PWTimeout("x"); assert m.set_package_visibility(page,"o","p") == "no_change_button"
    page.get_by_role.return_value.click.side_effect = None; monkeypatch.setattr(m, "find_open_dialog", lambda *_: None); assert m.set_package_visibility(page,"o","p") == "no_dialog"
    dialog=MagicMock(); monkeypatch.setattr(m,"find_open_dialog",lambda *_:dialog); dialog.locator.return_value.check.side_effect=m.PWTimeout("x"); page.locator.return_value.first.check.side_effect=m.PWTimeout("x"); assert m.set_package_visibility(page,"o","p") == "no_public_radio"
    dialog.locator.return_value.check.side_effect=None; dialog.get_by_role.return_value.first.fill.side_effect=m.PWTimeout("x"); page.locator.return_value.last.fill.side_effect=m.PWTimeout("x"); assert m.set_package_visibility(page,"o","p") == "no_confirm_textbox"

def test_public_diagnostics_fallbacks_and_cli_edges(monkeypatch, tmp_path, capsys):
    m = load("make_public_browser")
    monkeypatch.setattr(m, "ORG_PACKAGES_FILE", str(tmp_path / "missing"))
    with pytest.raises(SystemExit): m.load_candidate_packages("org")
    page = MagicMock(); page.url="u"; page.title.return_value="t"; page.screenshot.return_value=None
    page.get_by_role.return_value.all_text_contents.return_value=[" Go ", ""]
    page.locator.return_value.all_text_contents.return_value=[" Head "]
    radio=MagicMock(); radio.get_attribute.side_effect=["aria", None, "id"]; page.get_by_role.return_value.all.return_value=[radio]
    dialog=MagicMock(); dialog.inner_text.return_value="dialog"; page.locator.return_value.all.return_value=[dialog]
    m.dump_debug_info(page,"pkg"); assert "Go" in capsys.readouterr().out
    page.locator.side_effect = RuntimeError("locator")
    with pytest.raises(RuntimeError): m.find_open_dialog(page,"x")
    page = MagicMock(); page.title.return_value="Settings"; page.url="/settings"; page.get_by_text.return_value.count.return_value=0; dialog=MagicMock(); monkeypatch.setattr(m,"find_open_dialog",lambda *_:dialog)
    dialog.locator.return_value.check.side_effect=m.PWTimeout("radio"); page.locator.return_value.first.check.return_value=None; page.get_by_role.return_value.click.return_value=None; dialog.get_by_role.return_value.first.fill.side_effect=m.PWTimeout("box"); page.locator.return_value.last.fill.return_value=None
    dialog.get_by_role.return_value.click.side_effect=m.PWTimeout("submit"); page.get_by_role.return_value.click.side_effect=[None,None]; assert m.set_package_visibility(page,"o","p") == "changed"
    monkeypatch.setattr(m,"AUTH_STATE_FILE",str(tmp_path/"auth")); monkeypatch.setattr(sys,"argv",["x"])
    with pytest.raises(SystemExit): m.main()
    Path(m.AUTH_STATE_FILE).write_text("x"); monkeypatch.setattr(m,"do_login",Mock()); monkeypatch.setattr(sys,"argv",["x","--login"]); m.main(); m.do_login.assert_called_once()


def test_delete_diagnostics_login_and_dialog_edges(monkeypatch, tmp_path, capsys):
    d = load("delete_packages_browser"); page=MagicMock(); page.url="u"; page.title.return_value="t"; page.screenshot.return_value=None; page.get_by_role.return_value.all_text_contents.return_value=[" Go "]; d.dump_debug_info(page,"p"); assert "Go" in capsys.readouterr().out
    page.locator.side_effect=RuntimeError("bad")
    with pytest.raises(RuntimeError): d.find_open_dialog(page,"x")
    class Ctx:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        @property
        def chromium(self): return self
        def launch(self,**k): return self
        def new_context(self,**k): return self
        def new_page(self): return page
        def storage_state(self,**k): pass
        def goto(self,*a,**k): pass
        def close(self): pass
    monkeypatch.setattr(d,"sync_playwright",lambda:Ctx()); monkeypatch.setattr("builtins.input",lambda:" "); monkeypatch.setattr(d,"AUTH_STATE_FILE",str(tmp_path/"a")); monkeypatch.setattr(sys,"argv",["x","--login"]); d.do_login(); assert "Session saved" in capsys.readouterr().out


def test_setup_main_error_and_live_branches(monkeypatch):
    s=load("setup_beta_project"); monkeypatch.setattr(s,"GITHUB_TOKEN",""); monkeypatch.setattr(sys,"argv",["x"])
    with pytest.raises(SystemExit): s.main()
    monkeypatch.setattr(s,"GITHUB_TOKEN","x"); monkeypatch.setattr(s,"gql",Mock(side_effect=RuntimeError("auth")))
    with pytest.raises(SystemExit): s.main()
    monkeypatch.setattr(s,"rest_get",Mock(side_effect=s.requests.HTTPError(response=SimpleNamespace(status_code=404)))); monkeypatch.setattr(s,"rest_post",Mock(return_value={"x":1})); s.setup_labels(False)


def test_reindex_validation_failures(monkeypatch,tmp_path):
    r=load("reindex_one_shard"); inp=tmp_path/"i"; inp.mkdir(); (inp/"1.txt").write_text("x"); monkeypatch.setattr(r,"INPUT_DIR",inp); monkeypatch.setattr(r,"STAGING_DIR",tmp_path/"s"); monkeypatch.setattr(r.torch.cuda,"is_available",lambda:True); monkeypatch.setattr(r.torch.cuda,"get_device_name",lambda _:"x"); monkeypatch.setattr(r.torch.cuda,"mem_get_info",lambda:(1,2)); monkeypatch.setattr(r,"SentenceTransformer",lambda *a,**k:SimpleNamespace(device="cuda",parameters=lambda:iter([SimpleNamespace(dtype="x")]),encode=lambda *a,**k:np.ones((1,1),dtype=np.float32))); monkeypatch.setattr(r.faiss,"IndexFlatIP",lambda d:FakeIndex(d))
    with pytest.raises(RuntimeError,match="Unexpected dimension"): r.main()
    o=load("reindex_one_shard_ollama"); monkeypatch.setattr(o,"INPUT_DIR",inp); monkeypatch.setattr(o,"STAGING_DIR",tmp_path/"os"); resp=Mock(); resp.json.return_value={"embeddings":[[1.0]*o.DIMENSION]}; monkeypatch.setattr(o.requests,"post",Mock(return_value=resp)); idx=FakeIndex(o.DIMENSION); monkeypatch.setattr(o.faiss,"IndexFlatIP",lambda d:idx); monkeypatch.setattr(o.faiss,"normalize_L2",lambda x:None); monkeypatch.setattr(o.faiss,"write_index",lambda *x:None); bad=SimpleNamespace(d=1,ntotal=1); monkeypatch.setattr(o.faiss,"read_index",lambda *x:bad)
    with pytest.raises(RuntimeError,match="Invalid dimension"): o.main()

def test_agent_api_and_yaml_loading(monkeypatch, tmp_path):
    a=load("agent_tool_selection_eval"); resp=Mock(); resp.json.return_value=[{"tool_id":"x","tags":["http","unverified"]}]; monkeypatch.setattr(a.requests,"get",lambda *x,**y:resp); tools=a.load_corpus_from_api("http://tes",["unverified"]); assert tools[0]["_verified"] is False
    d=tmp_path/"tools"; (d/"x86_64").mkdir(parents=True); (d/"a.yaml").write_text("tools:\n  - tool_id: a\n    slurm: true\n    tags: [slurm]\n"); (d/"x86_64/b.yaml").write_text("tools: []\n"); monkeypatch.setattr(a,"yaml",__import__("yaml")); assert a.load_corpus(None,str(d),["slurm"],None,[],False)[0]["_backend"] == "slurm"
    y=tmp_path/"one.yaml"; y.write_text("tools:\n  - tool_id: x\n    inputs_schema: {required: [a]}\n"); assert a.load_corpus(str(y),None,None,None,[],False)[0]["_source_file"] == "unknown"
    monkeypatch.setattr(a,"ollama_embed",lambda *x:np.ones(2)); monkeypatch.setattr(a,"call_ollama_chat_with_tools",lambda *x:(None,0.0,"bad")); monkeypatch.setattr(a,"TEST_CASES",[{"id":"i","category":"c","prompt":"p","expected_tool_id":"x"}]); assert a.run_eval("u","m","e",1,[{"tool_id":"x"}])[0].malformed_json


def test_download_cli_branches(monkeypatch,tmp_path,capsys):
    d=load("download_references"); dl=d.ReferenceDownloader(tmp_path); dl.registry={}; dl.print_status(); assert "Registry: empty" in capsys.readouterr().out; assert d._now(); monkeypatch.setattr(sys,"argv",["x","--base-dir",str(tmp_path),"--scaffold","--dry-run"]); d.main(); monkeypatch.setattr(sys,"argv",["x","--base-dir",str(tmp_path)]); 
    with pytest.raises(SystemExit): d.main()
    monkeypatch.setattr(sys,"argv",["x","--base-dir",str(tmp_path),"--assemblies","UNKNOWN"]); # parser rejects this before business logic; validate separately
    with pytest.raises(SystemExit): d.parse_args()
    monkeypatch.setattr(d,"ASSEMBLY_MAP",{"X":("x","missing")}); monkeypatch.setattr(sys,"argv",["x","--base-dir",str(tmp_path),"--assemblies","X"]); d.main(); assert "No downloader" in capsys.readouterr().out


def test_public_radio_and_submit_failures(monkeypatch):
    m=load("make_public_browser"); page=MagicMock(); page.title.return_value="Settings"; page.url="/settings"; page.get_by_text.return_value.count.return_value=0; dialog=MagicMock(); monkeypatch.setattr(m,"find_open_dialog",lambda *_:dialog); page.get_by_role.return_value.click.return_value=None; dialog.locator.return_value.check.return_value=None; dialog.get_by_role.return_value.first.fill.return_value=None; dialog.get_by_role.return_value.click.side_effect=m.PWTimeout("x"); page.get_by_role.return_value.click.side_effect=[None,m.PWTimeout("x")]; monkeypatch.setattr(m,"dump_debug_info",lambda *x:None); assert m.set_package_visibility(page,"o","p") == "submit_button_not_found"

def test_final_cli_error_branches(monkeypatch,tmp_path):
    d=load("delete_packages_browser"); monkeypatch.setattr(d,"AUTH_STATE_FILE",str(tmp_path/"a")); monkeypatch.setattr(d,"PACKAGES_FILE",str(tmp_path/"p")); Path(d.AUTH_STATE_FILE).write_text("x"); monkeypatch.setattr(d,"do_login",Mock()); monkeypatch.setattr(sys,"argv",["x","--login"]); d.main(); Path(d.PACKAGES_FILE).write_text("x\n"); monkeypatch.setattr(sys,"argv",["x"]); d.main(); Path(d.PACKAGES_FILE).unlink();
    a=load("agent_tool_selection_eval")
    td=tmp_path/"td"; td.mkdir(); monkeypatch.setattr(a,"yaml",None)
    with pytest.raises(SystemExit): a.load_corpus(None,str(td),None,None,[],False)
    with pytest.raises(SystemExit): a.load_corpus(str(tmp_path/"x"),None,None,None,[],False)
    s=load("setup_beta_project"); monkeypatch.setattr(s,"GITHUB_TOKEN","x"); monkeypatch.setattr(s,"gql",lambda *x:{"viewer":{"id":"id","login":s.OWNER}}); monkeypatch.setattr(s,"setup_labels",lambda *x:None); monkeypatch.setattr(s,"create_all_issues",lambda *x:[]); monkeypatch.setattr(sys,"argv",["x","--issues-only","--dry-run"]); s.main()
    monkeypatch.setattr(s,"create_project",lambda *x:("pid",1)); monkeypatch.setattr(s,"create_project_fields",lambda *x:None); monkeypatch.setattr(s,"link_issues_to_project",lambda *x:None); monkeypatch.setattr(s,"create_all_issues",lambda *x:[{"priority":"Low","repo":"r"}]); monkeypatch.setattr(sys,"argv",["x","--output",str(tmp_path/"issues")]); s.main(); assert (tmp_path/"issues").exists()


def test_public_main_auth_and_stop(monkeypatch,tmp_path):
    m=load("make_public_browser"); monkeypatch.setattr(m,"AUTH_STATE_FILE",str(tmp_path/"a")); monkeypatch.setattr(sys,"argv",["x"])
    with pytest.raises(SystemExit): m.main()
    Path(m.AUTH_STATE_FILE).write_text("x"); monkeypatch.setattr(m,"ORG_PACKAGES_FILE",str(tmp_path/"o")); (tmp_path/"o").write_text("a\tprivate\n"); monkeypatch.setattr(m,"sync_playwright",lambda:FakePlaywright(MagicMock())); monkeypatch.setattr(m,"set_package_public",lambda *x: "error: bad"); monkeypatch.setattr(sys,"argv",["x","--delay","0"]); m.main()
