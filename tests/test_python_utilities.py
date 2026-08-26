import csv
import gzip
import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load(name):
    return importlib.import_module(name)


def test_create_new_chunks_helpers_and_chunking(tmp_path, monkeypatch):
    mod = load("create_new_chunks")
    data = tmp_path / "data"
    domain = data / "topic"
    domain.mkdir(parents=True)
    fresh = domain / "1.json"
    fresh.write_text(json.dumps({"pmid": "1", "title": "one"}))
    old = domain / "2.json"
    old.write_text(json.dumps({"pmid": "2"}))
    old.touch()
    monkeypatch.setattr(mod, "DATA_DIR", data)
    monkeypatch.setattr(mod, "CHUNK_SIZE", 2)
    assert fresh in mod.get_updated_pmids()
    assert mod.get_next_chunk_number() == 57
    (data / "_general_corpus_chunk057").mkdir()
    (data / "_general_corpus_chunkbad").mkdir()
    assert mod.get_next_chunk_number() == 58

    state_file = tmp_path / "state.json"
    state_file.write_text('{"total_new": 3}')
    monkeypatch.setattr(mod, "STATE_FILE", state_file)
    assert mod.find_new_abstracts()["total_new"] == 3

    second = domain / "3.json"
    second.write_text(json.dumps({"pmid": "3"}))
    monkeypatch.setattr(mod, "get_updated_pmids", lambda: [fresh, second])
    mod.create_chunks_from_updates()
    output = data / "_general_corpus_chunk058"
    assert json.loads((output / "1.json").read_text())["title"] == "one"
    assert (output / "3.json").exists()


def test_create_new_chunks_skips_bad_file_and_empty_run(tmp_path, monkeypatch):
    mod = load("create_new_chunks")
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "get_updated_pmids", lambda: [])
    assert mod.create_chunks_from_updates() is None
    bad = tmp_path / "bad.json"
    bad.write_text("not-json")
    good = tmp_path / "good.json"
    good.write_text('{"title": "fallback"}')
    monkeypatch.setattr(mod, "get_updated_pmids", lambda: [bad, good])
    monkeypatch.setattr(mod, "CHUNK_SIZE", 99)
    mod.create_chunks_from_updates()
    assert json.loads((tmp_path / "_general_corpus_chunk057" / "unknown.json").read_text())["title"] == "fallback"


def test_pubmed_state_and_file_filtering(tmp_path, monkeypatch):
    mod = load("sync_pubmed_updates")
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(mod, "STATE_FILE", state_file)
    initial = mod.load_state()
    assert initial["files_processed"] == []
    state = {"last_file": "b.xml.gz", "files_processed": ["c.xml.gz"]}
    mod.save_state(state)
    assert json.loads(state_file.read_text()) == state
    assert mod.get_new_files(["a.xml.gz", "b.xml.gz", "c.xml.gz", "d.xml.gz"], state) == ["d.xml.gz"]
    assert mod.get_new_files(["a.xml.gz"], {"files_processed": []}) == ["a.xml.gz"]


def test_pubmed_ftp_listing_and_download_paths(tmp_path, monkeypatch):
    mod = load("sync_pubmed_updates")

    class FakeFTP:
        def __init__(self, host): self.host = host
        def login(self): pass
        def cwd(self, path): self.path = path
        def retrlines(self, command, callback):
            callback("-rw-r--r-- 1 x x 1 Jan 1 good.xml.gz")
            callback("-rw-r--r-- 1 x x 1 Jan 1 good.xml.gz.md5")
        def quit(self): pass

    monkeypatch.setattr(mod.ftplib, "FTP", FakeFTP)
    assert mod.get_all_update_files() == ["good.xml.gz"]
    monkeypatch.setattr(mod, "DOWNLOAD_DIR", tmp_path)
    cached = tmp_path / "cached.xml.gz"
    cached.write_bytes(b"cached")
    assert mod.download_file("cached.xml.gz") == cached
    response = Mock()
    response.iter_content.return_value = [b"a", b"b"]
    monkeypatch.setattr(mod.requests, "get", Mock(return_value=response))
    downloaded = mod.download_file("new.xml.gz")
    assert downloaded.read_bytes() == b"ab"
    mod.requests.get.assert_called_once()


