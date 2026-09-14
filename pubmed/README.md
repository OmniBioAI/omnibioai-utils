# PubMed 1024-D indexing worker

Canonical script: `pubmed/reindex_1024_mac.py` in `omnibioai-utils`.
On this Mac the full path is:

```text
/Users/manishkumar/Desktop/machine/omnibioai-utils/pubmed/reindex_1024_mac.py
```

The code location and current working directory do not determine where PubMed
sources, checkpoints, indexes, logs, or the manifest live.

## Data root

The data-root precedence is:

1. `--data-root PATH`
2. `OMNIBIOAI_PUBMED_ROOT`
3. `/Users/manishkumar/omnibioai-data/PubMed` (the existing Mac default)

An absolute path works from any working directory. `~` is expanded; an explicit
relative path is resolved once against the invocation's working directory.
Selecting a data root does not move existing data. To resume existing work, use
its original data root.

All worker-owned runtime paths are beneath that root:

| Purpose | Relative path |
| --- | --- |
| Existing general source directories | `Abstracts/general_corpus/<chunk_name>/` |
| Existing domain source directories | `Abstracts/<domain_name>/` or `Abstracts/domains/<domain_name>/` |
| General generated artifacts | `Index/_reindex_1024_mac/<chunk_name>/` |
| Domain generated artifacts | `Index/_reindex_1024_mac/domains/<domain_name>/` |
| Embedding checkpoints | `Index/_reindex_1024_mac/_embedding_checkpoints/<unit_key>/` |
| Worker source downloads | `Index/_reindex_1024_mac/_downloaded_sources/<unique-worker-directory>/` |
| Manifest / audit | `reindex_1024_mac_manifest.json` |
| Log | `reindex_1024_mac.log` |

The worker also recognizes existing compressed general source files and complete
sets of domain source files under `Abstracts/`. Existing local sources are used
without transferring ownership to the worker. Hugging Face's model cache remains
managed by the installed library; small remote-metadata checks use temporary
system directories.

## Hugging Face repositories and index settings

