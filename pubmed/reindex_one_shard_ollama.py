#!/usr/bin/env python3

"""
Reindex one PubMed shard with Ollama mxbai-embed-large.

Author: Manish Kumar
"""

import json
import time
from pathlib import Path

import faiss
import numpy as np
import requests


SHARD = "_general_corpus_chunk100"

PUBMED_DIR = Path(
    "/home/manish/Desktop/machine/data/PubMed"
)

INPUT_DIR = PUBMED_DIR / "Abstracts" / SHARD

STAGING_DIR = (
    PUBMED_DIR
    / "Index"
    / "_reindex_staging_ollama"
    / SHARD
)

MODEL_NAME = "mxbai-embed-large"
OLLAMA_URL = "http://localhost:11434/api/embed"

BATCH_SIZE = 64
DIMENSION = 1024


def main():
    STAGING_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    index_file = STAGING_DIR / "pubmed_index.faiss"
    pmid_file = STAGING_DIR / "pmid_map.json"
    metadata_file = STAGING_DIR / "metadata.json"


    files = sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file()
    )

    if not files:
        raise RuntimeError("No abstracts found")

    print(f"Shard: {SHARD}")
    print(f"Abstracts: {len(files):,}")
    print(f"Model: {MODEL_NAME}")
    print(f"Batch size: {BATCH_SIZE}")


    # Warmup
    sample = files[0].read_text(
        encoding="utf-8",
        errors="ignore"
    ).strip()

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "input": [sample]
        },
        timeout=300
    )

    response.raise_for_status()


    # FAISS cosine-similarity index
    index = faiss.IndexFlatIP(DIMENSION)

    pmids = []

    start = time.time()


    for start_idx in range(
        0,
        len(files),
        BATCH_SIZE
    ):

        batch_files = files[
            start_idx:start_idx + BATCH_SIZE
        ]

        texts = []
        batch_pmids = []

        for file_path in batch_files:

            text = file_path.read_text(
                encoding="utf-8",
                errors="ignore"
            ).strip()

            if not text:
                continue

            texts.append(text)
            batch_pmids.append(file_path.stem)

        if not texts:
            continue

        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "input": texts
            },
            timeout=300
        )

        response.raise_for_status()

        embeddings = np.asarray(
            response.json()["embeddings"],
            dtype=np.float32
        )

        if embeddings.shape[1] != DIMENSION:
            raise RuntimeError(
                f"Unexpected dimension: "
                f"{embeddings.shape[1]}"
            )

        # Normalize before IndexFlatIP
        faiss.normalize_L2(embeddings)

        index.add(embeddings)
        pmids.extend(batch_pmids)

        if index.ntotal % 10_000 < BATCH_SIZE:

            elapsed = time.time() - start
            rate = index.ntotal / elapsed

            print(
                f"{index.ntotal:,}/"
                f"{len(files):,} "
                f"({rate:.1f} abstracts/sec)"
            )


    elapsed = time.time() - start


    print("\nSaving index...")

    faiss.write_index(
        index,
        str(index_file)
    )

    with open(pmid_file, "w") as f:
        json.dump(pmids, f)


    metadata = {
        "shard": SHARD,
        "model": MODEL_NAME,
        "provider": "ollama",
        "dimension": DIMENSION,
        "vectors": int(index.ntotal),
        "batch_size": BATCH_SIZE,
        "normalized": True,
        "faiss_index": "IndexFlatIP",
        "elapsed_seconds": elapsed,
        "abstracts_per_second":
            index.ntotal / elapsed,
    }

    with open(metadata_file, "w") as f:
        json.dump(
            metadata,
            f,
            indent=2
        )


    # Validate
    test_index = faiss.read_index(
        str(index_file)
    )

    if test_index.d != DIMENSION:
        raise RuntimeError(
            f"Invalid dimension: "
            f"{test_index.d}"
        )

    if test_index.ntotal != len(pmids):
        raise RuntimeError(
            "FAISS count and PMID count differ"
        )


    rate = index.ntotal / elapsed


    print("\n==============================")
    print("OLLAMA REINDEX COMPLETE")
    print("==============================")

    print(f"Shard:       {SHARD}")
    print(f"Vectors:     {index.ntotal:,}")
    print(f"Dimension:   {test_index.d}")
    print(f"Elapsed:     {elapsed / 60:.2f} min")
    print(f"Throughput:  {rate:.2f} abstracts/sec")

    print(f"\nIndex:    {index_file}")
    print(f"PMID map: {pmid_file}")
    print(f"Metadata: {metadata_file}")

    print("==============================")

if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