def test_pubmed_xml_parse_and_update(tmp_path, monkeypatch):
    mod = load("sync_pubmed_updates")
    xml = """<PubmedArticleSet>
      <PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>Title</ArticleTitle>
      <Abstract><AbstractText>First</AbstractText><AbstractText>Second</AbstractText></Abstract>
      <Journal><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>
      <MeshHeadingList><MeshHeading><DescriptorName>Term</DescriptorName></MeshHeading></MeshHeadingList>
      </Article></MedlineCitation></PubmedArticle>
      <PubmedArticle><MedlineCitation><PMID>2</PMID><Article><ArticleTitle>No abstract</ArticleTitle></Article></MedlineCitation></PubmedArticle>
    </PubmedArticleSet>"""
    archive = tmp_path / "articles.xml.gz"
    with gzip.open(archive, "wb") as fh:
        fh.write(xml.encode())
    rows = mod.parse_xml(archive)
    assert rows[0]["abstract"] == "First Second"
    assert rows[0]["mesh_terms"] == ["Term"]
    assert mod.parse_xml(tmp_path / "missing.gz") == []

    data = tmp_path / "data"
    domain = data / "topic"
    domain.mkdir(parents=True)
    (domain / "1.json").write_text("old")
    monkeypatch.setattr(mod, "DATA_DIR", data)
    updated, new = mod.update_abstracts(rows + [{"pmid": "9", "abstract": "new"}])
    assert (updated, new) == (1, 1)
    assert json.loads((domain / "1.json").read_text())["pmid"] == "1"


def test_pubmed_main_no_work_and_one_file_workflow(monkeypatch, tmp_path, capsys):
    mod = load("sync_pubmed_updates")
    monkeypatch.setattr(mod, "load_state", lambda: {"total_updated": 0, "total_new": 0, "files_processed": []})
    monkeypatch.setattr(mod, "get_all_update_files", lambda: [])
    mod.main()
    assert "Already up to date" in capsys.readouterr().out
    monkeypatch.setattr(mod, "get_all_update_files", lambda: ["one.xml.gz"])
    downloaded = tmp_path / "one.xml.gz"
    downloaded.write_bytes(b"x")
    monkeypatch.setattr(mod, "download_file", lambda _: downloaded)
    monkeypatch.setattr(mod, "parse_xml", lambda _: [{"pmid": "1"}])
    monkeypatch.setattr(mod, "update_abstracts", lambda rows: (1, 0))
    saved = []
    monkeypatch.setattr(mod, "save_state", saved.append)
    mod.main()
    assert saved[0]["last_file"] == "one.xml.gz"
    assert not downloaded.exists()


def test_reference_registry_helpers_and_download_branches(tmp_path, monkeypatch):
    mod = load("download_references")
    registry_path = tmp_path / "nested" / "registry.json"
    assert mod.load_registry(registry_path) == {}
    registry = {}
    mod.register_file(registry, "genomes", {"name": "x", "version": 1})
    mod.register_file(registry, "genomes", {"name": "x", "version": 2})
    assert registry["genomes"] == [{"name": "x", "version": 2}]
    mod.save_registry(registry_path, registry)
    assert mod.load_registry(registry_path) == registry
    assert mod._human_size(12) == "12.0B"
    assert mod._human_size(1024) == "1.0KB"
    assert mod._human_size(1024**5) == "1.0PB"

    dl = mod.ReferenceDownloader(tmp_path / "refs", dry_run=True)
    target = tmp_path / "refs" / "x.dat"
    assert dl.download_file("https://example.invalid/x", target, "x") is False
    target.parent.mkdir(parents=True)
    target.write_bytes(b"already")
    assert dl.download_file("unused", target) is False
    monkeypatch.setattr(mod.subprocess, "run", Mock(side_effect=mod.subprocess.CalledProcessError(1, "wget")))
    live = mod.ReferenceDownloader(tmp_path / "live")
    assert live.download_file("https://example.invalid/x", tmp_path / "live" / "x") is False
    assert live._downloaded == []
    batch = mod.ReferenceDownloader(tmp_path / "batch", dry_run=True)
    seen = []
    monkeypatch.setattr(batch, "download_file", lambda url, dest, desc="": seen.append((url, dest, desc)))
    batch._batch({"a.dat": "https://example.invalid/a"}, tmp_path / "batch", "Prefix")
    assert seen[0][2] == "Prefix a.dat"
    batch.flush_registry()


