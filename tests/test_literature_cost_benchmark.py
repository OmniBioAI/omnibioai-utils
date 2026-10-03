"""literature_cost_benchmark.py: nvidia-smi parsing, GPU busy-time/energy
integration, request handling (success, HTTP error, network error), the
report and its cost model, text formatting, CLI validation, the billing
confirmation, and end-to-end runs with stubbed requests and GPU readings.

Developer: Manish Kumar <manish@omnibioai.org>
"""
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import literature_cost_benchmark as bench  # noqa: E402

KEY = "omni_sk_" + "a" * 40


def test_read_gpu_parses_each_gpu():
    run = lambda *a, **k: SimpleNamespace(stdout="50, 200.5, 1000\n100, 300, 2000\n")  # noqa: E731
    assert bench.read_gpu(run) == [(50.0, 200.5, 1000.0), (100.0, 300.0, 2000.0)]


@pytest.mark.parametrize("stdout", ["", "n/a, [N/A], 1\n"])
def test_read_gpu_unreadable_output(stdout):
    assert bench.read_gpu(lambda *a, **k: SimpleNamespace(stdout=stdout)) is None


@pytest.mark.parametrize("error", [FileNotFoundError("nvidia-smi"), subprocess.CalledProcessError(9, "x")])
def test_read_gpu_missing_tool(error):
    def run(*a, **k):
        raise error
    assert bench.read_gpu(run) is None


def test_sampler_integrates_busy_time_energy_and_memory():
    readings = iter([[(100.0, 300.0, 1000.0)], [(50.0, 100.0, 4000.0)], [(0.0, 50.0, 2000.0)]])
    times = iter([0.0, 2.0, 4.0])
    sampler = bench.GpuSampler(reader=lambda: next(readings), clock=lambda: next(times))
    for _ in range(3):
        assert sampler.sample_once()
    summary = sampler.summary()
    assert summary["busy_seconds"] == pytest.approx(2.0 + 1.0)
    assert summary["energy_wh"] == pytest.approx((300 * 2 + 100 * 2) / 3600)
    assert summary["peak_memory_mib"] == 4000.0
    assert summary["gpus"] == 1 and summary["samples"] == 3


def test_sampler_without_gpu_and_with_thread():
    assert bench.GpuSampler(reader=lambda: None).start() is False
    assert bench.GpuSampler(reader=lambda: None).summary() is None

    sampler = bench.GpuSampler(interval=0.01, reader=lambda: [(10.0, 50.0, 1.0)])
    assert sampler.start() is True
    import time
    time.sleep(0.05)
    sampler.stop()
    assert len(sampler.samples) >= 2
    bench.GpuSampler(reader=lambda: None).stop()


class _Resp:
    status = 200

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ask_success_sends_billable_request():
    seen = {}

    def opener(req, timeout):
        seen["req"], seen["timeout"] = req, timeout
        return _Resp()

    status, seconds = bench.ask("https://gw/", KEY, "q?", "Oncology", opener=opener)
    req = seen["req"]
    assert status == 200 and seconds >= 0
    assert req.full_url == "https://gw/v1/literature/answers"
    assert req.get_header("Authorization") == f"Bearer {KEY}"
    assert req.get_header("Idempotency-key")
    assert json.loads(req.data) == {"query": "q?", "study": "Oncology", "mode": "rag"}


def test_ask_http_and_network_errors():
    def http_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, io.BytesIO(b""))

    def down(req, timeout):
        raise urllib.error.URLError("refused")

    assert bench.ask("https://gw", KEY, "q", "s", opener=http_error)[0] == 402
    assert bench.ask("https://gw", KEY, "q", "s", opener=down)[0] == 0


def test_percentile():
    assert bench.percentile([3, 1, 2], 50) == 2
    assert bench.percentile([1, 2, 3, 4], 95) == 4
    assert bench.percentile([5], 99) == 5


