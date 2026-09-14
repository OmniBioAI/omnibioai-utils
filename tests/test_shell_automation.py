import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_script(name, *args, input_text=None, cwd=None):
    return subprocess.run(["bash", str(ROOT / name), *map(str, args)], cwd=cwd, input=input_text, text=True, capture_output=True)


def test_check_unpushed_work_reports_clean_dirty_and_json(tmp_path):
    repo = tmp_path / "omnibioai-demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "--allow-empty", "-m", "init", "-q"], check=True)
    (repo / "dirty.txt").write_text("wip")
    output = tmp_path / "status.json"
    result = run_script("check_unpushed_work.sh", "--root", tmp_path, "--json", output, "--quiet")
    assert result.returncode == 0
    report = json.loads(output.read_text())
    assert report["total_repos"] == 1
    assert report["dirty_only"] == 1
    assert report["repos"][0]["untracked"] == 1


def test_check_unpushed_work_rejects_bad_root_and_unknown_option(tmp_path):
    assert run_script("check_unpushed_work.sh", "--root", tmp_path / "missing").returncode == 2
    assert run_script("check_unpushed_work.sh", "--not-an-option").returncode == 2


def test_update_ghcr_refs_dry_run_and_apply_are_isolated(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("ghcr.io/man4ish/tool\n")
    dry = run_script("update_ghcr_refs.sh", tmp_path, cwd=tmp_path)
    assert dry.returncode == 0
    assert "DRY RUN ONLY" in dry.stdout
    assert "ghcr.io/man4ish" in target.read_text()
    apply = run_script("update_ghcr_refs.sh", tmp_path, "--apply", input_text="YES\n", cwd=tmp_path)
    assert apply.returncode == 0
    assert "ghcr.io/omnibioai/tool" in target.read_text()
    assert (tmp_path / "source.txt.bak").exists()
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "source.txt").write_text("ghcr.io/omnibioai/tool\n")
    no_match = run_script("update_ghcr_refs.sh", clean_dir, cwd=clean_dir)
    assert no_match.returncode == 0
    assert "No references" in no_match.stdout


def test_update_ghcr_refs_generated_report_is_reprocessed_on_repeat_run(tmp_path):
    target = tmp_path / "source.txt"
    target.write_text("ghcr.io/man4ish/tool\n")
    first = run_script("update_ghcr_refs.sh", tmp_path, cwd=tmp_path)
    assert first.returncode == 0
    repeated = run_script("update_ghcr_refs.sh", tmp_path, cwd=tmp_path)
    assert repeated.returncode == 0
    assert "ghcr_ref_matches.txt" in repeated.stdout


def test_split_general_corpus_in_temp_copy(tmp_path):
    source = ROOT / "pubmed" / "split_general_corpus.sh"
    script = tmp_path / "split.sh"
    text = source.read_text().replace(
        'SRC="/home/manish/Desktop/machine/omnibioai-data/PubMed/Abstracts/_general_corpus"',
        f'SRC="{tmp_path / "parent" / "_general_corpus"}"',
    ).replace("CHUNK_SIZE=500000", "CHUNK_SIZE=2").replace("/tmp/gc_filelist.txt", f'"{tmp_path / "filelist.txt"}"').replace("/tmp/gc_chunk_", f'"{tmp_path / "gc_chunk_"}"')
    script.write_text(text)
    src_dir = tmp_path / "parent" / "_general_corpus"
    src_dir.mkdir(parents=True)
    for number in range(5):
        (src_dir / f"{number}.json").write_text("{}")
    result = subprocess.run(["bash", str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "parent").glob("_general_corpus_chunk*/*.json"))) == 5
    assert not list(src_dir.glob("*.json"))
    assert "-printf" not in text
    assert '"${path#./}"' in text
