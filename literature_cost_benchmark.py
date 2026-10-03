#!/usr/bin/env python3
"""
Measure what one Literature AI answer costs to serve, to set API prices.

Sends N questions to the public API (POST {base}/v1/literature/answers)
with an omni_sk_ key, while sampling the GPU with nvidia-smi on the host
that runs RAG + Ollama. Reports per-answer latency, GPU busy-seconds,
energy and cost, plus a suggested price per 1,000 answers.

Every successful answer is a billable request for the key's organization:
run it with a key from a test organization that has no quota set.

Usage (on the GPU host, so nvidia-smi sees the serving GPU):
    OMNIBIOAI_API_KEY=omni_sk_... python3 literature_cost_benchmark.py \\
        --base-url https://<gateway> --count 1000 --gpu-hourly-cost 2.50 \\
        --output cost.json --yes

Cost model:
  dedicated   = gpu_hourly_cost * wall_seconds / 3600 / answers
                (what a GPU reserved for the API costs per answer at the
                measured throughput -- raise --concurrency to find the
                throughput the hardware sustains)
  busy-only   = gpu_hourly_cost * gpu_busy_seconds / 3600 / answers
                (lower bound: only the time the GPU was actually working)
  suggested price per 1,000 = dedicated * markup * 1000
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

DEFAULT_QUESTIONS = [
    "What is the role of TP53 in cancer?",
    "Which genes are associated with lymphocytic variant hypereosinophilic syndrome?",
    "How does BRCA1 contribute to DNA repair?",
    "What are the main drug targets in EGFR-mutant lung cancer?",
    "Which biomarkers predict response to immune checkpoint inhibitors?",
    "What is known about APOE4 and Alzheimer's disease risk?",
    "How does CRISPR-Cas9 off-target activity get measured?",
    "What are the mechanisms of resistance to imatinib in CML?",
    "Which metabolic pathways are altered in type 2 diabetes?",
    "What is the evidence for gut microbiome effects on depression?",
    "How do KRAS G12C inhibitors work?",
    "What genes are linked to familial hypercholesterolemia?",
    "Which cytokines drive cytokine release syndrome after CAR-T therapy?",
    "What is the function of the CFTR protein?",
    "How is minimal residual disease detected in leukemia?",
    "What are the known pharmacogenomic variants affecting warfarin dosing?",
    "Which pathways regulate cellular senescence?",
    "What is the role of HLA-B*57:01 in abacavir hypersensitivity?",
    "How does single-cell RNA-seq identify rare cell types?",
    "What are the genetic risk factors for inflammatory bowel disease?",
]

NVIDIA_SMI = [
    "nvidia-smi",
    "--query-gpu=utilization.gpu,power.draw,memory.used",
    "--format=csv,noheader,nounits",
]


def read_gpu(run=subprocess.run):
    """One nvidia-smi reading per GPU as (util_pct, power_w, mem_mib), or
    None when nvidia-smi is unavailable or its output is unreadable."""
    try:
        out = run(NVIDIA_SMI, capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    readings = []
    for line in out.strip().splitlines():
        try:
            util, power, mem = (float(v.strip()) for v in line.split(","))
        except ValueError:
            return None
        readings.append((util, power, mem))
    return readings or None


class GpuSampler:
    """Samples nvidia-smi on a background thread and integrates busy time
    and energy over the run."""

    def __init__(self, interval=0.5, reader=read_gpu, clock=time.monotonic):
        self.interval = interval
        self.reader = reader
        self.clock = clock
        self.samples = []  # (timestamp, [(util, power, mem), ...])
        self._stop = threading.Event()
        self._thread = None

    def sample_once(self):
        reading = self.reader()
        if reading is not None:
            self.samples.append((self.clock(), reading))
        return reading is not None

    def _loop(self):
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self.interval)

    def start(self):
        if not self.sample_once():
            return False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def summary(self):
        """busy_seconds (sum over GPUs of util x time), energy_wh, peak
        memory; None without at least two samples."""
        if len(self.samples) < 2:
            return None
        busy = energy_j = 0.0
        for (t0, r0), (t1, _) in zip(self.samples, self.samples[1:]):
            dt = t1 - t0
            busy += sum(util / 100.0 for util, _, _ in r0) * dt
            energy_j += sum(power for _, power, _ in r0) * dt
        peak_mem = max(mem for _, reading in self.samples for _, _, mem in reading)
        return {
            "gpus": len(self.samples[0][1]),
            "samples": len(self.samples),
            "busy_seconds": busy,
            "energy_wh": energy_j / 3600.0,
            "peak_memory_mib": peak_mem,
        }


def ask(base_url, api_key, question, study, timeout=300, opener=urllib.request.urlopen):
    """One billable answer. Returns (status, seconds)."""
    body = json.dumps({"query": question, "study": study, "mode": "rag"}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/literature/answers",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    start = time.monotonic()
    try:
        with opener(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError):
        status = 0
    return status, time.monotonic() - start


def percentile(values, pct):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def build_report(results, wall_seconds, gpu, gpu_hourly_cost, markup):
    ok = [seconds for status, seconds in results if 200 <= status < 300]
    statuses = {}
    for status, _ in results:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
    report = {
        "requests": len(results),
        "answers": len(ok),
        "statuses": statuses,
        "wall_seconds": wall_seconds,
        "answers_per_minute": len(ok) / wall_seconds * 60 if wall_seconds and ok else 0.0,
        "latency_seconds": None,
        "gpu": gpu,
        "cost": None,
    }
    if not ok:
        return report
    report["latency_seconds"] = {
        "mean": statistics.mean(ok),
        "p50": percentile(ok, 50),
        "p95": percentile(ok, 95),
        "max": max(ok),
    }
    if gpu:
        gpu["busy_seconds_per_answer"] = gpu["busy_seconds"] / len(ok)
        gpu["energy_wh_per_answer"] = gpu["energy_wh"] / len(ok)
    if gpu_hourly_cost:
        dedicated = gpu_hourly_cost * wall_seconds / 3600 / len(ok)
        report["cost"] = {
            "gpu_hourly_cost": gpu_hourly_cost,
            "per_answer_dedicated": dedicated,
            "per_answer_busy_only": gpu_hourly_cost * gpu["busy_seconds"] / 3600 / len(ok) if gpu else None,
            "markup": markup,
            "suggested_price_per_1000": dedicated * markup * 1000,
        }
    return report


def format_report(report):
    lines = [
        f"Answers: {report['answers']} of {report['requests']} requests  statuses={report['statuses']}",
        f"Wall time: {report['wall_seconds']:.1f} s  throughput: {report['answers_per_minute']:.1f} answers/min",
    ]
    lat = report["latency_seconds"]
    if lat:
        lines.append(f"Latency: mean {lat['mean']:.2f} s  p50 {lat['p50']:.2f} s  p95 {lat['p95']:.2f} s")
    gpu = report["gpu"]
    if gpu and "busy_seconds_per_answer" in gpu:
        lines.append(
            f"GPU: {gpu['busy_seconds_per_answer']:.2f} busy-s/answer  "
            f"{gpu['energy_wh_per_answer']:.3f} Wh/answer  peak {gpu['peak_memory_mib']:.0f} MiB"
        )
    elif gpu is None:
        lines.append("GPU: not measured (nvidia-smi unavailable on this host)")
    cost = report["cost"]
    if cost:
        busy = cost["per_answer_busy_only"]
        lines.append(
            f"Cost/answer: ${cost['per_answer_dedicated']:.5f} dedicated"
            + (f", ${busy:.5f} busy-only" if busy is not None else "")
        )
        lines.append(f"Suggested price: ${cost['suggested_price_per_1000']:.2f} per 1,000 answers "
                     f"(markup x{cost['markup']:g})")
    return "\n".join(lines)


def run(args, ask_fn=ask, sampler=None, clock=time.monotonic):
    questions = DEFAULT_QUESTIONS
    if args.questions:
        with open(args.questions) as f:
            questions = [line.strip() for line in f if line.strip()]
    if not questions:
        raise SystemExit("No questions to send.")
    batch = [questions[i % len(questions)] for i in range(args.count)]

    sampler = sampler or GpuSampler(interval=args.sample_interval)
    measuring = sampler.start()
    start = clock()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda q: ask_fn(args.base_url, args.api_key, q, args.study), batch))
    wall = clock() - start
    sampler.stop()
    return build_report(results, wall, sampler.summary() if measuring else None, args.gpu_hourly_cost, args.markup)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Measure the serving cost of one Literature AI answer.")
    p.add_argument("--base-url", required=True, help="API gateway base URL")
    p.add_argument("--api-key", default=os.environ.get("OMNIBIOAI_API_KEY"),
                   help="omni_sk_ key (default: OMNIBIOAI_API_KEY)")
    p.add_argument("--count", type=int, default=1000, help="answers to request (default 1000)")
    p.add_argument("--concurrency", type=int, default=1, help="parallel requests (default 1)")
    p.add_argument("--questions", help="file with one question per line (default: built-in set)")
    p.add_argument("--study", default="default")
    p.add_argument("--gpu-hourly-cost", type=float, help="USD per GPU-hour, for cost figures")
    p.add_argument("--markup", type=float, default=4.0, help="price = cost x markup (default 4)")
    p.add_argument("--sample-interval", type=float, default=0.5, help="nvidia-smi interval, seconds")
    p.add_argument("--output", help="write the full report as JSON here")
    p.add_argument("--yes", action="store_true", help="skip the billing confirmation")
    args = p.parse_args(argv)
    if not args.api_key:
        p.error("--api-key or OMNIBIOAI_API_KEY is required")
    if args.count < 1 or args.concurrency < 1:
        p.error("--count and --concurrency must be at least 1")
    return args


def main(argv=None, input_fn=input, **run_kwargs):
    args = parse_args(argv)
    if not args.yes:
        reply = input_fn(f"This makes {args.count} billable requests for the key's organization. Continue? [y/N] ")
        if reply.strip().lower() != "y":
            print("Cancelled.")
            return 1
    report = run(args, **run_kwargs)
    print(format_report(report))
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
    return 0 if report["answers"] else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
