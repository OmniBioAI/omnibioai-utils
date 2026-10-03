"""Transient-worker integration tests with real NumPy/FAISS and a fake Hub.

No model downloads, source downloads from the network, or remote writes occur.
Run from the repository root: python3 -m unittest discover -s tests/pubmed -v

Developer: Manish Kumar <manish@omnibioai.org>
"""
import gzip
import hashlib
import json
import shutil
import types
import unittest
from pathlib import Path
from unittest import mock

import faiss
import numpy as np

import test_reindex_1024_mac_checkpoints as checkpoints

FakeModel = checkpoints.FakeModel
script = checkpoints.script


def records_bytes(records):
    return gzip.compress("\n".join(json.dumps(record) for record in records).encode())


def record(pmid, text):
    return {"pmid": str(pmid), "title": "", "abstract": str(text)}


class FakeHub:
    """In-memory stand-in for the Hugging Face Hub API and file downloads,
    modeling source/destination repos and optional upload/verification
    failure modes without any network access."""

    def __init__(self):
        self.sources = {}
        self.destination = {}
        self.commit = "destination-revision"
        self.failure = None
        self.api = mock.Mock()
        self.api.repo_info.side_effect = lambda repo_id, **kw: types.SimpleNamespace(
            sha="source-revision" if repo_id == script.HF_SOURCE_REPO else self.commit
        )
        self.api.list_repo_tree.side_effect = self.tree
        self.api.get_paths_info.side_effect = self.paths_info
        self.api.upload_folder.side_effect = self.upload
        self.download = mock.Mock(side_effect=self.download_file)

    def tree(self, repo_id, **kwargs):
        assert repo_id == script.HF_SOURCE_REPO
        return [types.SimpleNamespace(path=path, size=len(data))
                for path, data in sorted(self.sources.items())]

    def paths_info(self, repo_id, paths, **kwargs):
        assert repo_id == script.HF_REPO
        result = []
        for path in paths:
            if path not in self.destination:
                continue
            data = self.destination[path]
            result.append(types.SimpleNamespace(
                path=path, size=len(data),
                blob_id=hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest(),
                lfs=(types.SimpleNamespace(sha256=hashlib.sha256(data).hexdigest())
                     if path.endswith("index.faiss") else None),
            ))
        return result

    def download_file(self, repo_id, filename, local_dir, **kwargs):
        data = (self.sources if repo_id == script.HF_SOURCE_REPO else self.destination)[filename]
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def upload(self, repo_id, folder_path, path_in_repo, **kwargs):
        assert repo_id == script.HF_REPO
        assert path_in_repo.startswith(("mxbai-1024/general_corpus/", "mxbai-1024/domains/"))
        if self.failure == "upload":
            raise RuntimeError("simulated upload failure")
        for path in Path(folder_path).iterdir():
            self.destination[f"{path_in_repo}/{path.name}"] = path.read_bytes()
        if self.failure == "missing":
            del self.destination[f"{path_in_repo}/pmid_map.json"]
        if self.failure == "hash":
            path = f"{path_in_repo}/index.faiss"
            self.destination[path] = self.destination[path][:-1] + b"X"
        if self.failure == "metadata":
            path = f"{path_in_repo}/metadata.json"
            metadata = json.loads(self.destination[path])
            metadata["dimension"] = 768
            self.destination[path] = json.dumps(metadata).encode()
        return types.SimpleNamespace(oid=self.commit)

    def seed_complete(self, unit, prefix=None):
        prefix = prefix or unit.remote_path
        vectors = np.eye(5, 1024, dtype=np.float32)
        index = faiss.IndexFlatIP(1024)
        index.add(vectors)
        metadata = {
            "chunk": unit.name, "corpus_type": unit.kind, "source_paths": list(unit.source_files),
            "model": script.MODEL_NAME, "dimension": 1024, "normalized": True,
            "faiss_type": "IndexFlatIP", "vector_count": 5, "pmid_count": 5,
            "usable_abstracts": 5, "status": "PASS", "completed_at": "completed-before-test",
            "self_retrieval": {"queries": 5, "top1": 1.0, "top5": 1.0, "top10": 1.0},
        }
        self.destination[f"{prefix}/index.faiss"] = faiss.serialize_index(index).tobytes()
        self.destination[f"{prefix}/pmid_map.json"] = json.dumps(list(map(str, range(5)))).encode()
        self.destination[f"{prefix}/metadata.json"] = json.dumps(metadata).encode()


