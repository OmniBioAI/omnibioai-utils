#!/usr/bin/env python3

"""
Reindex one PubMed shard with mxbai-embed-large-v1 using CUDA/FP16.

Writes a new 1024-D FAISS index to _reindex_staging.
Does not modify the existing production index.

Author: Manish Kumar
"""

import json
import time
from pathlib import Path

import faiss
import numpy as np
import torch
from sentence_transformers import SentenceTransformer


# Configuration
SHARD = "_general_corpus_chunk100"

PUBMED_DIR = Path("/home/manish/Desktop/machine/data/PubMed")

INPUT_DIR = PUBMED_DIR / "Abstracts" / SHARD
STAGING_DIR = PUBMED_DIR / "Index" / "_reindex_staging" / SHARD

MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1"

BATCH_SIZE = 64
DIMENSION = 1024


def main():
    # Check GPU
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU not available")

    print("GPU:", torch.cuda.get_device_name(0))

    free, total = torch.cuda.mem_get_info()

    print(f"CUDA free: {free / 1024**3:.2f} GiB")


    # Prepare output
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    index_file = STAGING_DIR / "pubmed_index.faiss"
    pmid_file = STAGING_DIR / "pmid_map.json"
    metadata_file = STAGING_DIR / "metadata.json"


    # Load files
    files = sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file()
    )

    if not files:
        raise RuntimeError(f"No abstracts found in {INPUT_DIR}")

    print(f"Abstracts: {len(files):,}")


    # Load model
    print("\nLoading model...")

    model = SentenceTransformer(
        MODEL_NAME,
        device="cuda",
        model_kwargs={"torch_dtype": torch.float16}
    )

    print("Model device:", model.device)
    print("Model dtype:", next(model.parameters()).dtype)


    # FAISS cosine-similarity index
    index = faiss.IndexFlatIP(DIMENSION)

    pmids = []

    start = time.time()


    # Embed batch-by-batch
    for start_idx in range(0, len(files), BATCH_SIZE):

        batch_files = files[start_idx:start_idx + BATCH_SIZE]

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

            # Assumes filename is PMID, e.g. 12345678.txt
            batch_pmids.append(file_path.stem)

        if not texts:
            continue

        embeddings = model.encode(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False
        )

        embeddings = np.asarray(
            embeddings,
            dtype=np.float32
        )

        if embeddings.shape[1] != DIMENSION:
            raise RuntimeError(
                f"Unexpected dimension: {embeddings.shape[1]}"
            )

        index.add(embeddings)

        pmids.extend(batch_pmids)

        if index.ntotal % 10_000 < BATCH_SIZE:
            elapsed = time.time() - start
            rate = index.ntotal / elapsed

            print(
                f"{index.ntotal:,}/{len(files):,} "
                f"({rate:.1f} abstracts/sec)"
            )


    torch.cuda.synchronize()

    elapsed = time.time() - start


    # Save staging index
    print("\nSaving FAISS index...")

    faiss.write_index(
        index,
        str(index_file)
    )


    # Save PMID mapping
    with open(pmid_file, "w") as f:
        json.dump(pmids, f)


    # Save metadata
    metadata = {
        "shard": SHARD,
        "model": MODEL_NAME,
        "dimension": DIMENSION,
        "vectors": int(index.ntotal),
        "batch_size": BATCH_SIZE,
        "dtype": "float16 inference / float32 FAISS",
        "normalized": True,
        "faiss_index": "IndexFlatIP",
        "elapsed_seconds": elapsed,
        "abstracts_per_second": index.ntotal / elapsed,
    }

    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)


    # Validate saved index
    print("\nValidating...")

    test_index = faiss.read_index(
        str(index_file)
    )

    if test_index.d != DIMENSION:
        raise RuntimeError(
            f"Invalid index dimension: {test_index.d}"
        )

    if test_index.ntotal != len(pmids):
        raise RuntimeError(
            "FAISS vector count and PMID count do not match"
        )


    # Results
    rate = index.ntotal / elapsed

    estimated_hours = 75_000_000 / rate / 3600
    estimated_days = estimated_hours / 24


    print("\n==============================")
    print("REINDEX COMPLETE")
    print("==============================")

    print(f"Shard:           {SHARD}")
    print(f"Vectors:         {index.ntotal:,}")
    print(f"Dimension:       {test_index.d}")
    print(f"Elapsed:         {elapsed / 60:.2f} min")
    print(f"Throughput:      {rate:.2f} abstracts/sec")
    print(f"75M estimate:    {estimated_days:.2f} days")

    print(f"\nIndex:    {index_file}")
    print(f"PMID map: {pmid_file}")
    print(f"Metadata: {metadata_file}")

    print("==============================")

if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
