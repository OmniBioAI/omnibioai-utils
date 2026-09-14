"""Offline checkpoint tests: real NumPy/filesystem, no model download or upload.

Run from the repository root: python3 -m unittest discover -s tests/pubmed -v
"""
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


# Load a private module with heavyweight/network dependencies stubbed, so these
# tests can run on a CPU machine without altering an installed module's globals.
spec = importlib.util.spec_from_file_location(
    "reindex_under_test",
    Path(__file__).resolve().parents[2] / "pubmed" / "reindex_1024_mac.py",
)
script = importlib.util.module_from_spec(spec)
with mock.patch.dict("sys.modules", {
    "faiss": types.SimpleNamespace(),
    "torch": types.SimpleNamespace(mps=types.SimpleNamespace(empty_cache=lambda: None)),
    "huggingface_hub": types.SimpleNamespace(HfApi=mock.Mock(), hf_hub_download=mock.Mock()),
    "sentence_transformers": types.SimpleNamespace(SentenceTransformer=mock.Mock()),
}):
    spec.loader.exec_module(script)


class FakeModel:
    max_seq_length = 512

    def __init__(self, failure_call=None, failure_type=RuntimeError):
        self.calls = []
        self.failure_call = failure_call
        self.failure_type = failure_type

    def _first_module(self):
        return types.SimpleNamespace(
            auto_model=types.SimpleNamespace(config=types.SimpleNamespace(_commit_hash="revision-a"))
        )

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        assert kwargs == dict(batch_size=32, normalize_embeddings=True,
                              convert_to_numpy=True, show_progress_bar=True)
        if len(self.calls) == self.failure_call:
            raise self.failure_type("simulated interruption")
        result = np.zeros((len(texts), 1024), dtype=np.float32)
        for row, text in enumerate(texts):
            result[row, int(text)] = 1
        return result


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = mock.patch.dict(script.os.environ, {"OMNIBIOAI_PUBMED_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        for name, value in {
            "DATA_ROOT": self.root,
            "LOG_FILE": self.root / "worker.log",
            "OUTPUT_ROOT": self.root / "output",
            "SOURCE_ROOT": self.root / "source",
            "MANIFEST_FILE": self.root / "manifest.json",
            "CHECKPOINT_ROWS": 2,
            "log": mock.Mock(),
        }.items():
            patcher = mock.patch.object(script, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.chunk = script.chunk_name(1)
        self.texts = [str(i) for i in range(5)]
        self.pmids = [f"pmid-{i}" for i in range(5)]
        self.directory = script.OUTPUT_ROOT / "_embedding_checkpoints" / self.chunk

    def embed(self, model=None):
        return script.embed_with_checkpoints(
            model or FakeModel(), self.chunk, self.texts, self.pmids
        )

    def path(self, block):
        return self.directory / f"block_{block:06d}.npz"

    def rewrite(self, block, change):
        path = self.path(block)
        with np.load(path, allow_pickle=False) as saved:
            vectors = saved["embeddings"]
            metadata = json.loads(saved["metadata"].item())
        vectors, metadata = change(vectors, metadata)
        np.savez(path, embeddings=vectors, metadata=json.dumps(metadata))

    def test_partial_final_block_and_full_reuse(self):
        model = FakeModel()
        expected, duration = self.embed(model)
        self.assertEqual(model.calls, [["0", "1"], ["2", "3"], ["4"]])
        self.assertEqual(expected.shape, (5, 1024))
        self.assertEqual(expected.dtype, np.float32)
        self.assertEqual(len(list(self.directory.glob("*.npz"))), 3)
        resumed = FakeModel()
        actual, resumed_duration = self.embed(resumed)
        self.assertEqual(resumed.calls, [])
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(duration, resumed_duration)

    def test_encode_exception_and_keyboard_interrupt_resume(self):
        for failure in (RuntimeError, KeyboardInterrupt):
            with self.subTest(failure=failure):
                # Reset only test-owned files between scenarios.
                for path in self.directory.glob("*"):
                    path.unlink()
                with self.assertRaises(failure):
                    self.embed(FakeModel(failure_call=2, failure_type=failure))
                original = self.path(1).read_bytes()
                self.assertFalse(self.path(2).exists())
                resumed = FakeModel()
                actual, _ = self.embed(resumed)
                self.assertEqual(resumed.calls, [["2", "3"], ["4"]])
                self.assertEqual(self.path(1).read_bytes(), original)
                np.testing.assert_array_equal(actual.argmax(axis=1), np.arange(5))

    def test_gap_and_stale_temporary_file(self):
        self.embed()
        self.path(2).unlink()
        (self.directory / "block_000002.npz.abandoned.tmp").write_bytes(b"partial")
        resumed = FakeModel()
        self.embed(resumed)
        self.assertEqual(resumed.calls, [["2", "3"]])

    def test_production_block_boundary(self):
        self.texts = [str(i % 1024) for i in range(10_001)]
        self.pmids = [str(i) for i in range(10_001)]
        with mock.patch.object(script, "CHECKPOINT_ROWS", 10_000):
            model = FakeModel()
            actual, _ = self.embed(model)
            self.assertEqual([len(call) for call in model.calls], [10_000, 1])
            resumed = FakeModel()
            recovered, _ = self.embed(resumed)
            self.assertEqual(resumed.calls, [])
            np.testing.assert_array_equal(actual, recovered)

    def test_invalid_new_vectors_never_published(self):
        model = FakeModel()
        model.encode = mock.Mock(return_value=np.full((2, 1024), np.nan, dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "NaN"):
            self.embed(model)
        self.assertEqual(list(self.directory.glob("*.npz")), [])

    def test_corrupt_vectors_recomputed(self):
        changes = {
            "rows": lambda v: v[:1],
            "dimension": lambda v: v[:, :100],
            "dtype": lambda v: v.astype(np.float64),
            "nan": lambda v: np.full_like(v, np.nan),
            "inf": lambda v: np.full_like(v, np.inf),
        }
        self.embed()
        for name, change in changes.items():
            with self.subTest(name=name):
                self.rewrite(2, lambda v, m: (change(v), m))
                resumed = FakeModel()
                self.embed(resumed)
                self.assertEqual(resumed.calls, [["2", "3"]])
        self.path(2).write_bytes(b"truncated zip")
        resumed = FakeModel()
        self.embed(resumed)
        self.assertEqual(resumed.calls, [["2", "3"]])

    def test_incompatible_settings_refused_before_encoding_gap(self):
        self.embed()
        original = self.path(3).read_bytes()
        self.path(1).unlink()
        for key, value in {
            "model": "different-model", "dimension": 768, "normalized": False,
            "model_revision": "revision-b", "max_seq_length": 256,
        }.items():
            with self.subTest(key=key):
                self.path(3).write_bytes(original)
                def change(vectors, metadata):
                    metadata["settings"][key] = value
                    return vectors, metadata
                self.rewrite(3, change)
                model = FakeModel()
                with self.assertRaises(script.IncompatibleCheckpoint):
                    self.embed(model)
                self.assertEqual(model.calls, [])

    def test_changed_source_or_pmid_order_refused(self):
        self.embed()
        for attribute in ("texts", "pmids"):
            with self.subTest(attribute=attribute):
                original = getattr(self, attribute)
                setattr(self, attribute, list(reversed(original)))
                with self.assertRaises(script.IncompatibleCheckpoint):
                    self.embed()
                setattr(self, attribute, original)

    def test_atomic_write_failures_preserve_committed_file(self):
        self.embed()
        path = self.path(1)
        expected = {**script.checkpoint_settings(FakeModel(), self.chunk, self.texts, self.pmids),
                    "start": 0, "stop": 2}
        vectors, seconds = script.read_checkpoint(path, expected)
        original = path.read_bytes()
        for target in ("np.savez", "os.fsync", "os.replace"):
            for error in (OSError("disk/write failure"), KeyboardInterrupt()):
                with self.subTest(target=target, error=type(error).__name__):
                    owner, name = target.split(".")
                    with mock.patch.object(getattr(script, owner), name, side_effect=error):
                        with self.assertRaises(type(error)):
                            script.write_checkpoint(path, vectors, expected, seconds)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_cli_range_and_chunk004_behavior(self):
        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True))
        )
        model = mock.Mock(device="mps")
        model.get_sentence_embedding_dimension.return_value = 1024
        with mock.patch.object(script, "torch", fake_torch), \
             mock.patch.object(script, "SentenceTransformer", return_value=model), \
             mock.patch.object(script, "discover_units", return_value=[
                 script.IndexingUnit("general_corpus", script.chunk_name(i)) for i in (3, 4, 5)
             ]), \
             mock.patch.object(script, "process_unit") as process:
            for extra, expected in (([], [3, 5]), (["--include-004"], [3, 4, 5])):
                with self.subTest(extra=extra), mock.patch(
                    "sys.argv", ["reindex_1024_mac.py", "--start", "3", "--end", "5"] + extra
                ):
                    process.reset_mock()
                    script.main()
                    self.assertEqual([call.kwargs["unit"].name for call in process.call_args_list],
                                     [script.chunk_name(i) for i in expected])


if __name__ == "__main__":
    unittest.main()