class WorkerTests(unittest.TestCase):
    """Verify process_unit's end-to-end behavior: skip-on-remote-complete,
    download/embed/upload, retention of local work on any failure, cleanup
    only after a verified upload, and unit discovery/CLI dispatch."""

    def setUp(self):
        checkpoints.CheckpointTests.setUp(self)
        self.hub = FakeHub()
        self.manifest = {"shards": {}}
        self.unit = script.IndexingUnit(
            "general_corpus", script.chunk_name(0),
            ("general_corpus/_general_corpus_chunk000.jsonl.gz",), "source-revision",
        )
        self.hub.sources[self.unit.source_files[0]] = records_bytes([record(i, i) for i in range(5)])
        for name, value in {
            "faiss": faiss, "hf_hub_download": self.hub.download,
            "free_disk_gib": mock.Mock(return_value=200),
        }.items():
            patcher = mock.patch.object(script, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_unit(self, model=None, unit=None):
        script.process_unit(model or FakeModel(), self.hub.api, unit or self.unit, self.manifest)

    def checkpoints(self, unit=None):
        return script.OUTPUT_ROOT / "_embedding_checkpoints" / (unit or self.unit).key

    def downloaded(self, unit=None):
        return Path(self.manifest["shards"][(unit or self.unit).key]["source_download"]["work_dir"])

    def source_calls(self):
        return [c for c in self.hub.download.call_args_list if c.kwargs["repo_id"] == script.HF_SOURCE_REPO]

    def assert_retained(self, unit=None):
        unit = unit or self.unit
        self.assertTrue(self.downloaded(unit).is_dir())
        self.assertEqual(len(list(self.checkpoints(unit).glob("*.npz"))), 3)
        self.assertTrue(unit.output_dir.is_dir())

    def test_remote_complete_skip_without_local_manifest_or_source(self):
        """Skip embedding and downloading entirely when the remote already
        holds a verified-complete unit, marking it UPLOADED locally."""
        self.hub.seed_complete(self.unit)
        model = FakeModel()
        self.run_unit(model)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.source_calls(), [])
        self.hub.api.upload_folder.assert_not_called()
        saved = json.loads(script.MANIFEST_FILE.read_text())["shards"][self.unit.key]
        self.assertEqual(saved["status"], "UPLOADED")
        self.assertEqual(saved["hf_path"], self.unit.remote_path)
        self.assertFalse(self.unit.output_dir.exists())
        messages = [c.args[0] for c in script.log.call_args_list]
        self.assertTrue(any("disk before unit" in m for m in messages))
        self.assertTrue(any("disk after unit" in m for m in messages))

    def test_source_download_and_cleanup_only_after_persisted_uploaded(self):
        """Clean up checkpoints, downloaded source, and the output directory
        only after the manifest has already persisted the UPLOADED status."""
        removed = []
        real_remove = shutil.rmtree
        def observe_remove(path, *args, **kwargs):
            path = Path(path)
            if path.resolve().is_relative_to(script.OUTPUT_ROOT.resolve()):
                saved = json.loads(script.MANIFEST_FILE.read_text())["shards"][self.unit.key]
                self.assertEqual(saved["status"], "UPLOADED")
                self.assertTrue(saved["local_artifacts_verified"])
                self.assertEqual(saved["hf_revision"], self.hub.commit)
                removed.append(path.resolve())
            return real_remove(path, *args, **kwargs)
        with mock.patch.object(script.shutil, "rmtree", side_effect=observe_remove):
            self.run_unit()
        downloaded = self.downloaded()
        self.assertEqual(removed, [p.resolve() for p in
                                  (self.checkpoints(), downloaded, self.unit.output_dir)])
        self.assertEqual([c.kwargs["filename"] for c in self.source_calls()], list(self.unit.source_files))
        self.assertFalse(downloaded.exists())
        self.assertFalse(self.unit.output_dir.exists())
        self.assertFalse(self.checkpoints().exists())
        self.assertTrue(script.MANIFEST_FILE.exists())
        self.assertEqual(self.manifest["shards"][self.unit.key]["self_retrieval"]["top1"], 1.0)

    def test_upload_failure_retains_everything_and_resume_reuses_checkpoints(self):
        """Keep downloaded source and embedding checkpoints on an upload
        failure, and fully reuse them (no re-download, no re-encode) on
        retry."""
        self.hub.failure = "upload"
        with self.assertRaisesRegex(RuntimeError, "upload failure"):
            self.run_unit()
        self.assert_retained()
        original = {p.name: p.read_bytes() for p in self.checkpoints().glob("*.npz")}
        self.hub.failure = None
        resumed = FakeModel()
        self.run_unit(resumed)
        self.assertEqual(resumed.calls, [])
        self.assertEqual(len(self.source_calls()), 1)
        self.assertEqual(len(original), 3)
        self.assertFalse(self.downloaded().exists())

    def test_local_compute_complete_resume_uploads_without_recomputation(self):
        """After a verified local PASS (embeddings assembled, FAISS built,
        self-test passed) but a failed upload -- the chunk054 incident --
        resuming must skip source download, source parsing, embedding, and
        self-retrieval entirely, reusing the exact local artifacts and only
        retrying publication, which must still pass remote verification."""
        self.hub.failure = "upload"
        with self.assertRaisesRegex(RuntimeError, "upload failure"):
            self.run_unit()
        self.assert_retained()
        saved = json.loads(script.MANIFEST_FILE.read_text())["shards"][self.unit.key]
        self.assertEqual(saved["status"], "PASS")
        before = {name: (self.unit.output_dir / name).read_bytes() for name in script.ARTIFACT_NAMES}
        self.hub.failure = None
        with mock.patch.object(script, "obtain_source",
                                side_effect=AssertionError("source re-fetched")), \
             mock.patch.object(script, "load_unit_abstracts",
                                side_effect=AssertionError("source re-parsed")), \
             mock.patch.object(script, "embed_with_checkpoints",
                                side_effect=AssertionError("re-embedded")), \
             mock.patch.object(script, "self_retrieval_test",
                                side_effect=AssertionError("self-test re-run")):
            resumed = FakeModel()
            self.run_unit(resumed)
        self.assertEqual(resumed.calls, [])
        after = {name: self.hub.destination[f"{self.unit.remote_path}/{name}"]
                 for name in script.ARTIFACT_NAMES}
        self.assertEqual(after, before)
        saved = json.loads(script.MANIFEST_FILE.read_text())["shards"][self.unit.key]
        self.assertEqual(saved["status"], "UPLOADED")
        self.assertTrue(saved["local_artifacts_verified"])
        self.assertEqual(set(saved["remote_artifacts"]), set(script.ARTIFACT_NAMES))
        self.assertFalse(self.unit.output_dir.exists())
        self.assertFalse(self.downloaded().exists())

    def test_local_compute_complete_resume_requires_remote_verification_to_pass(self):
        """The LOCAL_COMPUTE_COMPLETE resume shortcut must still perform full
        remote verification after upload, and must not mark the unit
        UPLOADED or delete local work when that verification fails."""
        self.hub.failure = "upload"
        with self.assertRaisesRegex(RuntimeError, "upload failure"):
            self.run_unit()
        self.hub.failure = "hash"
        with self.assertRaisesRegex(RuntimeError, "HF verification failed"):
            self.run_unit()
        self.assert_retained()
        saved = json.loads(script.MANIFEST_FILE.read_text())["shards"][self.unit.key]
        self.assertEqual(saved["status"], "PASS")
        self.assertNotEqual(saved.get("status"), "UPLOADED")

    def test_local_artifacts_complete_rejects_corrupt_or_incompatible_state(self):
        """local_artifacts_complete must return None -- never granting the
        upload-only shortcut -- for a truncated FAISS file, a missing PMID
        map, or metadata that disagrees on dimension/status/normalization/
        model/vector_count, even though the untouched PASS directory
        validates cleanly before and after each corruption."""
        self.hub.failure = "upload"
        with self.assertRaisesRegex(RuntimeError, "upload failure"):
            self.run_unit()
        output_dir = self.unit.output_dir
        self.assertIsNotNone(script.local_artifacts_complete(self.unit))

        original_index = (output_dir / "index.faiss").read_bytes()
        original_metadata = json.loads((output_dir / "metadata.json").read_text())
        original_pmids = (output_dir / "pmid_map.json").read_bytes()

        def restore():
            (output_dir / "index.faiss").write_bytes(original_index)
            (output_dir / "metadata.json").write_text(json.dumps(original_metadata))
            (output_dir / "pmid_map.json").write_bytes(original_pmids)

        with self.subTest("missing pmid_map"):
            (output_dir / "pmid_map.json").unlink()
            self.assertIsNone(script.local_artifacts_complete(self.unit))
            restore()

        with self.subTest("truncated index"):
            (output_dir / "index.faiss").write_bytes(original_index[:20])
            self.assertIsNone(script.local_artifacts_complete(self.unit))
            restore()

        for key, value in {
            "dimension": 768, "status": "FAILED", "vector_count": 999,
            "normalized": False, "model": "different-model",
        }.items():
            with self.subTest(key=key):
                metadata = dict(original_metadata)
                metadata[key] = value
                (output_dir / "metadata.json").write_text(json.dumps(metadata))
                self.assertIsNone(script.local_artifacts_complete(self.unit))
                restore()

        with self.subTest("pmid count mismatch"):
            (output_dir / "pmid_map.json").write_text(json.dumps(["x"]))
            self.assertIsNone(script.local_artifacts_complete(self.unit))
            restore()

        self.assertIsNotNone(script.local_artifacts_complete(self.unit))

    def test_workflow_has_no_dependency_on_hf_xet(self):
        """This reindex workflow uploads exclusively through
        api.upload_folder and never imports or references Xet/hf_xet, so
        publication cannot fail on a missing or incompatible hf_xet
        installation; confirms hf_xet is genuinely absent in this
        environment, matching the incident's restored environment."""
        source = Path(script.__file__).read_text() if getattr(script, "__file__", None) else None
        if source is None:
            source = (Path(__file__).resolve().parents[2] / "pubmed" / "reindex_1024_mac.py").read_text()
        self.assertNotIn("xet", source.lower())
        import importlib.util as importlib_util
        self.assertIsNone(importlib_util.find_spec("hf_xet"))

    def test_verification_failures_never_delete_local_work(self):
        """Retain local work and keep the manifest at PASS (not UPLOADED)
        when post-upload HF verification finds a missing file, hash
        mismatch, or metadata mismatch."""
        for failure in ("missing", "hash", "metadata"):
            with self.subTest(failure=failure):
                self.hub.destination.clear()
                self.hub.failure = failure
                with self.assertRaisesRegex(RuntimeError, "HF verification failed"):
                    self.run_unit()
                self.assert_retained()
                self.assertEqual(self.manifest["shards"][self.unit.key]["status"], "PASS")

    def test_manifest_failure_after_verified_upload_retains_then_recovers(self):
        """Retain local work when persisting the UPLOADED manifest entry
        fails, then recognize the already-verified upload and clean up on
        the next run without re-uploading or re-encoding."""
        real_save = script.save_manifest
        def fail_uploaded(manifest):
            if manifest["shards"][self.unit.key]["status"] == "UPLOADED":
                raise OSError("manifest persistence failed")
            real_save(manifest)
        with mock.patch.object(script, "save_manifest", side_effect=fail_uploaded):
            with self.assertRaisesRegex(OSError, "manifest persistence"):
                self.run_unit()
        self.assert_retained()
        self.manifest = json.loads(script.MANIFEST_FILE.read_text())
        model = FakeModel()
        self.run_unit(model)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.hub.api.upload_folder.call_count, 1)
        self.assertFalse(self.downloaded().exists())
        self.assertFalse(self.checkpoints().exists())

    def test_real_manifest_fsync_failure_does_not_allow_cleanup(self):
        """Leave the manifest at PASS and retain local work when the
        os.fsync call inside a real save_manifest fails while persisting
        the UPLOADED status."""
        real_save = script.save_manifest
        def fail_sync(manifest):
            if manifest["shards"][self.unit.key]["status"] == "UPLOADED":
                with mock.patch.object(script.os, "fsync", side_effect=OSError("fsync failed")):
                    real_save(manifest)
            else:
                real_save(manifest)
        with mock.patch.object(script, "save_manifest", side_effect=fail_sync):
            with self.assertRaisesRegex(OSError, "fsync failed"):
                self.run_unit()
        self.assert_retained()
        saved = json.loads(script.MANIFEST_FILE.read_text())
        self.assertEqual(saved["shards"][self.unit.key]["status"], "PASS")

    def test_preexisting_local_source_never_deleted(self):
        """Use a source directory that already exists on disk without
        downloading it, and never delete those pre-existing files."""
        source = script.SOURCE_ROOT / self.unit.name
        source.mkdir(parents=True)
        for i in range(5):
            (source / f"{i}.json").write_text(json.dumps(record(i, i)))
        before = {p.name: p.read_bytes() for p in source.iterdir()}
        self.run_unit()
        self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, before)
        self.assertEqual(self.source_calls(), [])
        self.assertNotIn("source_download", self.manifest["shards"][self.unit.key])
        self.assertFalse(self.unit.output_dir.exists())

    def test_validation_failure_and_keyboard_interrupt_retain_source(self):
        """Retain downloaded source and checkpoints when self-retrieval
        validation fails or a keyboard interrupt lands mid-embedding, and
        resume the interrupted unit correctly afterward."""
        with mock.patch.object(script, "self_retrieval_test", return_value={
            "queries": 5, "top1": 0.8, "top5": 1.0, "top10": 1.0,
        }):
            with self.assertRaisesRegex(RuntimeError, "Top-1"):
                self.run_unit()
        self.assert_retained()
        self.hub.api.upload_folder.assert_not_called()
        # Interrupt a different unit inside its second embedding block.
        unit = script.IndexingUnit("domains", "Interrupted", ("Interrupted.jsonl.gz",), "source-revision")
        self.hub.sources[unit.source_files[0]] = records_bytes([record(i, i) for i in range(5)])
        with self.assertRaises(KeyboardInterrupt):
            self.run_unit(FakeModel(failure_call=2, failure_type=KeyboardInterrupt), unit)
        self.assertTrue(self.downloaded(unit).exists())
        self.assertEqual(len(list(self.checkpoints(unit).glob("*.npz"))), 1)
        self.assertTrue(unit.output_dir.exists())
        resumed = FakeModel()
        self.run_unit(resumed, unit)
        self.assertEqual(resumed.calls, [["2", "3"], ["4"]])
        self.assertFalse(self.downloaded(unit).exists())

    def test_legacy_destinations_and_other_namespace_never_cause_skip(self):
        """Ignore legacy-prefixed or wrong-corpus-type remote destinations
        when checking for a completed unit, requiring an exact match under
        the unit's own mxbai-1024 namespace."""
        for unit in (self.unit, script.IndexingUnit("domains", "Cardiovascular", ("Cardiovascular.jsonl.gz",))):
            with self.subTest(unit=unit.key):
                self.hub.seed_complete(unit, prefix=f"legacy-768/{unit.name}")
                self.hub.seed_complete(unit, prefix=f"{unit.name}")
                other = "domains" if unit.kind == "general_corpus" else "general_corpus"
                self.hub.seed_complete(unit, prefix=f"mxbai-1024/{other}/{unit.name}")
                before = dict(self.hub.destination)
                self.assertIsNone(script.remote_completed_unit(self.hub.api, unit))
                self.assertEqual(self.hub.destination, before)
                requested = self.hub.api.get_paths_info.call_args.kwargs["paths"]
                self.assertTrue(all(p.startswith(unit.remote_path + "/") for p in requested))

    def test_domain_combines_sources_deduplicates_pmids_and_preserves_legacy(self):
        """Combine a multi-file domain's source records, drop invalid or
        duplicate PMIDs deterministically, and leave the unrelated legacy
        destination prefix untouched."""
        unit = script.IndexingUnit("domains", "Cardiovascular", (
            "domains/Cardiovascular/part000.jsonl.gz", "domains/Cardiovascular/part001.jsonl.gz",
        ), "source-revision")
        self.hub.sources[unit.source_files[0]] = records_bytes([
            record(2, 2), record(0, 0), record(1, 1), {"abstract": "missing PMID"},
        ])
        self.hub.sources[unit.source_files[1]] = records_bytes([
            record(1, 9), record(3, 3), record(4, 4), record(8, ""), None,
        ])
        self.hub.seed_complete(unit, prefix="Cardiovascular")
        legacy = {k: v for k, v in self.hub.destination.items() if k.startswith("Cardiovascular/")}
        model = FakeModel()
        self.run_unit(model, unit)
        self.assertEqual(model.calls, [["0", "1"], ["2", "3"], ["4"]])
        pmids = json.loads(self.hub.destination[f"{unit.remote_path}/pmid_map.json"])
        self.assertEqual(pmids, ["0", "1", "2", "3", "4"])
        metadata = self.manifest["shards"][unit.key]
        self.assertEqual(metadata["source_documents"], 9)
        self.assertEqual(metadata["unique_pmid_count"], 5)
        self.assertEqual(metadata["skipped_invalid_records"], 3)
        self.assertEqual(metadata["duplicate_pmids"], 1)
        self.assertEqual(metadata["vector_count"], 5)
        self.assertEqual(metadata["corpus_type"], "domains")
        self.assertEqual({k: self.hub.destination[k] for k in legacy}, legacy)
        self.assertFalse(self.downloaded(unit).exists())
        self.assertFalse(unit.output_dir.exists())
        self.assertIsNotNone(script.remote_completed_unit(self.hub.api, unit))

    def test_preexisting_nested_domain_source_is_deduplicated_and_retained(self):
        """Deduplicate a PMID that appears in two nested source
        subdirectories of a pre-existing domain source without deleting
        either file."""
        unit = script.IndexingUnit("domains", "Cardiovascular", ("Cardiovascular.jsonl.gz",))
        source = script.SOURCE_ROOT.parent / unit.name
        (source / "part1").mkdir(parents=True)
        (source / "part2").mkdir()
        for i in range(5):
            (source / "part1" / f"{i}.json").write_text(json.dumps(record(i, i)))
        duplicate = source / "part2" / "other-name.json"
        duplicate.write_text(json.dumps(record(0, 9)))
        self.run_unit(unit=unit)
        self.assertTrue(duplicate.exists())
        self.assertEqual(self.source_calls(), [])
        self.assertEqual(self.manifest["shards"][unit.key]["duplicate_pmids"], 1)

    def test_preexisting_split_domain_archives_never_downloaded_or_deleted(self):
        """Use pre-existing multi-file domain source archives found directly
        under the source root without downloading or deleting them."""
        unit = script.IndexingUnit("domains", "Split", ("Split_chunk000.jsonl.gz", "Split_chunk001.jsonl.gz"))
        root = script.SOURCE_ROOT.parent
        root.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(unit.source_files):
            (root / name).write_bytes(records_bytes([record(i, i)]))
        self.run_unit(unit=unit)
        self.assertEqual(self.source_calls(), [])
        self.assertTrue(all((root / name).is_file() for name in unit.source_files))

    def test_domain_remote_complete_skip_uses_only_domain_namespace(self):
        """Skip a completed domain unit using only its own namespace, and
        no longer treat it as complete once its source-file set changes."""
        unit = script.IndexingUnit("domains", "Cardiovascular", ("Cardiovascular.jsonl.gz",))
        self.hub.seed_complete(unit)
        model = FakeModel()
        self.run_unit(model, unit)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.source_calls(), [])
        self.hub.api.upload_folder.assert_not_called()
        self.assertEqual(self.manifest["shards"][unit.key]["hf_path"], unit.remote_path)
        changed = script.IndexingUnit("domains", unit.name, unit.source_files + ("Cardiovascular_part002.jsonl.gz",))
        self.assertIsNone(script.remote_completed_unit(self.hub.api, changed))

    def test_local_uploaded_manifest_does_not_skip_missing_1024_destination(self):
        """Re-embed and re-upload when the local manifest claims UPLOADED
        but the 1024-D destination is missing on the remote (only a legacy
        768-D one exists)."""
        self.manifest["shards"][self.unit.key] = {"status": "UPLOADED"}
        self.hub.seed_complete(self.unit, prefix=f"legacy-768/{self.unit.name}")
        model = FakeModel()
        self.run_unit(model)
        self.assertEqual(len(model.calls), 3)
        self.hub.api.upload_folder.assert_called_once()

    def test_interrupted_multi_file_download_resumes_before_embedding(self):
        """Leave a multi-file download unmarked ready and never start
        embedding when interrupted partway through, then resume and
        complete the download on the next run."""
        unit = script.IndexingUnit("domains", "Split", ("Split_part001.jsonl.gz", "Split_part002.jsonl.gz"))
        for i, name in enumerate(unit.source_files):
            self.hub.sources[name] = records_bytes([record(i, i)])
        original = self.hub.download_file
        def interrupt(repo_id, filename, **kwargs):
            if repo_id == script.HF_SOURCE_REPO and filename == unit.source_files[1]:
                raise KeyboardInterrupt()
            return original(repo_id, filename, **kwargs)
        self.hub.download.side_effect = interrupt
        model = FakeModel()
        with self.assertRaises(KeyboardInterrupt):
            self.run_unit(model, unit)
        self.assertEqual(model.calls, [])
        self.assertFalse((self.downloaded(unit) / "ready.json").exists())
        self.hub.download.side_effect = original
        self.run_unit(model, unit)
        self.assertEqual(model.calls, [["0", "1"]])

    def test_changed_domain_source_set_does_not_silently_reuse(self):
        """Refuse to silently reuse a domain's checkpoint once its
        source-file set changes, instead raising IncompatibleCheckpoint
        while retaining the prior local work."""
        unit = script.IndexingUnit("domains", "Split", ("Split_part001.jsonl.gz",))
        self.hub.sources[unit.source_files[0]] = records_bytes([record(i, i) for i in range(5)])
        self.hub.failure = "upload"
        with self.assertRaises(RuntimeError):
            self.run_unit(unit=unit)
        changed = script.IndexingUnit("domains", "Split", unit.source_files + ("Split_part002.jsonl.gz",))
        with self.assertRaises(script.IncompatibleCheckpoint):
            self.run_unit(unit=changed)
        self.assert_retained(unit)

    def test_discovery_zero_beyond_105_flat_and_split_domains(self):
        """Discover general-corpus chunks (including chunk 0 and beyond
        105) and both flat and multi-file domain units, and filter them
        correctly by mode, start/end, and explicit domain name."""
        self.hub.sources = dict.fromkeys([
            "general_corpus/_general_corpus_chunk000.jsonl.gz",
            "general_corpus/_general_corpus_chunk004.jsonl.gz",
            "general_corpus/_general_corpus_chunk105.jsonl.gz",
            "general_corpus/_general_corpus_chunk120.jsonl.gz",
            "general_corpus/_general_corpus_chunk0000.jsonl.gz",
            "general_corpus/not-a-shard.jsonl.gz", "README.md", ".cache/other.json",
            "Cardiovascular.jsonl.gz", "Split_chunk001.jsonl.gz", "Split_chunk002.jsonl.gz",
            "Nested/part1.jsonl.gz", "Nested/part2.jsonl.gz",
            "domains/Explicit/part001.jsonl.gz", "domains/Explicit/part002.jsonl.gz",
            "domains/Flat.jsonl.gz",
        ], b"source")
        units = script.discover_units(self.hub.api)
        self.assertEqual([u.name for u in units if u.kind == "general_corpus"],
                         [script.chunk_name(i) for i in (0, 4, 105, 120)])
        domains = {u.name: u.source_files for u in units if u.kind == "domains"}
        self.assertEqual(set(domains), {"Cardiovascular", "Split", "Nested", "Explicit", "Flat"})
        self.assertEqual(len(domains["Split"]), 2)
        self.assertEqual(len(domains["Explicit"]), 2)
        selected = script.discover_units(self.hub.api, "general", start=105)
        self.assertEqual([u.name for u in selected], [script.chunk_name(i) for i in (105, 120)])
        selected = script.discover_units(self.hub.api, "general", end=0)
        self.assertEqual([u.name for u in selected], [script.chunk_name(0)])
        selected = script.discover_units(self.hub.api, "domains", domain="Split")
        self.assertEqual([u.name for u in selected], ["Split"])

    def test_bad_remote_metadata_and_network_errors_are_not_completion(self):
        """Refuse to treat a remote unit as complete when its metadata
        disagrees on dimension, normalization, vector count, or status, and
        propagate network errors instead of masking them as incomplete."""
        self.hub.seed_complete(self.unit)
        path = f"{self.unit.remote_path}/metadata.json"
        original = self.hub.destination[path]
        for change in ({"dimension": 768}, {"normalized": False}, {"vector_count": 6}, {"status": "FAILED"}):
            metadata = json.loads(original)
            metadata.update(change)
            self.hub.destination[path] = json.dumps(metadata).encode()
            self.assertIsNone(script.remote_completed_unit(self.hub.api, self.unit))
        self.hub.api.get_paths_info.side_effect = ConnectionError("offline")
        with self.assertRaises(ConnectionError):
            self.run_unit()
        self.assertEqual(self.source_calls(), [])

    def test_remote_skip_manifest_failure_never_cleans_leftovers(self):
        """Retain leftover local work when persisting a remote-skip manifest
        update fails, even though the remote destination is already
        complete."""
        self.hub.failure = "upload"
        with self.assertRaises(RuntimeError):
            self.run_unit()
        self.hub.failure = None
        self.hub.upload(script.HF_REPO, str(self.unit.output_dir), self.unit.remote_path)
        with mock.patch.object(script, "save_manifest", side_effect=OSError("manifest failed")):
            with self.assertRaises(OSError):
                self.run_unit()
        self.assert_retained()

    def test_repeated_remote_skip_never_deletes_different_local_work(self):
        """Never delete retained local work across repeated remote-complete
        skips, since local_artifacts_verified was never set by this run."""
        self.hub.failure = "upload"
        with self.assertRaises(RuntimeError):
            self.run_unit()
        self.hub.seed_complete(self.unit)
        for _ in range(2):
            self.run_unit()
            self.assert_retained()
            self.assertFalse(self.manifest["shards"][self.unit.key]["local_artifacts_verified"])

    def test_cli_modes_default_all_and_single_domain(self):
        """Dispatch main() to the correct discovered units for the default
        all-units mode, a single --domain, and a --start/--end range."""
        with mock.patch.object(script, "HfApi", return_value=self.hub.api), \
             mock.patch.object(script, "supervise_units", return_value=0) as supervise:
            self.hub.sources["Cardiovascular.jsonl.gz"] = b"source"
            for args, expected in (([], [self.unit.key, "domains/Cardiovascular"]),
                                   (["--domain", "Cardiovascular"], ["domains/Cardiovascular"]),
                                   (["--start", "0", "--end", "0"], [self.unit.key]),
                                   (["--mode", "general", "--start", "0", "--end", "0"], [self.unit.key])):
                with self.subTest(args=args), mock.patch("sys.argv", ["worker"] + args):
                    supervise.reset_mock()
                    script.main()
                    self.assertEqual([unit.key for unit in supervise.call_args.args[0]], expected)


if __name__ == "__main__":
    unittest.main()
