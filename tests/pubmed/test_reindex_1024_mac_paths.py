"""Offline data-root and invocation-location tests for the relocated worker."""
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import test_reindex_1024_mac_checkpoints as checkpoints

script = checkpoints.script


class DataRootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("DATA_ROOT", "SOURCE_ROOT", "OUTPUT_ROOT", "MANIFEST_FILE", "LOG_FILE"):
            patcher = mock.patch.object(script, name, getattr(script, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("OMNIBIOAI_PUBMED_ROOT", None)
        previous_cwd = Path.cwd()
        self.addCleanup(os.chdir, previous_cwd)
        os.chdir(self.root)

    def assert_paths(self, expected):
        expected = expected.resolve()
        self.assertEqual(script.DATA_ROOT, expected)
        self.assertEqual(script.SOURCE_ROOT, expected / "Abstracts/general_corpus")
        self.assertEqual(script.OUTPUT_ROOT, expected / "Index/_reindex_1024_mac")
        self.assertEqual(script.MANIFEST_FILE, expected / "reindex_1024_mac_manifest.json")
        self.assertEqual(script.LOG_FILE, expected / "reindex_1024_mac.log")
        unit = script.IndexingUnit("domains", "Cardiovascular")
        self.assertEqual(unit.output_dir, expected / "Index/_reindex_1024_mac/domains/Cardiovascular")

    def test_default_root_is_independent_of_code_and_working_directory(self):
        script.configure_data_root()
        self.assert_paths(Path("/Users/manishkumar/omnibioai-data/PubMed"))
        self.assertFalse((self.root / "Index").exists())
        self.assertEqual(Path(script.__file__).resolve().parent.name, "pubmed")

    def test_environment_root_is_expanded_and_resolved(self):
        os.environ["OMNIBIOAI_PUBMED_ROOT"] = str(self.root / "environment data")
        script.configure_data_root()
        self.assert_paths(self.root / "environment data")
        self.assertFalse(script.DATA_ROOT.exists())
        os.environ["OMNIBIOAI_PUBMED_ROOT"] = "~/pubmed-path-test"
        script.configure_data_root()
        self.assert_paths(Path.home() / "pubmed-path-test")

    def test_explicit_root_overrides_environment_and_resolves_relative_input(self):
        os.environ["OMNIBIOAI_PUBMED_ROOT"] = str(self.root / "environment")
        script.configure_data_root("selected data")
        self.assert_paths(self.root / "selected data")
        another_cwd = self.root / "another-cwd"
        another_cwd.mkdir()
        os.chdir(another_cwd)
        self.assert_paths(self.root / "selected data")

    def test_empty_environment_uses_mac_default(self):
        os.environ["OMNIBIOAI_PUBMED_ROOT"] = ""
        script.configure_data_root()
        self.assert_paths(script.DEFAULT_DATA_ROOT)

    def test_cli_root_precedence_and_all_runtime_files_from_unrelated_cwd(self):
        environment_root = self.root / "environment"
        explicit_root = self.root / "cli data"
        os.environ["OMNIBIOAI_PUBMED_ROOT"] = str(environment_root)
        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True))
        )
        for args, expected in (([], environment_root), (["--data-root", str(explicit_root)], explicit_root)):
            with self.subTest(args=args), \
                 mock.patch("sys.argv", [str(script.__file__), "--mode", "general"] + args), \
                 mock.patch.object(script, "torch", fake_torch), \
                 mock.patch.object(script, "HfApi") as api, \
                 mock.patch.object(script, "discover_units", return_value=[]) as discover, \
                 mock.patch.object(script, "SentenceTransformer") as model:
                script.main()
                self.assert_paths(expected)
                self.assertTrue(script.OUTPUT_ROOT.is_dir())
                self.assertIn(str(expected), script.LOG_FILE.read_text())
                manifest = script.load_manifest()
                script.save_manifest(manifest)
                self.assertTrue(script.MANIFEST_FILE.is_file())
                self.assertEqual(script.load_manifest(), manifest)
                model.assert_not_called()
                api.return_value.upload_folder.assert_not_called()
                discover.assert_called_once()
        self.assertFalse((self.root / "Index").exists())


if __name__ == "__main__":
    unittest.main()
