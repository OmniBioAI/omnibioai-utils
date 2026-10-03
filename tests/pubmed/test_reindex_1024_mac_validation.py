"""Offline validation and bookkeeping tests for the relocated worker: indexing
unit names, CLI argument errors, manifest supervisor records, malformed source
records, embedding sanity checks, and cleanup failure handling.

Run from the repository root: python3 -m unittest discover -s tests/pubmed -v

Developer: Manish Kumar <manish@omnibioai.org>
"""
import gzip
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import test_reindex_1024_mac_checkpoints as checkpoints

script = checkpoints.script


class IsolatedRootTest(unittest.TestCase):
    """Point the module's output root and manifest at a temporary directory."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (
            ("OUTPUT_ROOT", self.root / "output"),
            ("MANIFEST_FILE", self.root / "manifest.json"),
            ("LOG_FILE", self.root / "worker.log"),
        ):
            patcher = mock.patch.object(script, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class IndexingUnitValidationTests(unittest.TestCase):
    """Reject indexing units whose namespace, name, or remote source paths
    could address anything outside the intended dataset layout."""

    def test_rejects_unknown_namespace(self):
        with self.assertRaisesRegex(ValueError, "Invalid indexing namespace"):
            script.IndexingUnit("other", "Cardiovascular")

    def test_rejects_unsafe_unit_name(self):
        with self.assertRaisesRegex(ValueError, "Invalid indexing unit name"):
            script.IndexingUnit("domains", "../escape")

    def test_rejects_non_shard_general_corpus_name(self):
        with self.assertRaisesRegex(ValueError, "Invalid general corpus shard name"):
            script.IndexingUnit("general_corpus", "chunk001")

    def test_rejects_absolute_and_parent_source_paths(self):
        for path in ("/etc/passwd", "general_corpus/../secret.jsonl.gz"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "Invalid remote source path"):
                    script.IndexingUnit("domains", "Cardiovascular", (path,))


class SelectedModeTests(unittest.TestCase):
    """Turn conflicting or out-of-range CLI selections into argparse errors,
    and map internal worker units back to their mode and range."""

    def parse(self, *argv):
        parser = script.argument_parser()
        return parser, parser.parse_args(list(argv))

    def assert_parser_error(self, *argv):
        parser, args = self.parse(*argv)
        with mock.patch.object(parser, "error", side_effect=SystemExit(2)) as error:
            with self.assertRaises(SystemExit):
                script.selected_mode(args, parser)
        return error.call_args.args[0]

    def test_domain_worker_unit_selects_that_domain(self):
        parser, args = self.parse("--worker-unit", "domains/Cardiovascular")
        self.assertEqual(script.selected_mode(args, parser), ("domains", None, None, "Cardiovascular"))

    def test_empty_domain_worker_unit_is_rejected(self):
        self.assertIn("invalid internal worker unit", self.assert_parser_error("--worker-unit", "domains/"))

    def test_malformed_general_worker_unit_is_rejected(self):
        self.assertIn("invalid internal worker unit", self.assert_parser_error("--worker-unit", "chunk7"))

    def test_conflicting_and_out_of_range_selections_are_rejected(self):
        cases = {
            ("--domain", "Cardiovascular", "--mode", "general"): "--domain cannot be used",
            ("--mode", "domains", "--start", "1"): "apply only to general corpus",
            ("--start", "-1"): "--start must be >= 0",
            ("--end", "-1"): "--end must be >= 0",
            ("--start", "5", "--end", "4"): "--end must be >= --start",
        }
        for argv, message in cases.items():
            with self.subTest(argv=argv):
                self.assertIn(message, self.assert_parser_error(*argv))


class SupervisorRecordTests(IsolatedRootTest):
    """Persist supervisor outcomes in the manifest and recognise a completed
    worker only when its upload record matches the unit exactly."""

    def setUp(self):
        super().setUp()
        self.unit = script.IndexingUnit("domains", "Cardiovascular")

    def test_record_supervisor_result_with_and_without_exit_code(self):
        script.record_supervisor_result(self.unit, "RUNNING")
        state = script.load_manifest()["shards"][self.unit.key]
        self.assertEqual(state["supervisor_status"], "RUNNING")
        self.assertNotIn("supervisor_exit_code", state)

        script.record_supervisor_result(self.unit, "FAILED", 3)
        state = script.load_manifest()["shards"][self.unit.key]
        self.assertEqual((state["supervisor_status"], state["supervisor_exit_code"]), ("FAILED", 3))

    def test_worker_completion_requires_matching_upload_record(self):
        self.assertFalse(script.worker_completion_recorded(self.unit))
        manifest = script.load_manifest()
        manifest["shards"][self.unit.key] = {
            "status": "UPLOADED",
            "hf_path": self.unit.remote_path,
            "remote_artifacts": {name: {} for name in script.ARTIFACT_NAMES},
        }
        script.save_manifest(manifest)
        self.assertTrue(script.worker_completion_recorded(self.unit))

        manifest["shards"][self.unit.key]["hf_path"] = "elsewhere"
        script.save_manifest(manifest)
        self.assertFalse(script.worker_completion_recorded(self.unit))


class SourceRecordTests(unittest.TestCase):
    """Yield a placeholder for undecodable JSON instead of aborting a shard,
    and expand JSON arrays into individual records."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_jsonl_skips_undecodable_lines(self):
        path = self.root / "part.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write('{"pmid": "1"}\nnot json\n')
        self.assertEqual(list(script.source_records(path)), [({"pmid": "1"}, None), (None, None)])

    def test_malformed_json_file_yields_single_placeholder(self):
        path = self.root / "123.json"
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(list(script.source_records(path)), [(None, None)])

    def test_json_array_yields_each_record(self):
        path = self.root / "batch.json"
        path.write_text(json.dumps([{"pmid": "1"}, {"pmid": "2"}]), encoding="utf-8")
        self.assertEqual(list(script.source_records(path)), [({"pmid": "1"}, None), ({"pmid": "2"}, None)])