@pytest.mark.parametrize("method_name", [
    "download_human_GRCh38", "download_human_GRCh37", "download_human_T2T",
    "download_mouse_GRCm39", "download_mouse_GRCm38", "download_rat_GRCr8",
    "download_zebrafish_GRCz11", "download_drosophila_BDGP6", "download_yeast_R64",
])
def test_reference_genome_methods_register_catalog_entries(tmp_path, monkeypatch, method_name):
    mod = load("download_references")
    dl = mod.ReferenceDownloader(tmp_path)
    batches = []
    monkeypatch.setattr(dl, "_batch", lambda files, base, prefix="": batches.append((files, base, prefix)))
    monkeypatch.setattr(mod, "_now", lambda: "now")
    getattr(dl, method_name)()
    assert batches and batches[0][0]
    assert dl.registry["genomes"][-1]["downloaded_at"] == "now"
    assert dl.registry["genomes"][-1]["files"] == list(batches[0][0])


def test_reference_annotation_variant_database_methods_and_status(tmp_path, monkeypatch, capsys):
    mod = load("download_references")
    dl = mod.ReferenceDownloader(tmp_path)
    calls = []
    monkeypatch.setattr(dl, "_batch", lambda files, base, prefix="": calls.append((files, base, prefix)))
    monkeypatch.setattr(dl, "download_file", lambda url, dest, desc="": calls.append((url, dest, desc)) or True)
    monkeypatch.setattr(mod, "_now", lambda: "now")
    dl.download_annotation_human()
    dl.download_annotation_mouse()
    dl.download_variants_human()
    dl.download_variants_mouse()
    dl.download_databases()
    assert len(dl.registry["variants"]) == 2
    assert len(dl.registry["databases"]) == 1
    assert any("annotation/human" in str(call[1]) for call in calls if isinstance(call, tuple) and len(call) == 3)
    (tmp_path / "organisms/human/GRCh38").mkdir(parents=True)
    (tmp_path / "organisms/human/GRCh38/genome.fa.gz").write_bytes(b"x")
    dl.print_status()
    assert "present" in capsys.readouterr().out
    dl.flush_registry()
    assert (tmp_path / mod.REGISTRY_FILENAME).exists()


def test_reference_cli_parser_and_main_dry_run(monkeypatch, capsys):
    mod = load("download_references")
    monkeypatch.setattr(sys, "argv", ["download_references.py", "--species", "human", "--dry-run"])
    args = mod.parse_args()
    assert args.species == ["human"] and args.dry_run
    monkeypatch.setattr(sys, "argv", ["download_references.py", "--base-dir", "/tmp/refs", "--status"])
    monkeypatch.setattr(mod.ReferenceDownloader, "print_status", lambda self: print("status"))
    mod.main()
    assert "status" in capsys.readouterr().out


def test_reference_main_dry_run_dispatches_optional_sections(monkeypatch, tmp_path):
    mod = load("download_references")
    monkeypatch.setattr(sys, "argv", ["download_references.py", "--base-dir", str(tmp_path), "--assemblies", "GRCh38", "--include-annotation", "--include-variants", "--include-databases", "--dry-run"])
    calls = []
    for name in ["download_human_GRCh38", "download_annotation_human", "download_annotation_mouse", "download_variants_human", "download_variants_mouse", "download_databases"]:
        monkeypatch.setattr(mod.ReferenceDownloader, name, lambda self, name=name: calls.append(name))
    mod.main()
    assert calls == ["download_human_GRCh38", "download_annotation_human", "download_annotation_mouse", "download_variants_human", "download_variants_mouse", "download_databases"]


