#!/usr/bin/env python3

"""
Benchmark mxbai-embed-large GPU embedding via Ollama on one PubMed chunk.

Author: Manish Kumar
"""

import time
from pathlib import Path

import requests


# Configuration
INPUT_DIR = Path(
    "/home/manish/Desktop/machine/data/PubMed/Abstracts/_general_corpus_chunk100"
)

MODEL_NAME = "mxbai-embed-large"
OLLAMA_URL = "http://localhost:11434/api/embed"

BATCH_SIZE = 64
MAX_FILES = 100_000
TOTAL_ABSTRACTS = 75_000_000


# Load abstracts
print("Reading abstracts...")

abstracts = []

for file_path in INPUT_DIR.iterdir():

    if not file_path.is_file():
        continue

    try:
        text = file_path.read_text(
            encoding="utf-8",
            errors="ignore"
        ).strip()

        if text:
            abstracts.append(text)

    except Exception as e:
        print("Skipping:", file_path, e)

    if len(abstracts) >= MAX_FILES:
        break


if not abstracts:
    raise RuntimeError("No abstracts found")

print(f"Loaded abstracts: {len(abstracts):,}")


# Warmup
print("\nOllama GPU warmup...")

response = requests.post(
    OLLAMA_URL,
    json={
        "model": MODEL_NAME,
        "input": abstracts[:32]
    },
    timeout=300
)

response.raise_for_status()


# Benchmark
print("\nStarting benchmark...")
print(f"Model: {MODEL_NAME}")
print(f"Batch size: {BATCH_SIZE}")

start = time.time()

count = 0
dimension = None

for i in range(0, len(abstracts), BATCH_SIZE):

    batch = abstracts[i:i + BATCH_SIZE]

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "input": batch
        },
        timeout=300
    )

    response.raise_for_status()

    embeddings = response.json()["embeddings"]

    if dimension is None:
        dimension = len(embeddings[0])

    count += len(embeddings)

    if count % 10_000 == 0:
        print(f"Embedded: {count:,}")


elapsed = time.time() - start


# Results
rate = count / elapsed
estimated_hours = TOTAL_ABSTRACTS / rate / 3600
estimated_days = estimated_hours / 24


print("\n==============================")
print("OLLAMA GPU BENCHMARK RESULTS")
print("==============================")

print(f"Abstracts:       {count:,}")
print(f"Embedding dim:   {dimension}")
print(f"Elapsed time:    {elapsed:.2f} sec")
print(f"Throughput:      {rate:.2f} abstracts/sec")
print(f"75M estimate:    {estimated_hours:.2f} hours")
print(f"                 {estimated_days:.2f} days")

print("==============================")