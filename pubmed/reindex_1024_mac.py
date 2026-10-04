#!/usr/bin/env python3

"""
Resumable PubMed 1024-D reindex on Apple Silicon MPS.

Pipeline per indexing unit (general shard or domain):
    check remote -> obtain source -> embed -> validate -> FAISS -> atomic save
    -> self-retrieval -> upload -> verify -> persist UPLOADED
    Embedding checkpoints/assemblies are retained as durable recovery assets.

Author: Manish Kumar
"""

import argparse
import gzip
import hashlib
import json
import os
import random
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from huggingface_hub import HfApi, hf_hub_download


MODEL_NAME = "mixedbread-ai/mxbai-embed-large-v1"
DIMENSION = 1024
DEVICE = "mps"
BATCH_SIZE = 32
CHECKPOINT_ROWS = 10_000
NORMALIZE_EMBEDDINGS = True
SELF_TEST_N = 50
SEED = 42

HF_REPO = "omnibioai/pubmed-faiss-indexes"
HF_PREFIX = "mxbai-1024/general_corpus"
HF_DOMAIN_PREFIX = "mxbai-1024/domains"
HF_SOURCE_REPO = "omnibioai/pubmed-abstracts-36M"
HF_SOURCE_PREFIX = "general_corpus"
ARTIFACT_NAMES = ("index.faiss", "pmid_map.json", "metadata.json")

DEFAULT_DATA_ROOT = Path("/Users/manishkumar/omnibioai-data/PubMed")

# The supervisor intentionally stays free of PyTorch/MPS state. Each exact-unit
# worker imports this runtime afresh.
torch = None
SentenceTransformer = None


def load_mps_runtime():
    global torch, SentenceTransformer
    if torch is None:
        import torch as torch_module

        torch = torch_module
    if SentenceTransformer is None:
        from sentence_transformers import SentenceTransformer as transformer_class

        SentenceTransformer = transformer_class
    return torch, SentenceTransformer


# FAISS loads native BLAS/OpenMP libraries.  On macOS, loading those libraries
# before PyTorch has initialized MPS can crash during SentenceTransformer's
# model construction.  Keep this name patchable for the offline tests, but do
# not load the native module until after the MPS model is ready (see main()).
faiss = None


def load_faiss():
    """Load FAISS after the MPS embedding model has initialized."""
    global faiss
    if faiss is None:
        import faiss as faiss_module

        faiss = faiss_module
    return faiss


def configure_data_root(data_root=None):
    """Resolve all runtime paths independently of the worker's code location.

    Explicit CLI/programmatic root > environment > the existing Mac default.
    Relative explicit paths are resolved against the caller's working directory.
    This function selects paths only; it does not create or move any data.
    """
    global DATA_ROOT, SOURCE_ROOT, OUTPUT_ROOT, MANIFEST_FILE, LOG_FILE
    selected = data_root if data_root is not None else (
        os.environ.get("OMNIBIOAI_PUBMED_ROOT") or DEFAULT_DATA_ROOT
    )
    DATA_ROOT = Path(selected).expanduser().resolve()
    SOURCE_ROOT = DATA_ROOT / "Abstracts" / "general_corpus"
    OUTPUT_ROOT = DATA_ROOT / "Index" / "_reindex_1024_mac"
    MANIFEST_FILE = DATA_ROOT / "reindex_1024_mac_manifest.json"
    LOG_FILE = DATA_ROOT / "reindex_1024_mac.log"


configure_data_root()


MIN_FREE_GIB = 150


@dataclass(frozen=True)
class IndexingUnit:
    kind: str
    name: str
    source_files: tuple = ()
    source_revision: str = None

    def __post_init__(self):
        if self.kind not in ("general_corpus", "domains"):
            raise ValueError(f"Invalid indexing namespace: {self.kind}")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", self.name):
            raise ValueError(f"Invalid indexing unit name: {self.name}")
        if self.kind == "general_corpus" and not re.fullmatch(r"_general_corpus_chunk\d{3,}", self.name):
            raise ValueError(f"Invalid general corpus shard name: {self.name}")
        for path in self.source_files:
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError(f"Invalid remote source path: {path}")

    @property
    def key(self):
        # Keep existing general shard manifest/checkpoint/output paths compatible.
        return self.name if self.kind == "general_corpus" else f"domains/{self.name}"

    @property
    def remote_path(self):
        prefix = HF_PREFIX if self.kind == "general_corpus" else HF_DOMAIN_PREFIX
        return f"{prefix}/{self.name}"

    @property
    def output_dir(self):
        return OUTPUT_ROOT / self.key

    @property
    def worker_prefix(self):
        return f"{self.kind}--{self.name}-"


class IncompatibleCheckpoint(RuntimeError):
    """A complete checkpoint belongs to a different embedding job."""


def checkpoint_settings(model, chunk, texts, pmids):
    # Hash exactly the ordered inputs used by encode and the PMID mapping.
    # Length prefixes avoid ambiguous concatenations; do not copy the corpus.
    digest = hashlib.sha256()
    for pmid, text in zip(pmids, texts):
        for value in (pmid, text):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)

    first_module = model._first_module()
    config = getattr(getattr(first_module, "auto_model", None), "config", None)
    return {
        "version": 1,
        "chunk": chunk,
        "model": MODEL_NAME,
        "model_revision": getattr(config, "_commit_hash", None),
        "max_seq_length": model.max_seq_length,
        "dimension": DIMENSION,
        "normalized": NORMALIZE_EMBEDDINGS,
        "device": DEVICE,
        "batch_size": BATCH_SIZE,
        "checkpoint_rows": CHECKPOINT_ROWS,
        "total_rows": len(texts),
        "source_sha256": digest.hexdigest(),
    }


def validate_checkpoint_vectors(vectors, rows):
    if vectors.shape != (rows, DIMENSION):
        raise ValueError(f"Checkpoint shape {vectors.shape}; expected {(rows, DIMENSION)}")
    if vectors.dtype != np.dtype(np.float32):
        raise ValueError(f"Checkpoint dtype {vectors.dtype}; expected float32")
    if not np.isfinite(vectors).all():
        raise ValueError("Checkpoint contains NaN or Inf")