def test_reference_scaffold_resolve_and_mappings(tmp_path, capsys):
    mod = load("download_references")
    dry = mod.ReferenceDownloader(tmp_path / "dry", dry_run=True)
    dry.scaffold_directories()
    assert not (tmp_path / "dry" / "organisms/human/GRCh38").exists()
    live = mod.ReferenceDownloader(tmp_path / "live")
    live.scaffold_directories()
    assert (tmp_path / "live" / "databases/go").is_dir()
    assert mod.resolve_assemblies(None, ["GRCh38", "R64"]) == ["GRCh38", "R64"]
    assert mod.resolve_assemblies(["human"], None) == ["GRCh38", "GRCh37", "T2T-CHM13"]
    assert mod.resolve_assemblies(["human"], ["GRCh38", "R64"]) == ["GRCh38"]
    assert mod.resolve_assemblies(None, None) == []
    assert "Directory scaffold" in capsys.readouterr().out


def test_prepare_real_data_parsing_and_annotation_helpers(tmp_path, monkeypatch):
    mod = load("prepare_real_data_facs")
    vcf = tmp_path / "small.vcf.gz"
    lines = [
        b"##fileformat=VCFv4.2\n",
        b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        b"1\t10\t.\tA\tG\t.\t.\tCLNSIG=Pathogenic\n",
        b"chr2\t20\t.\tC\tT\t.\t.\tCLNSIG=Benign\n",
        b"3\t30\t.\tAT\tG\t.\t.\tCLNSIG=Pathogenic\n",
        b"4\t40\t.\tA\tG\t.\t.\tCLNSIG=Uncertain_significance\n",
    ]
    with gzip.open(vcf, "wb") as fh:
        fh.writelines(lines)
    buckets = mod.parse_clinvar_variants(vcf, 1, 42)
    assert buckets["Pathogenic"][0]["chrom"] == "1"
    assert buckets["Benign"][0]["chrom"] == "chr2"
    assert mod.to_hgvs("7", "42", "A", "T") == "chr7:g.42A>T"
    assert mod.to_hgvs("chr7", "42", "A", "T") == "chr7:g.42A>T"
    assert mod.extract_score({"a": {"b": [3]}}, "a.b") == 3
    assert mod.extract_score({"a": "bad"}, "a.b") is None
    assert mod.extract_score({"a": {"b": []}}, "a.b") is None
    assert mod.extract_gerp({"dbnsfp": {"gerp++_rs": [1.2]}}) == 1.2
    assert mod.extract_gerp({"dbnsfp": {"gerp++_rs": []}}) is None
    assert mod.extract_gerp({}) is None

    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    response = Mock()
    response.json.return_value = [{"query": "chr1:g.10A>G", "cadd": {"phred": 12}}]
    monkeypatch.setattr(mod.requests, "post", Mock(return_value=response))
    assert mod.annotate_batch(["chr1:g.10A>G"])["chr1:g.10A>G"]["cadd"]["phred"] == 12
    row = mod.annotate_variants({"Pathogenic": [{"chrom": "1", "pos": "10", "ref": "A", "alt": "G"}]}, 1)[0]
    assert row["pathogenicity"] == "Pathogenic"


def test_prepare_parser_discards_malformed_and_samples_repeated_variants(tmp_path):
    mod = load("prepare_real_data_facs")
    vcf = tmp_path / "edge.vcf.gz"
    lines = [
        b"# header\n",
        b"too-short\n",
        b"1\t1\t.\tA\tG\t.\t.\tNO_CLNSIG\n",
        b"1\t2\t.\tAT\tG\t.\t.\tCLNSIG=Pathogenic\n",
        b"1\t3\t.\tA\tG\t.\t.\tCLNSIG=Uncertain_significance\n",
        b"1\t4\t.\tA\tG\t.\t.\tCLNSIG=Pathogenic\n",
        b"1\t5\t.\tA\tG\t.\t.\tCLNSIG=Pathogenic\n",
    ]
    with gzip.open(vcf, "wb") as fh:
        fh.writelines(lines)
    buckets = mod.parse_clinvar_variants(vcf, 1, 7)
    assert len(buckets["Pathogenic"]) == 1