def test_report_cost_model():
    gpu = {"gpus": 1, "samples": 10, "busy_seconds": 180.0, "energy_wh": 10.0, "peak_memory_mib": 9000.0}
    results = [(200, 2.0)] * 90 + [(500, 1.0)] * 10
    report = bench.build_report(results, 360.0, gpu, gpu_hourly_cost=2.0, markup=4)
    assert report["answers"] == 90 and report["statuses"] == {"200": 90, "500": 10}
    assert report["answers_per_minute"] == pytest.approx(15.0)
    assert report["gpu"]["busy_seconds_per_answer"] == pytest.approx(2.0)
    cost = report["cost"]
    assert cost["per_answer_dedicated"] == pytest.approx(2.0 * 360 / 3600 / 90)
    assert cost["per_answer_busy_only"] == pytest.approx(2.0 * 180 / 3600 / 90)
    assert cost["suggested_price_per_1000"] == pytest.approx(cost["per_answer_dedicated"] * 4000)
    text = bench.format_report(report)
    assert "Suggested price" in text and "busy-only" in text and "Wh/answer" in text


def test_report_without_gpu_cost_or_answers():
    no_gpu = bench.build_report([(200, 1.0)], 10.0, None, gpu_hourly_cost=1.0, markup=3)
    assert no_gpu["cost"]["per_answer_busy_only"] is None
    text = bench.format_report(no_gpu)
    assert "not measured" in text and "busy-only" not in text

    no_price = bench.build_report([(200, 1.0)], 10.0, None, gpu_hourly_cost=None, markup=3)
    assert no_price["cost"] is None and "Cost/answer" not in bench.format_report(no_price)

    failed = bench.build_report([(401, 0.1)], 1.0, None, gpu_hourly_cost=1.0, markup=3)
    assert failed["answers"] == 0 and failed["latency_seconds"] is None and failed["cost"] is None
    assert "Latency" not in bench.format_report(failed)

    gpu_no_answers = {"gpus": 1, "samples": 2, "busy_seconds": 1.0, "energy_wh": 0.1, "peak_memory_mib": 1.0}
    assert "busy-s" not in bench.format_report(bench.build_report([(500, 1.0)], 1.0, gpu_no_answers, None, 4))


def test_parse_args(monkeypatch):
    monkeypatch.delenv("OMNIBIOAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        bench.parse_args(["--base-url", "https://gw"])
    with pytest.raises(SystemExit):
        bench.parse_args(["--base-url", "https://gw", "--api-key", KEY, "--count", "0"])
    monkeypatch.setenv("OMNIBIOAI_API_KEY", KEY)
    args = bench.parse_args(["--base-url", "https://gw"])
    assert args.api_key == KEY and args.count == 1000 and args.markup == 4.0


class _Sampler:
    def __init__(self, ok=True):
        self.ok = ok

    def start(self):
        return self.ok

    def stop(self):
        pass

    def summary(self):
        return {"gpus": 1, "samples": 2, "busy_seconds": 4.0, "energy_wh": 1.0, "peak_memory_mib": 10.0}


def test_main_end_to_end(tmp_path, capsys):
    questions = tmp_path / "q.txt"
    questions.write_text("first?\n\nsecond?\n")
    output = tmp_path / "report.json"
    asked = []

    def fake_ask(base, key, question, study):
        asked.append(question)
        return 200, 0.5

    code = bench.main(
        ["--base-url", "https://gw", "--api-key", KEY, "--count", "4", "--questions", str(questions),
         "--gpu-hourly-cost", "3", "--output", str(output), "--yes", "--concurrency", "2"],
        ask_fn=fake_ask, sampler=_Sampler(),
    )
    assert code == 0
    assert sorted(asked) == ["first?", "first?", "second?", "second?"]
    report = json.loads(output.read_text())
    assert report["answers"] == 4 and report["gpu"]["busy_seconds_per_answer"] == 1.0
    assert "Suggested price" in capsys.readouterr().out


def test_main_confirmation_failures_and_empty_questions(tmp_path):
    args = ["--base-url", "https://gw", "--api-key", KEY, "--count", "1"]
    assert bench.main(args, input_fn=lambda prompt: "n") == 1
    assert bench.main(args, input_fn=lambda prompt: "y", ask_fn=lambda *a: (401, 0.1),
                      sampler=_Sampler(ok=False)) == 2

    empty = tmp_path / "empty.txt"
    empty.write_text("\n")
    with pytest.raises(SystemExit):
        bench.main(args + ["--questions", str(empty), "--yes"], sampler=_Sampler())