def read_checkpoint(path, expected):
    with np.load(path, allow_pickle=False) as saved:
        metadata = json.loads(saved["metadata"].item())
        if metadata["settings"] != expected:
            raise IncompatibleCheckpoint(
                f"Incompatible checkpoint: {path}. Source or embedding settings changed; "
                "use the original settings/source or move this shard's checkpoint directory "
                "aside to explicitly start over."
            )
        seconds = metadata["embedding_seconds"]
        if not isinstance(seconds, (int, float)) or not np.isfinite(seconds) or seconds <= 0:
            raise ValueError("Invalid checkpoint embedding duration")
        vectors = saved["embeddings"]
        validate_checkpoint_vectors(vectors, expected["stop"] - expected["start"])
    return vectors, seconds


def write_checkpoint(path, vectors, settings, seconds):
    validate_checkpoint_vectors(vectors, settings["stop"] - settings["start"])
    # Unique temporary file on the same filesystem: only the atomic rename
    # publishes a checkpoint. Leftover .tmp files are never read on restart.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            np.savez(
                handle,
                embeddings=vectors,
                metadata=json.dumps({"settings": settings, "embedding_seconds": seconds}),
            )
            handle.flush()
            os.fsync(handle.fileno())
        checked, _ = read_checkpoint(temporary, settings)
        del checked
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resume_completed_embeddings(model, chunk, texts, pmids):
    """Adopt legacy assembled files only after checking every source-bound block.

    Read-only throughout: a mismatched source never truncates assembled.npy.
    Block comparisons also detect an assembly interrupted before its final flush.
    """
    directory = OUTPUT_ROOT / "_embedding_checkpoints" / chunk
    settings = checkpoint_settings(model, chunk, texts, pmids)
    receipt_path = directory / "embedding_complete.json"
    assembled = directory / "assembled.npy"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt["settings"] != settings:
            raise IncompatibleCheckpoint("Completed embedding settings/source changed")
        name = receipt["assembled_file"]
        if Path(name).name != name:
            raise ValueError("Invalid assembled filename")
        assembled = directory / name
    vectors = None
    if assembled.exists():
        vectors = np.load(assembled, mmap_mode="r", allow_pickle=False)
        if vectors.shape != (len(texts), DIMENSION) or vectors.dtype != np.float32:
            raise ValueError("Assembled vector shape/dtype mismatch")
    seconds = 0.0
    complete = vectors is not None
    for block, start in enumerate(range(0, len(texts), CHECKPOINT_ROWS), 1):
        stop = min(start + CHECKPOINT_ROWS, len(texts))
        path = directory / f"block_{block:06d}.npz"
        if not path.exists():
            complete = False
            continue
        try:
            saved, duration = read_checkpoint(path, {**settings, "start": start, "stop": stop})
        except IncompatibleCheckpoint:
            raise
        except (OSError, ValueError, KeyError):
            complete = False
            continue
        seconds += duration
        if vectors is not None and not np.array_equal(saved, vectors[start:stop]):
            complete = False
    if not complete:
        return None
    # Legacy adoption publishes only a small receipt; never rewrites the vectors.
    atomic_json(receipt_path, {"settings": settings, "assembled_file": assembled.name,
                               "embedding_seconds": seconds})
    return vectors, seconds


def embed_with_checkpoints(model, chunk, texts, pmids):
    checkpoint_dir = OUTPUT_ROOT / "_embedding_checkpoints" / chunk
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    settings = checkpoint_settings(model, chunk, texts, pmids)
    total = len(texts)
    blocks = (total + CHECKPOINT_ROWS - 1) // CHECKPOINT_ROWS
    resumed = resume_completed_embeddings(model, chunk, texts, pmids)
    if resumed is not None:
        return resumed
    # Assemble into a NEW file. Keep any previous assembled.npy untouched,
    # including when source/settings validation subsequently refuses the job.
    handle = tempfile.NamedTemporaryFile(dir=checkpoint_dir, prefix="assembled.",
                                         suffix=".npy", delete=False)
    assembled_path = Path(handle.name)
    handle.close()
    embeddings = np.lib.format.open_memmap(
        assembled_path, mode="w+", dtype=np.float32, shape=(total, DIMENSION),
    )
    completed = 0
    embed_seconds = 0.0
    new_rows = 0
    new_seconds = 0.0

    # Validate ALL existing blocks before encoding. This detects incompatible
    # later blocks even when an earlier block is missing, and gives an accurate
    # remaining-work estimate when checkpoints have gaps.
    pending = []
    for block, start in enumerate(range(0, total, CHECKPOINT_ROWS), start=1):
        stop = min(start + CHECKPOINT_ROWS, total)
        expected = {**settings, "start": start, "stop": stop}
        path = checkpoint_dir / f"block_{block:06d}.npz"
        if path.exists():
            try:
                vectors, seconds = read_checkpoint(path, expected)
            except IncompatibleCheckpoint:
                raise
            except Exception as exc:
                log(f"{chunk}: block {block}/{blocks} invalid; will recompute: {exc}")
            else:
                embeddings[start:stop] = vectors
                del vectors
                completed += stop - start
                embed_seconds += seconds
                log(f"{chunk}: block {block}/{blocks} checkpoint reused; "
                    f"completed={completed:,}/{total:,} ({completed / total:.2%})")
                continue
        pending.append((block, start, stop, path, expected))

    for block, start, stop, path, expected in pending:
        cumulative_rate = new_rows / new_seconds if new_seconds else (
            completed / embed_seconds if embed_seconds else 0.0
        )
        eta = (f"{(total - completed) / cumulative_rate / 3600:.2f} h"
               if cumulative_rate else "unknown")
        log(f"{chunk}: block {block}/{blocks} embedding rows {start:,}:{stop:,}; "
            f"completed={completed:,}/{total:,} ({completed / total:.2%}); "
            f"cumulative throughput={cumulative_rate:.2f} abs/sec; ETA={eta}")
        started = time.perf_counter()
        vectors = model.encode(
            texts[start:stop],
            batch_size=BATCH_SIZE,
            normalize_embeddings=NORMALIZE_EMBEDDINGS,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
        seconds = time.perf_counter() - started
        vectors = np.asarray(vectors, dtype=np.float32)
        write_checkpoint(path, vectors, expected, seconds)
        embeddings[start:stop] = vectors
        del vectors
        torch.mps.empty_cache()
        completed += stop - start
        embed_seconds += seconds
        new_rows += stop - start
        new_seconds += time.perf_counter() - started
        block_rate = (stop - start) / seconds
        cumulative_rate = completed / embed_seconds
        log(f"{chunk}: block {block}/{blocks} checkpoint committed; "
            f"completed={completed:,}/{total:,} ({completed / total:.2%}); "
            f"block throughput={block_rate:.2f} abs/sec; "
            f"cumulative throughput={cumulative_rate:.2f} abs/sec; "
            f"ETA={(total - completed) / cumulative_rate / 3600:.2f} h")

    embeddings.flush()
    with assembled_path.open("rb") as handle:
        os.fsync(handle.fileno())
    # Publish the first assembly under the legacy name, but never replace one.
    legacy = checkpoint_dir / "assembled.npy"
    if not legacy.exists():
        os.rename(assembled_path, legacy)
        assembled_path = legacy
    atomic_json(checkpoint_dir / "embedding_complete.json", {
        "settings": settings, "assembled_file": assembled_path.name,
        "embedding_seconds": embed_seconds,
    })
    return embeddings, embed_seconds


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def log(message):
    line = f"{now()} {message}"

    print(
        line,
        flush=True,
    )

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            line + "\n"
        )