def test_prepare_download_cache_and_retry_failure(tmp_path, monkeypatch):
    mod = load("prepare_real_data_facs")
    cached = tmp_path / "cached.gz"
    cached.write_bytes(b"cache")
    mod.download_clinvar(cached, False)
    assert cached.read_bytes() == b"cache"
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    response = Mock()
    response.raise_for_status.side_effect = RuntimeError("bad response")
    monkeypatch.setattr(mod.requests, "post", Mock(return_value=response))
    assert mod.annotate_batch(["x"], max_retries=2) == {}


def test_prepare_main_writes_csv_and_filters_missing_scores(tmp_path, monkeypatch):
    mod = load("prepare_real_data_facs")
    row = {"chrom": "1", "pos": "1", "ref": "A", "alt": "T", "cadd": 1, "gnomad_af": 2, "gerp": 3, "phylop": 4, "sift": 5, "polyphen": 6, "pathogenicity": "Pathogenic"}
    monkeypatch.setattr(sys, "argv", ["prepare_real_data_facs.py", "--out-dir", str(tmp_path), "--n-per-class", "1", "--drop-missing-scores"])
    monkeypatch.setattr(mod, "download_clinvar", lambda *args: None)
    monkeypatch.setattr(mod, "parse_clinvar_variants", lambda *args: {"Pathogenic": [{}], "Benign": [{}]})
    monkeypatch.setattr(mod, "annotate_variants", lambda *args: [row, {**row, "cadd": None}])
    mod.main()
    output = tmp_path / "real_variant_pathogenicity.csv"
    with output.open() as fh:
        records = list(csv.DictReader(fh))
    assert len(records) == 1 and records[0]["pathogenicity"] == "Pathogenic"


def test_agent_eval_pure_helpers_and_http_branches(monkeypatch, tmp_path):
    mod = load("agent_tool_selection_eval")
    for key, expected in [("slurm", "slurm"), ("http", "http"), ("aws_batch", "aws_batch"), ("gcp_batch", "gcp_batch"), ("azure_batch", "azure_batch"), ("kubernetes", "kubernetes"), ("none", "unknown")]:
        assert mod._infer_backend({key: {}}) == expected
    tool = {"tool_id": "x", "display_name": "X", "description": "desc", "inputs": [{"name": "a", "required": True, "type": "integer"}, {"name": "b", "required": False, "type": "boolean"}]}
    assert mod.tool_to_text(tool) == "x - X - desc"
    assert mod.tool_required_args(tool) == ["a"]
    assert mod.tool_required_args({"inputs_schema": {"required": ["x"]}}) == ["x"]
    assert mod.build_ollama_tool_schema(tool)["function"]["parameters"]["properties"]["a"]["type"] == "integer"
    assert mod.build_ollama_tool_schema({"tool_id": "y", "inputs_schema": {"properties": {"x": {"type": "string"}}, "required": ["x"]}})["function"]["parameters"]["required"] == ["x"]
    assert mod.cosine_sim(np.array([1., 0.]), np.array([0., 1.])) == 0.0
    assert mod.cosine_sim(np.array([1., 0.]), np.array([1., 0.])) == 1.0
    response = Mock()
    response.json.return_value = {"embedding": [1, 2]}
    monkeypatch.setattr(mod.requests, "post", Mock(return_value=response))
    assert mod.ollama_embed("http://ollama", "m", "text").dtype == np.float32
    response.json.return_value = {"message": {"tool_calls": [{"function": {"name": "x", "arguments": '{"a": 1}'}}]}}
    call, _, _ = mod.call_ollama_chat_with_tools("http://ollama", "m", "p", [tool])
    assert call == {"tool_id": "x", "args": {"a": 1}, "malformed": False}
    response.json.return_value = {"message": {"tool_calls": [{"function": {"name": "x", "arguments": "bad"}}]}}
    assert mod.call_ollama_chat_with_tools("http://ollama", "m", "p", [tool])[0]["malformed"] is True
    response.json.return_value = {"message": {}}
    assert mod.call_ollama_chat_with_tools("http://ollama", "m", "p", [tool])[0] is None
    monkeypatch.setattr(mod.requests, "post", Mock(side_effect=RuntimeError("offline")))
    assert mod.call_ollama_chat_with_tools("http://ollama", "m", "p", [tool])[0] is None


