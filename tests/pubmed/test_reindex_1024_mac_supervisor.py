"""Offline tests for the one-fresh-process-per-unit supervisor."""
import json
import signal
import types
import unittest
from unittest import mock

import test_reindex_1024_mac_checkpoints as checkpoints


script = checkpoints.script


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        checkpoints.CheckpointTests.setUp(self)
        self.units = [
            script.IndexingUnit("general_corpus", script.chunk_name(1)),
            script.IndexingUnit("domains", "Cardiovascular"),
            script.IndexingUnit("domains", "Neurology"),
        ]

    def child(self, exit_code=0):
        child = mock.Mock(returncode=exit_code)
        child.wait.return_value = exit_code
        child.poll.return_value = exit_code
        return child

    def test_worker_command_selects_exactly_one_unit_and_preserves_data_root(self):
        command = script.worker_command(self.units[1])
        self.assertEqual(command[:3], [script.sys.executable, "-X", "faulthandler"])
        self.assertEqual(command[3], str(script.Path(script.__file__).resolve()))
        self.assertEqual(command[-4:], [
            "--data-root", str(script.DATA_ROOT), "--worker-unit", "domains/Cardiovascular",
        ])

    def test_supervisor_waits_for_each_fresh_worker(self):
        children = [self.child(), self.child(), self.child()]
        with mock.patch.object(script.subprocess, "Popen", side_effect=children) as popen, \
             mock.patch.object(script, "worker_completion_recorded", return_value=True), \
             mock.patch.object(script, "record_supervisor_result") as record:
            self.assertEqual(script.supervise_units(self.units), 0)
        self.assertEqual(popen.call_count, 3)
        self.assertEqual([call.args[0][-1] for call in popen.call_args_list],
                         [unit.key for unit in self.units])
        self.assertTrue(all(child.wait.call_count == 1 for child in children))
        self.assertEqual([call.args[1:] for call in record.call_args_list],
                         [("COMPLETE", 0)] * 3)

    def test_nonzero_worker_stops_migration_and_is_recorded(self):
        children = [self.child(), self.child(7)]
        with mock.patch.object(script.subprocess, "Popen", side_effect=children) as popen, \
             mock.patch.object(script, "worker_completion_recorded", return_value=True), \
             mock.patch.object(script, "record_supervisor_result") as record:
            self.assertEqual(script.supervise_units(self.units), 7)
        self.assertEqual(popen.call_count, 2)
        record.assert_has_calls([
            mock.call(self.units[0], "COMPLETE", 0),
            mock.call(self.units[1], "FAILED", 7),
        ])

    def test_zero_exit_without_uploaded_receipt_stops_migration(self):
        with mock.patch.object(script.subprocess, "Popen", return_value=self.child()) as popen, \
             mock.patch.object(script, "worker_completion_recorded", return_value=False), \
             mock.patch.object(script, "record_supervisor_result") as record:
            self.assertEqual(script.supervise_units(self.units), 1)
        popen.assert_called_once()
        record.assert_called_once_with(self.units[0], "FAILED", 1)

    def test_ctrl_c_interrupts_current_worker_and_never_starts_next(self):
        child = self.child(130)
        child.wait.side_effect = [
            KeyboardInterrupt(),
            script.subprocess.TimeoutExpired("worker", 30),
            130,
        ]
        child.poll.return_value = None
        with mock.patch.object(script.subprocess, "Popen", return_value=child) as popen, \
             mock.patch.object(script, "record_supervisor_result") as record:
            self.assertEqual(script.supervise_units(self.units), 130)
        popen.assert_called_once()
        child.send_signal.assert_called_once_with(signal.SIGINT)
        record.assert_called_once_with(self.units[0], "INTERRUPTED", 130)

    def test_model_initialization_failure_is_persisted_without_loading_faiss(self):
        fake_torch = types.SimpleNamespace(
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: True))
        )
        with mock.patch.object(script, "torch", fake_torch), \
             mock.patch.object(script, "SentenceTransformer", side_effect=RuntimeError("model failed")), \
             mock.patch.object(script, "load_faiss") as load_faiss, \
             self.assertRaisesRegex(RuntimeError, "model failed"):
            script.run_worker(self.units[0], mock.Mock())
        load_faiss.assert_not_called()
        saved = json.loads(script.MANIFEST_FILE.read_text())["shards"][self.units[0].key]
        self.assertEqual(saved["status"], "FAILED")
        self.assertEqual(saved["error"], "model failed")

    def test_status_checks_remote_without_starting_worker(self):
        with mock.patch.object(script, "remote_completed_unit",
                               side_effect=[{"metadata": {}}, None]), \
             mock.patch.object(script, "load_manifest", return_value={
                 "shards": {self.units[0].key: {"status": "UPLOADED"}}
             }):
            script.report_unit_status(mock.Mock(), self.units[:2], show_manifest=True)
        messages = [call.args[0] for call in script.log.call_args_list]
        self.assertTrue(any("remote=COMPLETE; local=UPLOADED" in message
                            for message in messages))
        self.assertTrue(any("remote=PENDING; local=UNKNOWN" in message
                            for message in messages))
        self.assertTrue(any("remaining=1" in message for message in messages))

    def test_dry_run_cli_never_initializes_model_or_starts_worker(self):
        with mock.patch.object(script, "discover_units", return_value=self.units), \
             mock.patch.object(script, "remote_completed_unit", return_value=None), \
             mock.patch.object(script, "SentenceTransformer") as model, \
             mock.patch.object(script.subprocess, "Popen") as popen, \
             mock.patch("sys.argv", ["worker", "--mode", "all", "--dry-run"]):
            script.main()
        model.assert_not_called()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