def load_manifest():
    if not MANIFEST_FILE.exists():
        return {
            "model": MODEL_NAME,
            "dimension": DIMENSION,
            "device": DEVICE,
            "batch_size": BATCH_SIZE,
            "hf_repo": HF_REPO,
            "hf_prefix": HF_PREFIX,
            "created_at": now(),
            "shards": {},
        }

    with MANIFEST_FILE.open(
        "r",
        encoding="utf-8",
    ) as handle:
        return json.load(handle)


def save_manifest(manifest):
    atomic_json(MANIFEST_FILE, manifest)


def atomic_json(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_receipt(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def atomic_faiss_index(module, index, path):
    """Only rename a fully flushed native write; abandoned .tmp files are ignored."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".",
                                     suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        module.write_index(index, str(temporary))
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def free_disk_gib(path):
    usage = shutil.disk_usage(path)

    return (
        usage.free
        / 1024**3
    )


def chunk_name(number):
    return (
        f"_general_corpus_chunk"
        f"{number:03d}"
    )


def source_stem(path):
    for suffix in (".jsonl.gz", ".jsonl", ".json"):
        if path.endswith(suffix):
            return path[:-len(suffix)]
    return None


def discover_units(api, mode="all", start=None, end=None, domain=None):
    """Discover a pinned source snapshot; group flat or directory domain shards.

    Supported domain layouts: NAME.jsonl.gz, NAME_chunkNNN.jsonl.gz,
    NAME/<files>, domains/NAME.jsonl.gz, and domains/NAME/<files>.
    All supported files below a domain directory belong to that one domain.
    """
    revision = api.repo_info(HF_SOURCE_REPO, repo_type="dataset").sha
    general = {}
    domains = {}
    for entry in api.list_repo_tree(
        HF_SOURCE_REPO, repo_type="dataset", revision=revision, recursive=True,
    ):
        if not hasattr(entry, "size"):
            continue
        path = entry.path
        if any(part.startswith(".") for part in Path(path).parts):
            continue
        if Path(path).name in ("dataset_infos.json", "dataset_info.json", "metadata.json", "state.json"):
            continue
        match = re.fullmatch(
            r"general_corpus/_general_corpus_chunk(\d{3,})\.jsonl\.gz", path,
        )
        if match:
            number = int(match.group(1))
            if path == f"general_corpus/{chunk_name(number)}.jsonl.gz":
                if (start is None or number >= start) and (end is None or number <= end):
                    general[number] = IndexingUnit(
                        "general_corpus", chunk_name(number), (path,), revision,
                    )
            continue
        if path.startswith("general_corpus/") or source_stem(path) is None:
            continue
        relative = path.removeprefix("domains/")
        if "/" in relative:
            name = relative.split("/", 1)[0]
        else:
            name = source_stem(relative)
            name = re.sub(r"(?:[_-](?:chunk|part|shard)[_-]?\d+|[-_]\d{5}-of-\d{5})$", "", name)
        if domain is None or name == domain:
            domains.setdefault(name, []).append(path)
    units = []
    if mode in ("general", "all"):
        units.extend(general[number] for number in sorted(general))
    if mode in ("domains", "all"):
        units.extend(IndexingUnit("domains", name, tuple(sorted(paths)), revision)
                     for name, paths in sorted(domains.items()))
    log(f"Discovered {len(units)} indexing units at {HF_SOURCE_REPO}@{revision}: "
        f"{[unit.key for unit in units]}")
    if domain is not None and not any(unit.kind == "domains" for unit in units):
        raise RuntimeError(f"Domain not found in remote source discovery: {domain}")
    return units


def load_abstracts(source_dir):

    texts = []
    pmids = []

    files = sorted(
        source_dir.glob("*.json")
    )

    log(
        f"Source files: "
        f"{len(files):,}"
    )

    for i, file in enumerate(
        files,
        start=1,
    ):

        try:
            with file.open(
                "r",
                encoding="utf-8",
            ) as handle:
                data = json.load(
                    handle
                )

        except Exception as exc:
            log(
                f"WARNING malformed "
                f"{file.name}: {exc}"
            )
            continue

        if not isinstance(data, dict):
            log(f"WARNING invalid abstract record: {file.name}")
            continue

        pmid = str(
            data.get("pmid")
            or file.stem
        ).strip()

        title = str(
            data.get("title")
            or ""
        ).strip()

        abstract = str(
            data.get("abstract")
            or ""
        ).strip()

        text = (
            f"{title}\n\n{abstract}"
        ).strip()

        if not pmid:
            continue

        if not text:
            continue

        pmids.append(
            pmid
        )

        texts.append(
            text
        )

        if i % 100_000 == 0:
            log(
                f"Scanned "
                f"{i:,}/{len(files):,}; "
                f"usable={len(texts):,}"
            )

    return (
        texts,
        pmids,
        len(files),
    )


def validate_vectors(
    embeddings,
):
    if (
        embeddings.ndim != 2
        or embeddings.shape[1]
        != DIMENSION
    ):
        raise RuntimeError(
            f"Invalid embedding "
            f"shape: "
            f"{embeddings.shape}"
        )

    norm_sum = 0.0
    min_norm = float("inf")
    max_norm = 0.0
    for start in range(0, len(embeddings), CHECKPOINT_ROWS):
        block = embeddings[start:start + CHECKPOINT_ROWS]
        if not np.isfinite(block).all():
            raise RuntimeError("Embeddings contain NaN or Inf")
        norms = np.linalg.norm(block, axis=1)
        norm_sum += float(norms.sum(dtype=np.float64))
        min_norm = min(min_norm, float(norms.min()))
        max_norm = max(max_norm, float(norms.max()))
    return norm_sum / len(embeddings), min_norm, max_norm


def self_retrieval_test(
    index,
    embeddings,
    n=SELF_TEST_N,
):

    rng = random.Random(
        SEED
    )

    n = min(
        n,
        len(embeddings),
    )

    sample_ids = rng.sample(
        range(len(embeddings)),
        n,
    )

    top1 = 0
    top5 = 0
    top10 = 0

    for idx in sample_ids:

        query = embeddings[
            idx:idx + 1
        ]

        scores, neighbors = (
            index.search(
                query,
                10,
            )
        )

        hits = neighbors[
            0
        ].tolist()

        if hits[0] == idx:
            top1 += 1

        if idx in hits[:5]:
            top5 += 1

        if idx in hits[:10]:
            top10 += 1

    return {
        "queries": n,
        "top1": top1 / n,
        "top5": top5 / n,
        "top10": top10 / n,
    }


def remote_completed_unit(api, unit, revision=None):
    """Check one immutable remote snapshot, downloading only small metadata.

    Earlier 1024-D PASS metadata is supported. This check trusts its recorded
    validation; post-upload verification additionally compares all local bytes.
    Network/auth errors propagate rather than being treated as absent artifacts.
    """
    revision = revision or api.repo_info(HF_REPO, repo_type="dataset").sha
    remote_path = unit.remote_path
    paths = [f"{remote_path}/{name}" for name in ARTIFACT_NAMES]
    entries = api.get_paths_info(
        HF_REPO, paths=paths, repo_type="dataset", revision=revision,
    )
    files = {entry.path: entry for entry in entries if getattr(entry, "size", 0) > 0}
    if not set(paths).issubset(files):
        return None
    with tempfile.TemporaryDirectory(prefix="pubmed-remote-check-") as scratch:
        metadata_path = hf_hub_download(
            repo_id=HF_REPO, repo_type="dataset", filename=paths[2],
            revision=revision, local_dir=scratch,
        )
        try:
            with Path(metadata_path).open(encoding="utf-8") as handle:
                metadata = json.load(handle)
            count = metadata["vector_count"]
            valid = (
                metadata["chunk"] == unit.name
                and metadata.get("corpus_type", "general_corpus") == unit.kind
                and (unit.kind != "domains" or metadata.get("source_paths") == list(unit.source_files))
                and metadata["status"] == "PASS"
                and metadata["model"] == MODEL_NAME
                and metadata["dimension"] == DIMENSION
                and metadata["normalized"] is NORMALIZE_EMBEDDINGS
                and metadata["faiss_type"] == "IndexFlatIP"
                and type(count) is int and count > 0
                and count == metadata["pmid_count"] == metadata["usable_abstracts"]
                and metadata["self_retrieval"]["queries"] > 0
                and metadata["self_retrieval"]["top1"] == 1.0
                and files[paths[0]].size >= count * DIMENSION * 4
            )
        except (ValueError, KeyError, TypeError):
            valid = False
    if not valid:
        log(f"{unit.key}: remote artifacts have incomplete/incompatible metadata")
        return None
    return {"metadata": metadata, "revision": revision, "files": files}


def verify_remote_bytes(unit, remote):
    """Match file sizes and HF LFS SHA-256 or Git blob SHA-1, with bounded RAM."""
    output_dir = unit.output_dir
    for name in ARTIFACT_NAMES:
        path = output_dir / name
        entry = remote["files"][f"{unit.remote_path}/{name}"]
        size = path.stat().st_size
        if size != entry.size:
            raise RuntimeError(f"HF verification failed: size mismatch for {name}")
        if entry.lfs is not None:
            digest = hashlib.sha256()
            expected = entry.lfs.sha256
        else:
            digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
            expected = entry.blob_id
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"HF verification failed: hash mismatch for {name}")


def remote_receipt(unit, remote):
    return {
        "hf_path": unit.remote_path,
        "hf_revision": remote["revision"],
        "remote_verified_at": now(),
        "remote_artifacts": {
            name: {
                "size": entry.size,
                "hash_type": "sha256" if entry.lfs is not None else "git-sha1",
                "hash": entry.lfs.sha256 if entry.lfs is not None else entry.blob_id,
            }
            for name in ARTIFACT_NAMES
            for entry in (remote["files"][f"{unit.remote_path}/{name}"],)
        },
    }


def upload_and_verify(api, unit):
    output_dir = unit.output_dir
    remote_path = unit.remote_path
    log(f"Uploading to {HF_REPO}/{remote_path}")
    commit = api.upload_folder(
        repo_id=HF_REPO, repo_type="dataset", folder_path=str(output_dir),
        path_in_repo=remote_path,
        allow_patterns=list(ARTIFACT_NAMES),
        commit_message=f"Add 1024-D PubMed index {unit.key}",
    )
    remote = remote_completed_unit(api, unit, revision=commit.oid)
    if remote is None:
        raise RuntimeError("HF verification failed: missing or incomplete unit artifacts")
    verify_remote_bytes(unit, remote)
    log(f"{unit.key}: HF upload verification: PASS (content hashes; commit {commit.oid})")
    return remote_receipt(unit, remote)


def local_artifacts_complete(unit):
    """Check this unit's own local output files for a complete, passing
    computation (LOCAL_COMPUTE_COMPLETE), independent of the manifest's
    "status" field: a later publication failure overwrites that field, but
    must never be allowed to erase the evidence that computation already
    finished. Mirrors remote_completed_unit's trust model (recorded PASS
    metadata plus a structural reload), applied to the on-disk artifacts
    instead of the remote ones. Returns the validated metadata, or None if
    the local artifacts are absent, incomplete, or incompatible.
    """
    output_dir = unit.output_dir
    if not all((output_dir / name).is_file() for name in ARTIFACT_NAMES):
        return None
    try:
        with (output_dir / "metadata.json").open(encoding="utf-8") as handle:
            metadata = json.load(handle)
        valid = (
            metadata["chunk"] == unit.name
            and metadata.get("corpus_type", "general_corpus") == unit.kind
            and (unit.kind != "domains" or metadata.get("source_paths") == list(unit.source_files))
            and metadata["status"] == "PASS"
            and metadata["model"] == MODEL_NAME
            and metadata["dimension"] == DIMENSION
            and metadata["normalized"] is NORMALIZE_EMBEDDINGS
            and metadata["faiss_type"] == "IndexFlatIP"
            and type(metadata["vector_count"]) is int and metadata["vector_count"] > 0
            and metadata["vector_count"] == metadata["pmid_count"] == metadata["usable_abstracts"]
            and metadata["self_retrieval"]["queries"] > 0
            and metadata["self_retrieval"]["top1"] == 1.0
        )
        if not valid:
            return None
        with (output_dir / "pmid_map.json").open(encoding="utf-8") as handle:
            pmids = json.load(handle)
        if not isinstance(pmids, list) or len(pmids) != metadata["pmid_count"]:
            return None
        if metadata.get("index_receipt") is not None:
            if file_receipt(output_dir / "index.faiss") != metadata["index_receipt"]:
                return None
        faiss_module = load_faiss()
        index = faiss_module.read_index(str(output_dir / "index.faiss"))
        if index.d != DIMENSION or index.ntotal != metadata["vector_count"]:
            return None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None
    return metadata


def _publish_unit(api, unit, manifest, downloaded_source, embeddings=None, reload_index=None):
    """Upload already-validated local artifacts, verify them remotely, and
    persist UPLOADED only after that verification passes. Shared by both the
    fresh-computation path and the LOCAL_COMPUTE_COMPLETE resume shortcut, so
    a failed upload is retried identically either way.
    """
    chunk = unit.key
    receipt = upload_and_verify(api, unit)
    manifest["shards"][chunk]["status"] = "UPLOADED"
    manifest["shards"][chunk]["uploaded_at"] = now()
    manifest["shards"][chunk].update(receipt)
    manifest["shards"][chunk]["local_artifacts_verified"] = True
    save_manifest(manifest)
    # Release any mapped embeddings/FAISS index before deleting their worker files.
    del embeddings
    del reload_index
    cleanup_worker_files(unit, downloaded_source)
    log(f"{chunk}: FINAL STATUS UPLOADED")


def owned_source_dir(unit, state):
    """Only a recorded, uniquely allocated worker directory may be removed."""
    record = state.get("source_download")
    if not record:
        return None
    path = Path(record["work_dir"])
    root = OUTPUT_ROOT / "_downloaded_sources"
    if (path.is_symlink() or path.parent.resolve() != root.resolve()
            or not path.name.startswith(unit.worker_prefix)
            or record.get("unit") != unit.key):
        raise RuntimeError(f"Unsafe downloaded source ownership record: {path}")
    if path.exists():
        marker = path / "owner.json"
        if marker.is_symlink() or json.loads(marker.read_text()) != record:
            raise RuntimeError(f"Downloaded source ownership mismatch: {path}")
    return path


def obtain_source(api, unit, manifest):
    if unit.kind == "general_corpus":
        candidates = [SOURCE_ROOT / unit.name, SOURCE_ROOT / f"{unit.name}.jsonl.gz"]
    else:
        abstract_root = SOURCE_ROOT.parent
        candidates = [abstract_root / unit.name, abstract_root / "domains" / unit.name]
        if len(unit.source_files) == 1:
            candidates += [Path(str(path) + suffix) for path in candidates[:]
                           for suffix in (".jsonl.gz", ".jsonl")]
    for path in candidates:
        if path.exists():
            log(f"{unit.key}: using pre-existing source {path}; never eligible for cleanup")
            return (path,), None
    local_files = tuple(SOURCE_ROOT.parent / name for name in unit.source_files)
    if local_files and all(path.is_file() for path in local_files):
        log(f"{unit.key}: using {len(local_files)} pre-existing source files; never eligible for cleanup")
        return local_files, None

    state = manifest["shards"][unit.key]
    work_dir = owned_source_dir(unit, state)
    if work_dir is None or not work_dir.exists():
        if not unit.source_files:
            raise RuntimeError(f"No discovered source files for {unit.key}")
        revision = unit.source_revision or api.repo_info(HF_SOURCE_REPO, repo_type="dataset").sha
        root = OUTPUT_ROOT / "_downloaded_sources"
        root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=unit.worker_prefix, dir=root))
        record = {
            "work_dir": str(work_dir.resolve()), "unit": unit.key,
            "repo": HF_SOURCE_REPO, "revision": revision,
            "files": list(unit.source_files), "created_at": now(),
        }
        atomic_json(work_dir / "owner.json", record)
        state["source_download"] = record
        # Persist ownership before downloading; never infer ownership from existence.
        save_manifest(manifest)
    record = state["source_download"]
    if record["files"] != list(unit.source_files) or record["repo"] != HF_SOURCE_REPO:
        raise IncompatibleCheckpoint(
            f"{unit.key}: discovered source files changed since the worker download. "
            "Retaining local work; explicitly archive/reset this unit before rebuilding."
        )
    paths = tuple(work_dir / "download" / name for name in record["files"])
    ready = work_dir / "ready.json"
    if ready.exists():
        if (json.loads(ready.read_text()) != record
                or any(not path.is_file() or path.stat().st_size == 0 for path in paths)):
            raise RuntimeError(f"Invalid downloaded source completion marker: {ready}")
        log(f"{unit.key}: reusing completed worker download ({len(paths)} files)")
        return paths, work_dir

    for number, name in enumerate(record["files"], start=1):
        log(f"{unit.key}: downloading source file {number}/{len(paths)}: {name}")
        hf_hub_download(
            repo_id=record["repo"], repo_type="dataset", filename=name,
            revision=record["revision"], local_dir=work_dir / "download",
        )
    if any(not path.is_file() or path.stat().st_size == 0 for path in paths):
        raise RuntimeError(f"{unit.key}: incomplete source download")
    atomic_json(ready, record)
    return paths, work_dir


def source_records(path):
    """Stream JSONL; invalid records are counted, truncated gzip files fail closed."""
    if path.name.endswith((".jsonl.gz", ".jsonl")):
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                try:
                    yield json.loads(line), None
                except json.JSONDecodeError:
                    yield None, None
    else:
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError:
            yield None, None
            return
        if isinstance(data, list):
            for record in data:
                yield record, None
        else:
            yield data, path.stem


def load_unit_abstracts(unit, source_paths):
    # Preserve the exact old general-corpus directory ordering and fallback PMIDs.
    if (unit.kind == "general_corpus" and len(source_paths) == 1
            and source_paths[0].is_dir()):
        texts, pmids, count = load_abstracts(source_paths[0])
        return texts, pmids, {
            "source_files": count, "source_documents": count,
            "unique_pmid_count": len(set(pmids)),
            "skipped_invalid_records": count - len(pmids), "duplicate_pmids": 0,
        }

    files = []
    for path in source_paths:
        if path.is_dir():
            files.extend(p for p in path.rglob("*")
                         if p.is_file() and source_stem(p.name) is not None)
        else:
            files.append(path)
    records_by_pmid = {}
    documents = invalid = duplicates = 0
    for file in sorted(files):
        for data, fallback in source_records(file):
            documents += 1
            if not isinstance(data, dict):
                invalid += 1
                continue
            pmid = str(data.get("pmid") or fallback or "").strip()
            title = str(data.get("title") or "").strip()
            abstract = str(data.get("abstract") or "").strip()
            text = f"{title}\n\n{abstract}".strip()
            if not pmid or not text:
                invalid += 1
                continue
            if pmid in records_by_pmid:
                duplicates += 1
                continue
            # Deterministic first usable record wins across sorted source files.
            records_by_pmid[pmid] = text
            if documents % 100_000 == 0:
                log(f"{unit.key}: source documents={documents:,}; "
                    f"unique PMIDs={len(records_by_pmid):,}; "
                    f"invalid={invalid:,}; duplicates={duplicates:,}")
    # Sorting matches the PMID filenames produced by the existing downloader.
    pmids = sorted(records_by_pmid)
    texts = [records_by_pmid[pmid] for pmid in pmids]
    return texts, pmids, {
        "source_files": len(files), "source_documents": documents,
        "unique_pmid_count": len(pmids), "skipped_invalid_records": invalid,
        "duplicate_pmids": duplicates,
    }


def cleanup_worker_files(unit, downloaded_source=None):
    # Call ONLY after remote verification and successful UPLOADED persistence.
    # Embedding checkpoints and assembled vectors remain durable recovery assets,
    # even after publication. Never delete them during finalization cleanup.
    paths = []
    if downloaded_source is not None:
        paths.append(downloaded_source)
    paths.append(unit.output_dir)
    for path in paths:
        try:
            if path.exists():
                shutil.rmtree(path)
                log(f"{unit.key}: removed worker files {path}")
        except OSError as exc:
            log(f"{unit.key}: WARNING cleanup failed for {path}: {exc}")


def process_chunk(model, api, number, manifest):
    """Compatibility entry point for a single general shard."""
    name = chunk_name(number)
    unit = IndexingUnit("general_corpus", name, (f"general_corpus/{name}.jsonl.gz",))
    return process_unit(model, api, unit, manifest)


def process_unit(model, api, unit, manifest):
    log(f"{unit.key}: disk before unit={free_disk_gib(DATA_ROOT):.1f} GiB free")
    try:
        return _process_unit(model, api, unit, manifest)
    finally:
        log(f"{unit.key}: disk after unit={free_disk_gib(DATA_ROOT):.1f} GiB free")


def _process_unit(model, api, unit, manifest):
    chunk = unit.key
    output_dir = unit.output_dir

    state = (
        manifest["shards"]
        .get(
            chunk,
            {},
        )
    )

    remote = remote_completed_unit(api, unit)
    if remote is not None:
        receipt = remote_receipt(unit, remote)
        # Preserve local work unless it is this exact previously verified job,
        # or its saved artifacts can now be verified against the remote bytes.
        can_cleanup = (
            state.get("status") == "UPLOADED"
            and state.get("local_artifacts_verified") is True
            and bool(state.get("completed_at"))
            and state.get("completed_at") == remote["metadata"].get("completed_at")
            and state.get("hf_path") == unit.remote_path
            and state.get("remote_artifacts") == receipt["remote_artifacts"]
        )
        if not can_cleanup and all((output_dir / name).is_file() for name in ARTIFACT_NAMES):
            try:
                verify_remote_bytes(unit, remote)
            except (OSError, RuntimeError) as exc:
                log(f"{chunk}: remote complete; retaining different/unverified local work: {exc}")
            else:
                can_cleanup = True
        downloaded_source = owned_source_dir(unit, state) if can_cleanup else None
        manifest["shards"][chunk] = {
            **state, **remote["metadata"], **receipt,
            "status": "UPLOADED", "remote_complete_skip": True,
            "local_artifacts_verified": can_cleanup,
        }
        save_manifest(manifest)
        if can_cleanup:
            cleanup_worker_files(unit, downloaded_source)
        log(f"{chunk}: 1024-D destination already complete; skipping source and indexing")
        return

    local_metadata = local_artifacts_complete(unit)
    if local_metadata is not None:
        log(f"{chunk}: local artifacts already LOCAL_COMPUTE_COMPLETE; "
            f"skipping source/embedding/FAISS/self-test and retrying publication only")
        downloaded_source = owned_source_dir(unit, state)
        manifest["shards"][chunk] = {
            **state, **local_metadata,
            "status": "PASS", "local_artifacts_verified": False,
        }
        save_manifest(manifest)
        _publish_unit(api, unit, manifest, downloaded_source)
        return

    free_gib = free_disk_gib(
        DATA_ROOT
    )

    log(
        f"{chunk}: "
        f"free disk="
        f"{free_gib:.1f} GiB"
    )

    if free_gib < MIN_FREE_GIB:
        raise RuntimeError(
            f"Free disk below "
            f"{MIN_FREE_GIB} GiB"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest[
        "shards"
    ][chunk] = {
        **state,
        "status": "RUNNING",
        "local_artifacts_verified": False,
        "remote_complete_skip": False,
        "started_at": now(),
    }

    save_manifest(
        manifest
    )

    source_paths, downloaded_source = obtain_source(api, unit, manifest)

    load_start = (
        time.perf_counter()
    )

    texts, pmids, source_stats = load_unit_abstracts(unit, source_paths)
    source_count = source_stats["source_files"]

    load_seconds = (
        time.perf_counter()
        - load_start
    )

    usable = len(
        texts
    )

    log(
        f"{chunk}: "
        f"source={source_count:,}, "
        f"documents={source_stats['source_documents']:,}, "
        f"unique PMIDs={source_stats['unique_pmid_count']:,}, "
        f"invalid/skipped={source_stats['skipped_invalid_records']:,}, "
        f"duplicates={source_stats['duplicate_pmids']:,}, usable={usable:,}"
    )

    if usable == 0:
        raise RuntimeError(
            "No usable abstracts"
        )

    resumed = resume_completed_embeddings(model, chunk, texts, pmids)
    if resumed is None:
        torch.mps.empty_cache()
        log(f"{chunk}: embedding start")
        embeddings, embed_seconds = embed_with_checkpoints(model, chunk, texts, pmids)
    else:
        log(f"{chunk}: embedding complete; resuming finalization without encoding")
        embeddings, embed_seconds = resumed

    throughput = (
        usable
        / embed_seconds
    )

    log(
        f"{chunk}: "
        f"embedding complete "
        f"{embed_seconds / 3600:.3f} h "
        f"{throughput:.2f} abs/sec"
    )

    (
        mean_norm,
        min_norm,
        max_norm,
    ) = validate_vectors(
        embeddings
    )

    if embeddings.shape != (usable, DIMENSION) or len(pmids) != usable:
        raise ValueError("Vector/PMID/source count mismatch")
    settings = checkpoint_settings(model, chunk, texts, pmids)
    pmid_file = output_dir / "pmid_map.json"
    if pmid_file.exists():
        existing_pmids = json.loads(pmid_file.read_text())
        if existing_pmids != pmids:
            raise ValueError("PMID map count/order/source mismatch")
    else:
        atomic_json(pmid_file, pmids)

    index_file = output_dir / "index.faiss"
    metadata_file = output_dir / "metadata.json"
    finalization_file = output_dir / "finalization.json"
    finalization = {"settings": settings, "stage": "VECTORS_AND_PMIDS_READY"}
    if finalization_file.exists():
        finalization = json.loads(finalization_file.read_text())
        if finalization["settings"] != settings:
            raise IncompatibleCheckpoint("Finalization source/settings changed")

    def stage(name, **values):
        finalization.update(values)
        finalization["stage"] = name
        atomic_json(finalization_file, finalization)
        manifest["shards"][chunk]["finalization_stage"] = name
        save_manifest(manifest)
        log(f"{chunk}: finalization {name}")

    stage("VECTORS_AND_PMIDS_READY")
    faiss_module = load_faiss()
    # Bound native OpenMP search parallelism on macOS, where FAISS and MPS
    # coexist in this worker. This also avoids severe oversubscription.
    if sys.platform == "darwin":
        faiss_module.omp_set_num_threads(1)
    index = None
    receipt = finalization.get("index_receipt")
    if receipt and index_file.exists() and file_receipt(index_file) == receipt:
        stage("INDEX_RELOAD_STARTED")
        index = faiss_module.read_index(str(index_file))
        if index.d != DIMENSION or index.ntotal != usable:
            raise ValueError("Committed FAISS dimension/count mismatch")
        log(f"{chunk}: committed FAISS index reused")
    if index is None:
        stage("FAISS_BUILD_STARTED", index_receipt=None)
        index = faiss_module.IndexFlatIP(DIMENSION)
        for start in range(0, usable, CHECKPOINT_ROWS):
            index.add(embeddings[start:start + CHECKPOINT_ROWS])
        if index.ntotal != usable or index.d != DIMENSION:
            raise RuntimeError("FAISS/PMID count or dimension mismatch")
        stage("INDEX_WRITE_STARTED")
        atomic_faiss_index(faiss_module, index, index_file)
        stage("INDEX_WRITTEN", index_receipt=file_receipt(index_file))
        # Reload BEFORE recording PASS; a write/reload crash remains resumable.
        del index
        stage("INDEX_RELOAD_STARTED")
        index = faiss_module.read_index(str(index_file))
        if index.d != DIMENSION or index.ntotal != usable:
            raise ValueError("Reload FAISS dimension/count mismatch")

    log(f"{chunk}: final vector count={index.ntotal:,}; PMID map count={len(pmids):,}")
    stage("SELF_TEST_STARTED")
    self_test = self_retrieval_test(index, embeddings)
    log(f"{chunk}: self Top1={self_test['top1']:.2%} "
        f"Top5={self_test['top5']:.2%} Top10={self_test['top10']:.2%}")
    if self_test["top1"] != 1.0:
        raise RuntimeError("Self-retrieval Top-1 failed")
    stage("VALIDATED", self_retrieval=self_test)

    metadata = {
        "chunk": unit.name,
        "unit_key": unit.key,
        "corpus_type": unit.kind,
        "source_paths": list(unit.source_files),
        "source_revision": (
            manifest["shards"][chunk]["source_download"]["revision"]
            if downloaded_source is not None else unit.source_revision
        ),
        "source_origin": "worker_download" if downloaded_source is not None else "preexisting_local",
        "embedding_settings": settings,
        "index_receipt": finalization["index_receipt"],
        **source_stats,
        "usable_abstracts": usable,
        "model": MODEL_NAME,
        "dimension": DIMENSION,
        "device": DEVICE,
        "batch_size": BATCH_SIZE,
        "normalized": True,
        "vector_count": int(
            index.ntotal
        ),
        "pmid_count": len(
            pmids
        ),
        "load_seconds": load_seconds,
        "embedding_seconds": (
            embed_seconds
        ),
        "abstracts_per_second": (
            throughput
        ),
        "mean_vector_norm": (
            mean_norm
        ),
        "min_vector_norm": (
            min_norm
        ),
        "max_vector_norm": (
            max_norm
        ),
        "faiss_type": (
            "IndexFlatIP"
        ),
        "self_retrieval": (
            self_test
        ),
        "status": "PASS",
        "completed_at": now(),
    }

    atomic_json(metadata_file, metadata)
    del index

    manifest[
        "shards"
    ][chunk] = {
        **manifest["shards"][chunk],
        **metadata,
        "status": "PASS",
    }

    save_manifest(
        manifest
    )

    stage("UPLOAD_STARTED")
    _publish_unit(api, unit, manifest, downloaded_source, embeddings)

    del texts
    del pmids

    torch.mps.empty_cache()


def argument_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-root", type=Path, default=None,
        help=(
            "PubMed data directory; overrides OMNIBIOAI_PUBMED_ROOT "
            f"(default: {DEFAULT_DATA_ROOT})"
        ),
    )
    parser.add_argument(
        "--mode", choices=("general", "domains", "all"), default=None,
        help="Default: all; --domain alone selects domains; --start/--end alone select general",
    )
    parser.add_argument("--domain", help="Process only this exact discovered domain name")

    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="Optional inclusive lower bound on remotely discovered shards (including 000)",
    )

    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="Optional inclusive upper bound; default discovers all available shards",
    )

    parser.add_argument(
        "--include-004",
        action="store_true",
        help=(
            "Recompute chunk004 "
            "instead of skipping it"
        ),
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--dry-run", action="store_true",
        help="Discover selected units and check remote completion without starting workers",
    )
    operation.add_argument(
        "--status", action="store_true",
        help="Show local manifest and remote completion status without starting workers",
    )
    parser.add_argument("--worker-unit", help=argparse.SUPPRESS)
    return parser


def selected_mode(args, parser):
    if args.worker_unit:
        if args.worker_unit.startswith("domains/"):
            name = args.worker_unit.removeprefix("domains/")
            if not name:
                parser.error("invalid internal worker unit")
            return "domains", None, None, name
        match = re.fullmatch(r"_general_corpus_chunk(\d{3,})", args.worker_unit)
        if not match:
            parser.error("invalid internal worker unit")
        number = int(match.group(1))
        return "general", number, number, None

    mode = args.mode or (
        "domains" if args.domain else
        "general" if args.start is not None or args.end is not None else "all"
    )
    if args.domain and mode == "general":
        parser.error("--domain cannot be used with --mode general")
    if mode == "domains" and (args.start is not None or args.end is not None):
        parser.error("--start/--end apply only to general corpus shards")
    if args.start is not None and args.start < 0:
        parser.error("--start must be >= 0")
    if args.end is not None and args.end < 0:
        parser.error("--end must be >= 0")
    if args.start is not None and args.end is not None and args.end < args.start:
        parser.error("--end must be >= --start")
    return mode, args.start, args.end, args.domain


def eligible_units(units, include_004):
    selected = []
    for unit in units:
        if (unit.kind == "general_corpus" and unit.name == chunk_name(4)
                and not include_004):
            log("chunk004 skipped (existing benchmark PASS; use --include-004 to verify/migrate)")
            continue
        selected.append(unit)
    return selected


def report_unit_status(api, units, show_manifest=False):
    manifest = load_manifest() if show_manifest else {"shards": {}}
    complete = 0
    for number, unit in enumerate(units, start=1):
        remote = remote_completed_unit(api, unit)
        remote_status = "COMPLETE" if remote is not None else "PENDING"
        local_status = manifest["shards"].get(unit.key, {}).get("status", "UNKNOWN")
        suffix = f"; local={local_status}" if show_manifest else ""
        log(f"STATUS unit {number}/{len(units)}: {unit.key}; remote={remote_status}{suffix}")
        complete += remote is not None
    log(f"STATUS summary: remote complete={complete}/{len(units)}; "
        f"remaining={len(units) - complete}")


def worker_command(unit):
    return [
        sys.executable,
        "-X",
        "faulthandler",
        str(Path(__file__).resolve()),
        "--data-root",
        str(DATA_ROOT),
        "--worker-unit",
        unit.key,
    ]


def record_supervisor_result(unit, status, exit_code=None):
    manifest = load_manifest()
    state = manifest["shards"].setdefault(unit.key, {})
    state["supervisor_status"] = status
    state["supervisor_updated_at"] = now()
    if exit_code is not None:
        state["supervisor_exit_code"] = exit_code
    save_manifest(manifest)


def worker_completion_recorded(unit):
    state = load_manifest()["shards"].get(unit.key, {})
    return (
        state.get("status") == "UPLOADED"
        and state.get("hf_path") == unit.remote_path
        and set(state.get("remote_artifacts", {})) == set(ARTIFACT_NAMES)
    )


def supervise_units(units):
    total = len(units)
    for number, unit in enumerate(units, start=1):
        log(f"SUPERVISOR unit {number}/{total} starting: {unit.key}")
        started = time.perf_counter()
        child = subprocess.Popen(worker_command(unit))
        try:
            exit_code = child.wait()
        except KeyboardInterrupt:
            log(f"SUPERVISOR interrupted during unit {number}/{total}: {unit.key}")
            if child.poll() is None:
                try:
                    # A terminal Ctrl+C normally reaches both parent and child.
                    # Give the worker time to persist INTERRUPTED before sending
                    # a signal ourselves for programmatic/isolated interrupts.
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.send_signal(signal.SIGINT)
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                        child.wait()
            record_supervisor_result(unit, "INTERRUPTED", child.returncode)
            return 130
        if exit_code != 0:
            record_supervisor_result(unit, "FAILED", exit_code)
            log(f"SUPERVISOR unit {number}/{total} FAILED: {unit.key}; "
                f"exit={exit_code}; stopping migration")
            return exit_code
        if not worker_completion_recorded(unit):
            record_supervisor_result(unit, "FAILED", 1)
            log(f"SUPERVISOR unit {number}/{total} FAILED: {unit.key}; "
                "worker exited 0 without a complete UPLOADED receipt; stopping migration")
            return 1
        elapsed = time.perf_counter() - started
        record_supervisor_result(unit, "COMPLETE", 0)
        log(f"SUPERVISOR unit {number}/{total} complete: {unit.key}; "
            f"elapsed={elapsed / 3600:.2f} h")
    log(f"SUPERVISOR migration complete: {total}/{total} units")
    return 0


def run_worker(unit, api):
    manifest = load_manifest()
    chunk = unit.key
    try:
        torch_module, transformer_class = load_mps_runtime()
        if not torch_module.backends.mps.is_available():
            raise RuntimeError("MPS is unavailable")
        log("Loading embedding model")
        model = transformer_class(MODEL_NAME, device=DEVICE)
        model_dimension = model.get_sentence_embedding_dimension()
        if model_dimension != DIMENSION:
            raise RuntimeError(f"Model dimension {model_dimension} != {DIMENSION}")
        log(f"Model ready: device={model.device}, dimension={model_dimension}")

        # Preserve the macOS stability boundary: MPS must be initialized first.
        load_faiss()
        process_unit(model=model, api=api, unit=unit, manifest=manifest)
    except KeyboardInterrupt:
        log(f"{chunk}: INTERRUPTED")
        manifest["shards"].setdefault(chunk, {})["status"] = "INTERRUPTED"
        save_manifest(manifest)
        raise
    except Exception as exc:
        log(f"{chunk}: FAILED: {exc}")
        state = manifest["shards"].setdefault(chunk, {})
        state.update({"status": "FAILED", "error": str(exc), "failed_at": now()})
        save_manifest(manifest)
        raise
    log(f"WORKER unit complete: {unit.key}")


def main():
    parser = argument_parser()
    args = parser.parse_args()
    mode, start, end, domain = selected_mode(args, parser)

    configure_data_root(args.data_root)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    log(f"PubMed data root: {DATA_ROOT}")
    api = HfApi()
    units = discover_units(api, mode, start, end, domain)
    if args.worker_unit:
        units = [unit for unit in units if unit.key == args.worker_unit]
        if len(units) != 1:
            raise RuntimeError(f"Exact worker unit not found: {args.worker_unit}")
        run_worker(units[0], api)
        return

    units = eligible_units(units, args.include_004)
    if not units:
        log("No source units found within requested filters")
        return
    if args.dry_run or args.status:
        report_unit_status(api, units, show_manifest=args.status)
        return

    exit_code = supervise_units(units)
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