- Source: [`omnibioai/pubmed-abstracts-36M`](https://huggingface.co/datasets/omnibioai/pubmed-abstracts-36M).
- Destination: [`omnibioai/pubmed-faiss-indexes`](https://huggingface.co/datasets/omnibioai/pubmed-faiss-indexes).
- Model: `mixedbread-ai/mxbai-embed-large-v1`.
- Embeddings: 1024 dimensions, float32, normalized, batch size 32.
- Device: Apple Silicon `mps`; the worker checks MPS availability at startup.
- Index: FAISS `IndexFlatIP`, including self-retrieval and saved-index reload checks.

Only these destination namespaces are checked and written:

```text
mxbai-1024/general_corpus/<chunk_name>/
mxbai-1024/domains/<domain_name>/
```

Each contains `index.faiss`, `pmid_map.json`, and `metadata.json`. Legacy 768-D
PubMedBERT and domain directories elsewhere in the repository are neither reused
nor modified. No remote deletion operation is performed.

## General corpus and domain discovery

General corpus files are discovered dynamically from
`general_corpus/_general_corpus_chunkNNN.jsonl.gz`. Chunk000 is included, missing
numbers are not invented, and there is no fixed maximum shard count. Each shard
is an independent indexing unit. `--start` and `--end` are optional inclusive
bounds on discovered general shards. Chunk004 is skipped by default; use
`--include-004` to make it eligible, subject to the remote-completion check.

Domains are discovered from root-level archives such as
`Cardiovascular.jsonl.gz`, domain directories, and the optional `domains/`
namespace. Supported split-file names include `NAME_chunkNNN`, `NAME_partNNN`,
`NAME_shardNNN`, and `NAME-00000-of-00002`. Supported source formats are JSONL,
gzip-compressed JSONL, and JSON records/arrays. All recognized files belonging to
a domain are combined into one logical index.

Domain PMID deduplication keeps the first usable record in sorted source-file
order and then sorts PMIDs for deterministic embedding order. The manifest and
logs record source document count, unique usable PMID count, skipped/invalid
records, duplicates, and final vector count. General shards and domains share
the same checkpointing, FAISS, verification, and cleanup pipeline.

## Checkpoints, recovery, and memory

Embedding checkpoints commit approximately every 10,000 abstracts. Each block
is validated for expected row count, dimension, float32 dtype, and finite values.
Checkpoint metadata identifies the model/settings and a fingerprint of the
ordered texts and PMIDs. Writes use a flushed, validated temporary file followed
by an atomic rename. Uncommitted `.tmp` files are ignored.

Restart with the same command and data root. Valid blocks are reused; missing or
corrupt blocks are recomputed, starting with the first incomplete block.
Incompatible settings or changed input content cause an explicit error rather
than silent reuse. Download ownership and completed downloads survive restarts;
partially downloaded source units are completed before embedding begins. A
changed source-file set for an unfinished domain is rejected for explicit review.

The assembled embedding matrix is a disposable memory-mapped file and is rebuilt
from blocks. Vector validation and FAISS insertion run in blocks, and the initial
FAISS index is released before its validation reload. The model, source texts,
and one FAISS index still require RAM. The existing 150 GiB minimum free-space
check remains. Logs report disk space before/after each unit, embedding block,
completed abstracts, percentage, throughput, and estimated embedding time left.
Run one worker process per data root.

## Remote completion, upload verification, and cleanup

Before loading or downloading a unit's source, the worker checks only its matching
1024-D destination. A completed unit requires all three nonempty artifacts,
compatible PASS metadata, matching positive counts, passing self-retrieval, and
an index size consistent with the declared vectors. This preflight check trusts
the recorded validation; it does not download the full remote index for retesting.

After a new upload, verification is pinned to the returned commit. Every local
artifact must match the remote size and content hash (LFS SHA-256 or Git blob
SHA-1). The worker then:

1. Atomically persists `UPLOADED` in the manifest, including metadata, commit,
   source provenance, and artifact hashes.
2. Deletes local embedding checkpoints and the disposable assembled matrix.
3. Deletes the source download directory only if it is recorded as worker-owned.
4. Deletes that unit's generated local index directory.

Indexing, validation, upload, verification, or manifest-write failures retain
local work. Pre-existing user-owned sources are never deleted. The manifest and
log remain as the local audit. Cleanup errors are logged; recovery only removes
leftovers when ownership and the verified artifact identity can be established.

## CLI examples

These examples use absolute script and interpreter paths and can be run from any
directory. The interpreter below is the existing Mac environment used for the
focused tests. Under pyenv, the bare `python3` command may select a different
environment after changing directories. You may substitute another activated
environment with the required dependencies. Indexing commands include upload and
cleanup after successful verification.

One general shard, including chunk000:

```bash
/Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 \
  /Users/manishkumar/Desktop/machine/omnibioai-utils/pubmed/reindex_1024_mac.py \
  --mode general --start 0 --end 0
```

One domain:

```bash
/Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 \
  /Users/manishkumar/Desktop/machine/omnibioai-utils/pubmed/reindex_1024_mac.py \
  --mode domains --domain Cardiovascular
```

Explicit data root, overriding the environment:

```bash
/Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 \
  /Users/manishkumar/Desktop/machine/omnibioai-utils/pubmed/reindex_1024_mac.py \
  --data-root /Users/manishkumar/omnibioai-data/PubMed \
  --mode general --start 1 --end 1
```

Environment-selected data root:

```bash
OMNIBIOAI_PUBMED_ROOT=/Volumes/PubMedData/PubMed \
  /Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 \
  /Users/manishkumar/Desktop/machine/omnibioai-utils/pubmed/reindex_1024_mac.py \
  --domain Cardiovascular
```

Production selectors are `--mode general`, `--mode domains`, and `--mode all`.
With no filters, the default is `all`. `--domain NAME` alone selects domains;
`--start`/`--end` alone select general corpus for backward compatibility. With an
explicit `--mode all`, range bounds limit only general shards, and `--domain`
limits only domains. Domain names are exact and case-sensitive.

To exercise recovery, interrupt after a `checkpoint committed` log and rerun the
same one-unit command. Look for `checkpoint reused`. A remotely completed unit
will instead skip source download and indexing.

## Mac environment and focused tests

Use the existing Apple Silicon Python environment with `torch` (MPS support),
`sentence-transformers`, `numpy`, `faiss-cpu`, and `huggingface_hub`. FAISS operates
on the CPU; MPS is used for model inference. Authenticate Hugging Face with an
account able to read the source and write the destination dataset. Keep the Mac
on power and awake during long runs. This worker does not fall back to CUDA or CPU
embedding when MPS is unavailable.

The focused tests need NumPy and FAISS; model inference and Hugging Face transfers
are simulated. They exercise checkpoint recovery, real FAISS save/reload and
self-retrieval, discovery, domain deduplication, namespace isolation, cleanup,
and data-root selection from another working directory.

Run from any directory:

```bash
/Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 -m unittest discover \
  -s /Users/manishkumar/Desktop/machine/omnibioai-utils/tests/pubmed -v
```

Or use the repository's pytest installation, overriding its repository-wide
coverage policy for this focused suite:

```bash
/Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 -m pytest -o addopts='' \
  /Users/manishkumar/Desktop/machine/omnibioai-utils/tests/pubmed
```

Show CLI help without starting indexing:

```bash
/Users/manishkumar/.pyenv/versions/3.12.0/bin/python3 \
  /Users/manishkumar/Desktop/machine/omnibioai-utils/pubmed/reindex_1024_mac.py --help
```