def test_agent_corpus_loading_and_setup_api_wrappers(monkeypatch, tmp_path):
    mod = load("agent_tool_selection_eval")
    yaml = pytest.importorskip("yaml")
    yaml_path = tmp_path / "tools.yaml"
    yaml_path.write_text("tools:\n  - tool_id: a\n    tags: [slurm, unverified]\n  - tool_id: b\n    tags: [http]\n")
    tools = mod.load_corpus(str(yaml_path), None, ["http"], None, ["unverified"], True)
    assert [t["tool_id"] for t in tools] == []
    tools = mod.load_corpus(str(yaml_path), None, None, None, ["unverified"], False)
    assert len(tools) == 2
    assert mod.load_corpus(None, None, None, None, [], False) == mod.MOCK_TOOLS

    setup = load("setup_beta_project")
    response = Mock()
    response.json.return_value = {"data": {"viewer": {"id": "id", "login": "me"}}}
    monkeypatch.setattr(setup.requests, "post", Mock(return_value=response))
    assert setup.gql("query", {"x": 1})["viewer"]["id"] == "id"
    assert setup.rest_post("/x", {"a": 1})["data"]["viewer"]["id"] == "id"
    monkeypatch.setattr(setup.requests, "get", Mock(return_value=response))
    assert setup.rest_get("/x")["data"]["viewer"]["login"] == "me"
    assert setup.create_project("owner", dry_run=True) == "DRY_RUN_PROJECT_ID"
    assert setup.add_single_select_field("id", "Priority", ["High"], dry_run=True) == "DRY_Priority_ID"
    assert setup.create_issue("repo", "title", "body", "High", "Testing", [], dry_run=True)["number"] == 0
    assert setup.create_all_issues(dry_run=True)


def test_setup_project_http_errors_rate_limits_and_dry_run_paths(monkeypatch, capsys):
    setup = load("setup_beta_project")
    response = Mock()
    response.json.return_value = {"data": {"viewer": {"id": "id", "login": "me"}}}
    monkeypatch.setattr(setup.requests, "post", Mock(return_value=response))
    assert setup.get_user_node_id() == ("id", "me")
    monkeypatch.setattr(setup, "gql", lambda *args, **kwargs: {"createProjectV2": {"projectV2": {"id": "project", "number": 7, "url": "url"}}})
    assert setup.create_project("owner", False) == ("project", 7)
    assert setup.add_single_select_field("id", "Priority", ["High"], False) is None
    setup.create_project_fields("id", False)
    assert "manual setup" in capsys.readouterr().out

    calls = []
    monkeypatch.setattr(setup, "rest_get", lambda path: calls.append(("get", path)))
    monkeypatch.setattr(setup, "rest_post", lambda path, body: calls.append(("post", path, body)))
    setup.ensure_label("repo", "label", "fff")
    assert calls == [("get", "/repos/man4ish/repo/labels/label")]
    import requests
    calls.clear()
    monkeypatch.setattr(setup, "rest_get", Mock(side_effect=requests.HTTPError(response=SimpleNamespace(status_code=404))))
    setup.ensure_label("repo", "label", "fff")
    assert calls and calls[0][0] == "post"
    monkeypatch.setattr(setup, "time", SimpleNamespace(sleep=lambda _: None))
    attempts = iter([requests.HTTPError(response=SimpleNamespace(status_code=429)), "ok"])
    def limited_call():
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value
    assert setup.rate_limited_call(limited_call) == "ok"
    attempts = iter([requests.HTTPError(response=SimpleNamespace(status_code=500))])
    with pytest.raises(requests.HTTPError): setup.rate_limited_call(limited_call)
    attempts = iter([requests.HTTPError(response=SimpleNamespace(status_code=403))] * 3)
    with pytest.raises(RuntimeError): setup.rate_limited_call(limited_call)
    setup.setup_labels(True)
    setup.link_issues_to_project("id", [{"node_id": "x", "repo": "r", "number": 1}, {"node_id": "DRY_NODE"}], True)