class VectorValidationTests(unittest.TestCase):
    """Reject embeddings with the wrong shape or any non-finite value before
    they reach the FAISS index."""

    def test_rejects_wrong_dimension(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid embedding shape"):
            script.validate_vectors(np.ones((2, script.DIMENSION - 1), dtype=np.float32))

    def test_rejects_non_finite_values(self):
        embeddings = np.ones((2, script.DIMENSION), dtype=np.float32)
        embeddings[1, 0] = np.nan
        with self.assertRaisesRegex(RuntimeError, "NaN or Inf"):
            script.validate_vectors(embeddings)


class WorkerHousekeepingTests(IsolatedRootTest):
    """Keep going when worker cleanup cannot remove a path, report free disk
    space in GiB, and route single-shard calls through process_unit."""

    def test_cleanup_logs_and_continues_after_os_error(self):
        unit = script.IndexingUnit("domains", "Cardiovascular")
        unit.output_dir.mkdir(parents=True)
        with mock.patch.object(script.shutil, "rmtree", side_effect=OSError("busy")), \
                mock.patch.object(script, "log") as log:
            script.cleanup_worker_files(unit)
        self.assertTrue(any("WARNING cleanup failed" in call.args[0] for call in log.call_args_list))

    def test_free_disk_gib_converts_bytes(self):
        usage = types.SimpleNamespace(free=3 * 1024**3)
        with mock.patch.object(script.shutil, "disk_usage", return_value=usage):
            self.assertEqual(script.free_disk_gib(self.root), 3.0)

    def test_process_chunk_builds_general_corpus_unit(self):
        with mock.patch.object(script, "process_unit", return_value="done") as process_unit:
            self.assertEqual(script.process_chunk("model", "api", 7, {"shards": {}}), "done")
        unit = process_unit.call_args.args[2]
        self.assertEqual((unit.kind, unit.name), ("general_corpus", "_general_corpus_chunk007"))
        self.assertEqual(unit.source_files, ("general_corpus/_general_corpus_chunk007.jsonl.gz",))


if __name__ == "__main__":
    unittest.main()