def test_agent_api_corpus_and_eval_loop(monkeypatch, tmp_path):
    mod = load("agent_tool_selection_eval")
    response = Mock()
    response.json.return_value = [{"tool_id": "a", "tags": ["slurm"]}, {"tool_id": "b", "tags": ["unverified"]}]
    monkeypatch.setattr(mod.requests, "get", Mock(return_value=response))
    loaded = mod.load_corpus_from_api("http://tes", ["unverified"])
    assert loaded[0]["_backend"] == "slurm" and loaded[1]["_verified"] is False
    directory = tmp_path / "tools"
    directory.mkdir()
    (directory / "a.yaml").write_text("tools:\n  - tool_id: a\n    tags: [slurm]\n")
    assert len(mod.load_corpus(None, str(directory), None, None, [], False)) == 1

    monkeypatch.setattr(mod, "TEST_CASES", [{"id": "c", "category": "x", "prompt": "p", "expected_tool_id": "a", "required_args": []}])
    monkeypatch.setattr(mod, "ollama_embed", lambda *args: np.array([1., 0.]))
    monkeypatch.setattr(mod, "call_ollama_chat_with_tools", lambda *args: ({"tool_id": "a", "args": {}, "malformed": False}, 0.1, "{}"))
    results = mod.run_eval("http://ollama", "model", "embed", 1, [{"tool_id": "a"}])
    assert results[0].correct_tool
    mod.print_summary(results)


def test_agent_main_writes_optional_output(monkeypatch, tmp_path):
    mod = load("agent_tool_selection_eval")
    output = tmp_path / "results.json"
    monkeypatch.setattr(sys, "argv", ["agent_tool_selection_eval.py", "--out", str(output)])
    monkeypatch.setattr(mod, "load_corpus", lambda *args: [{"tool_id": "a"}])
    result = mod.ItemResult(id="x", category="c", prompt="p", expected_tool_id="a")
    monkeypatch.setattr(mod, "run_eval", lambda *args: [result])
    monkeypatch.setattr(mod, "print_summary", lambda *args: None)
    mod.main()
    assert json.loads(output.read_text())[0]["expected_tool_id"] == "a"


def test_setup_main_dry_run_requires_no_network_or_write(tmp_path, monkeypatch, capsys):
    setup = load("setup_beta_project")
    setup.GITHUB_TOKEN = "test-token"
    monkeypatch.setattr(sys, "argv", ["setup_beta_project.py", "--dry-run", "--output", str(tmp_path / "issues.json")])
    monkeypatch.setattr(setup, "gql", lambda *args, **kwargs: {"viewer": {"login": setup.OWNER, "id": "id"}})
    monkeypatch.setattr(setup, "setup_labels", lambda dry_run=False: None)
    created = [{"repo": "repo", "number": 0, "node_id": "DRY_NODE", "url": "DRY_RUN", "title": "title", "priority": "Low", "category": "Testing"}]
    monkeypatch.setattr(setup, "create_all_issues", lambda dry_run=False: created)
    setup.main()
    assert "DRY RUN complete" in capsys.readouterr().out
    assert not (tmp_path / "issues.json").exists()


def test_setup_live_issue_creation_and_linking(monkeypatch):
    setup = load("setup_beta_project")
    monkeypatch.setattr(setup, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(setup, "rate_limited_call", lambda fn, *args, **kwargs: {"number": 4, "node_id": "node", "html_url": "url"})
    issue = setup.create_issue("repo", "title", "body", "High", "Testing", ["bug"], False)
    assert issue["number"] == 4
    monkeypatch.setattr(setup, "create_issue", lambda *args: {"number": 1, "node_id": "n", "html_url": "u"})
    created = setup.create_all_issues(False)
    assert len(created) == len(setup.ISSUES)
    monkeypatch.setattr(setup, "gql", lambda *args, **kwargs: {"addProjectV2ItemById": {"item": {"id": "item"}}})
    setup.link_issues_to_project("project", [{"repo": "repo", "number": 1, "node_id": "node"}], False)
    monkeypatch.setattr(setup, "gql", Mock(side_effect=RuntimeError("bad")))
    setup.link_issues_to_project("project", [{"repo": "repo", "number": 1, "node_id": "node"}], False)
