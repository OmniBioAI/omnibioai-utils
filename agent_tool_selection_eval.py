#!/usr/bin/env python3
"""
agent_tool_selection_eval.py

Measures how reliably a local Ollama model (default: llama3.1:70b) can pick
the correct tool and fill valid arguments when given a semantically-narrowed
shortlist of candidates from a large tool corpus (TES tools, plugins, or
workflows).

This answers the core open question for OmniAssistant v2: is tool-selection
reliability at your real scale a solved problem, or the bottleneck.

Confirmed live from the TES API (GET /api/tools): 11,577 total tools, of
which ~9,061 (78%) are tagged auto_generated / unverified_command /
biocontainers -- bulk-generated from the BioContainers registry and never
actually run. The other ~2,400 (1,229 slurm + 687 http + misc) are the
vetted core. Use --verified-only for a first eval pass to separate "is the
model bad at this" from "is the corpus itself unreliable."

USAGE
-----
    # Preferred: load live from a running TES instance (ground truth)
    python3 agent_tool_selection_eval.py \\
        --tes-api-url http://localhost:8081 \\
        --verified-only \\
        --backend slurm --backend http \\
        --top-k 8 \\
        --out results.json

    # Or from category files on disk
    python3 agent_tool_selection_eval.py \\
        --tools-dir /path/to/omnibioai-tes/configs/tools \\
        --verified-only

If none of --tes-api-url / --tools-dir / --tools-yaml is given, a small
built-in mock corpus is used so you can smoke-test the harness itself first.

REQUIRES
--------
    pip install requests pyyaml numpy --break-system-packages
    ollama pull llama3.1:70b
    ollama pull mxbai-embed-large

WHAT IT MEASURES
-----------------
For each labeled test prompt:
  1. Embed the prompt, cosine-search the tool corpus -> top-K shortlist
     (did the correct tool even make the shortlist? "recall@K")
  2. Send the shortlist to the model as Ollama tool-calling `tools=[...]`
  3. Parse the model's tool_calls -> did it pick the right tool_id?
  4. Validate required args are present and non-empty ("arg completeness")
  5. Track: malformed-JSON count, retry count, latency per call

Outputs a per-item breakdown + aggregate accuracy so you can decide whether
to invest in the mid-execution reasoning loop, or whether tool selection
itself needs more work first (bigger/different model, better descriptions,
narrower categories, etc).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

try:
    import yaml
except ImportError:
    yaml = None

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy required. pip install numpy --break-system-packages", file=sys.stderr)
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────
# 1. Test set — edit/extend this to match your real corpus and real user
#    prompts. Ground truth (expected_tool_id, required_args) is what makes
#    this an eval and not just a demo.
# ──────────────────────────────────────────────────────────────────────────

TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "qc_1",
        "prompt": "Run quality control on this FASTQ file: /data/sample_R1.fastq.gz",
        "category": "qc",
        "expected_tool_id": "fastqc",
        "required_args": ["input_file"],
    },
    {
        "id": "qc_2",
        "prompt": "Aggregate the QC reports from multiple tools into one summary",
        "category": "qc",
        "expected_tool_id": "multiqc",
        "required_args": ["input_dir"],
    },
    {
        "id": "align_1",
        "prompt": "Align these paired-end RNA-seq reads to the human genome using STAR",
        "category": "alignment",
        "expected_tool_id": "star_align",
        "required_args": [],
    },
    {
        "id": "align_2",
        "prompt": "I have Oxford Nanopore long reads, align them to the reference",
        "category": "alignment",
        "expected_tool_id": "minimap2_align",
        "required_args": [],
    },
    {
        "id": "variant_1",
        "prompt": "Call germline variants from this sorted BAM file against hg38, sample name s1",
        "category": "variant_calling",
        "expected_tool_id": "gatk_haplotypecaller",
        "required_args": ["input_bam", "reference", "sample_name"],
    },
    {
        "id": "variant_2",
        "prompt": "Run somatic variant calling on this tumor/normal pair",
        "category": "variant_calling",
        "expected_tool_id": "mutect2",
        "required_args": [],
    },
    {
        "id": "de_1",
        "prompt": "Find differentially expressed genes from this counts matrix, condition column is 'condition'",
        "category": "differential_expression",
        "expected_tool_id": "deseq2_analysis",
        "required_args": ["counts_file", "metadata_file", "condition_col"],
    },
    {
        "id": "lit_1",
        "prompt": "Search PubMed for recent papers on BRCA1 and breast cancer, top 5 results",
        "category": "literature",
        "expected_tool_id": "pubmed_search",
        "required_args": ["query"],
    },
    {
        "id": "lit_2",
        "prompt": "Look up the UniProt entry for accession P38398",
        "category": "literature",
        "expected_tool_id": "uniprot_lookup",
        "required_args": ["accession"],
    },
    {
        "id": "lit_3",
        "prompt": "What is the official HGNC gene symbol for TP53?",
        "category": "literature",
        "expected_tool_id": "hgnc_symbol",
        "required_args": ["symbol"],
    },
    {
        "id": "path_1",
        "prompt": "What pathways is BRCA1 involved in according to Reactome?",
        "category": "pathway",
        "expected_tool_id": "reactome_pathways_for_gene",
        "required_args": [],
    },
    {
        "id": "sc_1",
        "prompt": "Run QC and clustering on this 10x Genomics single-cell dataset",
        "category": "single_cell",
        "expected_tool_id": "seurat_cluster",
        "required_args": [],
    },
    {
        "id": "sc_2",
        "prompt": "Detect doublets in this scRNA-seq h5ad file",
        "category": "single_cell",
        "expected_tool_id": "scrublet_doublets",
        "required_args": [],
    },
    {
        "id": "meta_1",
        "prompt": "Classify these metagenomic reads taxonomically",
        "category": "metagenomics",
        "expected_tool_id": "kraken2_classify",
        "required_args": [],
    },
    {
        "id": "epi_1",
        "prompt": "Call ChIP-seq peaks for H3K27ac from this BAM file",
        "category": "epigenomics",
        "expected_tool_id": "macs2_callpeak",
        "required_args": [],
    },
    {
        "id": "ml_1",
        "prompt": "Train an XGBoost model to predict variant pathogenicity from this feature table",
        "category": "ml",
        "expected_tool_id": "xgboost_train",
        "required_args": [],
    },
    {
        "id": "popgen_1",
        "prompt": "Run a GWAS association test on this genotype dataset with PLINK",
        "category": "population_genetics",
        "expected_tool_id": "plink_gwas",
        "required_args": [],
    },
    {
        "id": "struct_1",
        "prompt": "Predict the 3D structure of this protein sequence using AlphaFold2",
        "category": "structural_biology",
        "expected_tool_id": "alphafold2_predict",
        "required_args": [],
    },
    {
        "id": "assembly_1",
        "prompt": "Assemble this bacterial genome from these paired FASTQ reads",
        "category": "assembly",
        "expected_tool_id": "spades_assemble",
        "required_args": [],
    },
    {
        "id": "ambiguous_1",
        "prompt": "Clean up my RNA-seq reads before alignment",
        "category": "ambiguous",
        "expected_tool_id": "trimmomatic",
        "required_args": [],
        "note": "Deliberately ambiguous - trimmomatic vs fastqc are both plausible reads",
    },
]


# ──────────────────────────────────────────────────────────────────────────
# 2. Mock corpus (used only if --tools-yaml not given, for smoke-testing)
# ──────────────────────────────────────────────────────────────────────────

MOCK_TOOLS = [
    {"tool_id": "fastqc", "display_name": "FastQC", "description": "Quality control checks on raw FASTQ sequencing reads",
     "inputs": [{"name": "input_file", "type": "string", "required": True}]},
    {"tool_id": "multiqc", "display_name": "MultiQC", "description": "Aggregate QC reports from multiple bioinformatics tools into one report",
     "inputs": [{"name": "input_dir", "type": "string", "required": True}]},
    {"tool_id": "trimmomatic", "display_name": "Trimmomatic", "description": "Trim and filter low-quality bases and adapters from FASTQ reads",
     "inputs": [{"name": "input_file", "type": "string", "required": True}]},
    {"tool_id": "star_align", "display_name": "STAR Aligner", "description": "Align RNA-seq FASTQ reads to a reference genome, splice-aware",
     "inputs": [{"name": "fastq_r1", "type": "string", "required": True}, {"name": "reference", "type": "string", "required": True}]},
    {"tool_id": "minimap2_align", "display_name": "Minimap2", "description": "Align long reads (Nanopore/PacBio) to a reference genome",
     "inputs": [{"name": "fastq", "type": "string", "required": True}, {"name": "reference", "type": "string", "required": True}]},
    {"tool_id": "gatk_haplotypecaller", "display_name": "GATK HaplotypeCaller", "description": "Call germline SNPs and indels from a sorted BAM file",
     "inputs": [{"name": "input_bam", "type": "string", "required": True}, {"name": "reference", "type": "string", "required": True}, {"name": "sample_name", "type": "string", "required": True}]},
    {"tool_id": "mutect2", "display_name": "Mutect2", "description": "Call somatic variants from tumor/normal BAM pairs",
     "inputs": [{"name": "tumor_bam", "type": "string", "required": True}, {"name": "normal_bam", "type": "string", "required": True}]},
    {"tool_id": "deseq2_analysis", "display_name": "DESeq2", "description": "Differential gene expression analysis from RNA-seq count data",
     "inputs": [{"name": "counts_file", "type": "string", "required": True}, {"name": "metadata_file", "type": "string", "required": True}, {"name": "condition_col", "type": "string", "required": True}]},
    {"tool_id": "pubmed_search", "display_name": "PubMed Search", "description": "Search PubMed literature by keyword query",
     "inputs": [{"name": "query", "type": "string", "required": True}, {"name": "max_results", "type": "integer", "required": False}]},
    {"tool_id": "uniprot_lookup", "display_name": "UniProt Lookup", "description": "Fetch protein information by UniProt accession ID",
     "inputs": [{"name": "accession", "type": "string", "required": True}]},
    {"tool_id": "hgnc_symbol", "display_name": "HGNC Symbol Lookup", "description": "Resolve the official gene symbol from HGNC by input symbol or alias",
     "inputs": [{"name": "symbol", "type": "string", "required": True}]},
    {"tool_id": "reactome_pathways_for_gene", "display_name": "Reactome Gene Pathways", "description": "Find Reactome biological pathways containing a given gene",
     "inputs": [{"name": "gene", "type": "string", "required": True}]},
    {"tool_id": "seurat_cluster", "display_name": "Seurat Clustering", "description": "QC, normalize, and cluster single-cell RNA-seq data from 10x Genomics",
     "inputs": [{"name": "input_path", "type": "string", "required": True}]},
    {"tool_id": "scrublet_doublets", "display_name": "Scrublet", "description": "Detect doublets in single-cell RNA-seq h5ad data",
     "inputs": [{"name": "h5ad_path", "type": "string", "required": True}]},
    {"tool_id": "kraken2_classify", "display_name": "Kraken2", "description": "Taxonomic classification of metagenomic sequencing reads",
     "inputs": [{"name": "input_file", "type": "string", "required": True}]},
    {"tool_id": "macs2_callpeak", "display_name": "MACS2", "description": "Call ChIP-seq or ATAC-seq peaks from aligned BAM files",
     "inputs": [{"name": "input_bam", "type": "string", "required": True}]},
    {"tool_id": "xgboost_train", "display_name": "XGBoost Trainer", "description": "Train a gradient-boosted tree model on a tabular feature dataset",
     "inputs": [{"name": "features_file", "type": "string", "required": True}]},
    {"tool_id": "plink_gwas", "display_name": "PLINK GWAS", "description": "Run genome-wide association study tests on genotype data",
     "inputs": [{"name": "genotype_file", "type": "string", "required": True}]},
    {"tool_id": "alphafold2_predict", "display_name": "AlphaFold2", "description": "Predict 3D protein structure from an amino acid sequence",
     "inputs": [{"name": "sequence", "type": "string", "required": True}]},
    {"tool_id": "spades_assemble", "display_name": "SPAdes", "description": "De novo genome assembly from paired-end sequencing reads",
     "inputs": [{"name": "fastq_r1", "type": "string", "required": True}, {"name": "fastq_r2", "type": "string", "required": True}]},
]


# ──────────────────────────────────────────────────────────────────────────
# 3. Corpus loading
# ──────────────────────────────────────────────────────────────────────────

def load_corpus_from_api(tes_api_url: str, exclude_tags: list[str]) -> list[dict[str, Any]]:
    """
    Loads the live tool corpus from GET {tes_api_url}/api/tools — this is
    ground truth (matches what TES will actually route to), unlike the yaml
    files which can drift if `make build` wasn't re-run.

    Tags each tool with _backend (from its 'slurm'/'http'/etc tag) and
    _verified (False if it carries any of exclude_tags, e.g. the ~9,061
    auto_generated/unverified_command/biocontainers x86_64 tools that were
    bulk-generated from BioContainers and never actually run).
    """
    r = requests.get(f"{tes_api_url}/api/tools", timeout=30)
    r.raise_for_status()
    tools = r.json()
    for t in tools:
        tags = set(t.get("tags") or [])
        t["_backend"] = next((b for b in ("slurm", "http", "aws_batch", "gcp_batch", "azure_batch", "kubernetes", "k8s") if b in tags), "unknown")
        t["_verified"] = not bool(tags & set(exclude_tags))
        t["_source_file"] = t.get("_backend", "unknown")
    verified_n = sum(t["_verified"] for t in tools)
    print(f"[corpus] Loaded {len(tools)} tools live from {tes_api_url}/api/tools")
    print(f"[corpus]   verified (no {exclude_tags} tags): {verified_n}")
    print(f"[corpus]   unverified (auto-generated/untested): {len(tools) - verified_n}")
    return tools


def load_corpus(
    tools_yaml: Optional[str], tools_dir: Optional[str], backend_filter: Optional[list[str]],
    tes_api_url: Optional[str], exclude_tags: list[str], verified_only: bool,
) -> list[dict[str, Any]]:
    """
    Loads from (in priority order):
      --tes-api-url  GET /api/tools    (preferred: live ground truth)
      --tools-dir    configs/tools/    (29 category files + x86_64/ subdir + kubernetes file)
      --tools-yaml   configs/tools.example.yaml  (auto-generated merge, may be stale)
    """
    if tes_api_url:
        all_tools = load_corpus_from_api(tes_api_url, exclude_tags)
    elif tools_dir:
        dir_path = Path(tools_dir)
        if not dir_path.exists():
            print(f"ERROR: {tools_dir} not found", file=sys.stderr)
            sys.exit(1)
        if yaml is None:
            print("ERROR: pyyaml required. pip install pyyaml --break-system-packages", file=sys.stderr)
            sys.exit(1)
        # Category files directly under tools/, PLUS the x86_64/ subdirectory
        # (9,140 auto-generated remote tools) and any kubernetes file.
        yaml_files = sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("x86_64/*.yaml"))
        all_tools = []
        for yf in yaml_files:
            data = yaml.safe_load(yf.read_text()) or {}
            tools = data.get("tools") or []
            for t in tools:
                t["_source_file"] = yf.stem if yf.parent.name != "x86_64" else f"x86_64/{yf.stem}"
                t["_backend"] = _infer_backend(t)
                tags = set(t.get("tags") or [])
                t["_verified"] = not bool(tags & set(exclude_tags))
            all_tools.extend(tools)
        print(f"[corpus] Loaded {len(all_tools)} tools from {len(yaml_files)} category files in {tools_dir}")
    elif tools_yaml:
        if yaml is None:
            print("ERROR: pyyaml required. pip install pyyaml --break-system-packages", file=sys.stderr)
            sys.exit(1)
        path = Path(tools_yaml)
        if not path.exists():
            print(f"ERROR: {tools_yaml} not found", file=sys.stderr)
            sys.exit(1)
        data = yaml.safe_load(path.read_text())
        all_tools = data.get("tools") or []
        for t in all_tools:
            t.setdefault("_source_file", "unknown")
            t["_backend"] = _infer_backend(t)
            tags = set(t.get("tags") or [])
            t["_verified"] = not bool(tags & set(exclude_tags))
        print(f"[corpus] Loaded {len(all_tools)} tools from {tools_yaml} (auto-generated merge — "
              f"run `make build` first if you've edited configs/tools/*.yaml recently)")
    else:
        print(f"[corpus] No source given, using {len(MOCK_TOOLS)}-tool mock corpus for smoke-testing.")
        return MOCK_TOOLS

    if verified_only:
        before = len(all_tools)
        all_tools = [t for t in all_tools if t.get("_verified", True)]
        print(f"[corpus] --verified-only: {before} -> {len(all_tools)} tools "
              f"(excluded tools tagged {exclude_tags})")

    if backend_filter:
        before = len(all_tools)
        all_tools = [t for t in all_tools if t.get("_backend") in backend_filter]
        print(f"[corpus] Filtered to backends {backend_filter}: {before} -> {len(all_tools)} tools")

    from collections import Counter
    counts = Counter(t.get("_source_file", "unknown") for t in all_tools)
    print("[corpus] Per-category counts:")
    for cat, n in sorted(counts.items()):
        print(f"    {cat:32s} {n}")

    return all_tools


def _infer_backend(tool: dict[str, Any]) -> str:
    if "slurm" in tool:
        return "slurm"
    if "http" in tool:
        return "http"
    if "aws_batch" in tool or "aws" in tool:
        return "aws_batch"
    if "gcp_batch" in tool or "gcp" in tool:
        return "gcp_batch"
    if "azure_batch" in tool or "azure" in tool:
        return "azure_batch"
    if "kubernetes" in tool or "k8s" in tool:
        return "kubernetes"
    return "unknown"


def tool_to_text(tool: dict[str, Any]) -> str:
    """What gets embedded for semantic search - keep this in sync with your
    real search_capabilities implementation so eval results transfer."""
    parts = [
        tool.get("tool_id", ""),
        tool.get("display_name", ""),
        tool.get("description", ""),
    ]
    return " - ".join(p for p in parts if p)


def tool_required_args(tool: dict[str, Any]) -> list[str]:
    flat = [i["name"] for i in (tool.get("inputs") or []) if i.get("required")]
    if flat:
        return flat
    # Fallback: inputs_schema.required (the JSON-Schema style used in the real TES configs)
    schema = tool.get("inputs_schema") or {}
    return list(schema.get("required") or [])


# ──────────────────────────────────────────────────────────────────────────
# 4. Ollama calls
# ──────────────────────────────────────────────────────────────────────────

def ollama_embed(base_url: str, model: str, text: str) -> np.ndarray:
    r = requests.post(f"{base_url}/api/embeddings", json={"model": model, "prompt": text}, timeout=60)
    r.raise_for_status()
    return np.array(r.json()["embedding"], dtype=np.float32)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def build_ollama_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a TES tool def into Ollama-compatible tool-calling schema.
    Supports both the flat `inputs: [...]` list and the JSON-Schema style
    `inputs_schema: {properties, required}` used in the real configs/tools/*.yaml."""
    props = {}
    required = []
    if tool.get("inputs"):
        for inp in tool["inputs"]:
            props[inp["name"]] = {
                "type": {"string": "string", "integer": "integer", "boolean": "boolean"}.get(inp.get("type", "string"), "string"),
                "description": inp.get("description", ""),
            }
            if inp.get("required"):
                required.append(inp["name"])
    elif tool.get("inputs_schema"):
        schema = tool["inputs_schema"]
        props = schema.get("properties") or {}
        required = list(schema.get("required") or [])
    return {
        "type": "function",
        "function": {
            "name": tool["tool_id"],
            "description": tool.get("description", tool.get("display_name", "")),
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def call_ollama_chat_with_tools(
    base_url: str, model: str, prompt: str, shortlist: list[dict[str, Any]]
) -> tuple[Optional[dict], float, str]:
    """Returns (parsed_tool_call_or_none, latency_seconds, raw_response_text)."""
    tool_schemas = [build_ollama_tool_schema(t) for t in shortlist]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "You are a bioinformatics tool-dispatch agent. Given the user's request, "
                "call exactly one of the provided tools with the correct arguments. "
                "Only use information from the request - do not invent file paths or values "
                "not mentioned or clearly implied by the user."
            )},
            {"role": "user", "content": prompt},
        ],
        "tools": tool_schemas,
        "stream": False,
    }
    t0 = time.time()
    try:
        r = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return None, time.time() - t0, f"REQUEST_ERROR: {e}"
    latency = time.time() - t0
    msg = data.get("message", {})
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return None, latency, json.dumps(msg)[:500]
    call = tool_calls[0]
    fn = call.get("function", {})
    name = fn.get("name")
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return {"tool_id": name, "args": None, "malformed": True}, latency, json.dumps(msg)[:500]
    return {"tool_id": name, "args": args or {}, "malformed": False}, latency, json.dumps(msg)[:500]


# ──────────────────────────────────────────────────────────────────────────
# 5. Eval loop
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ItemResult:
    id: str
    category: str
    prompt: str
    expected_tool_id: str
    shortlist_ids: list[str] = field(default_factory=list)
    in_shortlist: bool = False
    picked_tool_id: Optional[str] = None
    correct_tool: bool = False
    malformed_json: bool = False
    args_returned: dict = field(default_factory=dict)
    missing_required_args: list[str] = field(default_factory=list)
    args_complete: bool = False
    latency_seconds: float = 0.0
    raw_response: str = ""


def run_eval(
    base_url: str, model: str, embed_model: str, top_k: int, corpus: list[dict[str, Any]]
) -> list[ItemResult]:
    print(f"\n[embed] Embedding {len(corpus)} tools with {embed_model} ...")
    corpus_embeddings = []
    for i, tool in enumerate(corpus):
        emb = ollama_embed(base_url, embed_model, tool_to_text(tool))
        corpus_embeddings.append(emb)
        if (i + 1) % 25 == 0 or i == len(corpus) - 1:
            print(f"  embedded {i + 1}/{len(corpus)}")
    corpus_embeddings = np.stack(corpus_embeddings)

    results: list[ItemResult] = []
    print(f"\n[eval] Running {len(TEST_CASES)} test cases against {model} (top_k={top_k}) ...\n")

    for case in TEST_CASES:
        result = ItemResult(
            id=case["id"], category=case["category"], prompt=case["prompt"],
            expected_tool_id=case["expected_tool_id"],
        )

        # Step 1: semantic shortlist (recall@K check)
        q_emb = ollama_embed(base_url, embed_model, case["prompt"])
        sims = [cosine_sim(q_emb, e) for e in corpus_embeddings]
        ranked = sorted(zip(corpus, sims), key=lambda x: x[1], reverse=True)[:top_k]
        shortlist = [t for t, _ in ranked]
        result.shortlist_ids = [t["tool_id"] for t in shortlist]
        result.in_shortlist = case["expected_tool_id"] in result.shortlist_ids

        # Step 2: model picks from shortlist
        call, latency, raw = call_ollama_chat_with_tools(base_url, model, case["prompt"], shortlist)
        result.latency_seconds = latency
        result.raw_response = raw

        if call is None:
            result.malformed_json = True
        else:
            result.picked_tool_id = call["tool_id"]
            result.malformed_json = call.get("malformed", False)
            result.correct_tool = (call["tool_id"] == case["expected_tool_id"])
            result.args_returned = call.get("args") or {}
            expected_tool_def = next((t for t in corpus if t["tool_id"] == case["expected_tool_id"]), None)
            if expected_tool_def and result.correct_tool:
                required = tool_required_args(expected_tool_def)
                missing = [a for a in required if not result.args_returned.get(a)]
                result.missing_required_args = missing
                result.args_complete = len(missing) == 0

        status = "OK " if result.correct_tool else "MISS"
        shortlist_flag = "" if result.in_shortlist else " [NOT IN SHORTLIST]"
        print(f"  [{status}] {case['id']:16s} expected={case['expected_tool_id']:28s} "
              f"got={result.picked_tool_id or '(none)':28s} ({latency:.1f}s){shortlist_flag}")

        results.append(result)

    return results


def print_summary(results: list[ItemResult]) -> None:
    n = len(results)
    recall_at_k = sum(r.in_shortlist for r in results) / n
    correct = sum(r.correct_tool for r in results) / n
    malformed = sum(r.malformed_json for r in results) / n
    args_complete = [r for r in results if r.correct_tool]
    args_ok_rate = (sum(r.args_complete for r in args_complete) / len(args_complete)) if args_complete else 0.0
    avg_latency = sum(r.latency_seconds for r in results) / n

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Test cases:                  {n}")
    print(f"  Recall@K (correct tool in shortlist):  {recall_at_k:.0%}")
    print(f"  Tool-selection accuracy:               {correct:.0%}")
    print(f"  Malformed JSON rate:                   {malformed:.0%}")
    print(f"  Required-args completeness (when correct tool picked): {args_ok_rate:.0%}")
    print(f"  Avg latency per call:                  {avg_latency:.1f}s")

    print("\n  By category:")
    cats = sorted(set(r.category for r in results))
    for cat in cats:
        sub = [r for r in results if r.category == cat]
        acc = sum(r.correct_tool for r in sub) / len(sub)
        print(f"    {cat:24s} {acc:.0%}  ({len(sub)} cases)")

    print("\n  Misses (expected -> got):")
    for r in results:
        if not r.correct_tool:
            reason = "not in shortlist" if not r.in_shortlist else "in shortlist but wrong pick"
            print(f"    {r.id:16s} {r.expected_tool_id} -> {r.picked_tool_id or '(none)'}  ({reason})")
    print("=" * 70)

    print("\nHOW TO READ THIS:")
    print("  - Recall@K low  -> your embedding/search step needs work (bigger K, better")
    print("    tool descriptions, different embed model) before the LLM ever sees the")
    print("    right option.")
    print("  - Recall@K high but accuracy low -> the LLM itself is the bottleneck: try a")
    print("    different/larger model, tighten the system prompt, or add few-shot examples.")
    print("  - Malformed JSON rate > ~10% -> budget a validator/repair pass")
    print("    (e.g. qwen2.5-coder:32b) before dispatch, as planned.")
    print("  - Args completeness low -> even correct tool picks need a required-args")
    print("    checker with a re-prompt loop before calling TesClient for real.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tools-yaml", default=None,
                     help="Path to the auto-generated configs/tools.example.yaml (may be stale)")
    ap.add_argument("--tools-dir", default=None,
                     help="Path to configs/tools/ directory (category files + x86_64/ subdir)")
    ap.add_argument("--tes-api-url", default=None,
                     help="Load live from a running TES instance, e.g. http://localhost:8081 "
                          "(preferred: matches what TES will actually route to, right now)")
    ap.add_argument("--backend", action="append", default=None,
                     choices=["slurm", "http", "aws_batch", "gcp_batch", "azure_batch", "kubernetes", "k8s"],
                     help="Restrict corpus to specific backend(s). Repeat flag for multiple.")
    ap.add_argument("--exclude-tag", action="append",
                     default=["auto_generated", "unverified_command", "unverified"],
                     help="Tags marking a tool as untested (default: auto_generated, "
                          "unverified_command, unverified — the ~9,061 bulk-generated "
                          "BioContainers x86_64 tools). Pass --exclude-tag with no other "
                          "value to disable filtering entirely.")
    ap.add_argument("--verified-only", action="store_true",
                     help="Drop any tool carrying an --exclude-tag before building the "
                          "shortlist index. Strongly recommended for a first eval run — "
                          "measure reasoning quality against the ~2,400 vetted tools before "
                          "mixing in 9,000+ never-run auto-generated ones.")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--model", default="llama3.1:70b")
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--out", default=None, help="Optional path to write full JSON results")
    args = ap.parse_args()

    corpus = load_corpus(
        args.tools_yaml, args.tools_dir, args.backend,
        args.tes_api_url, args.exclude_tag, args.verified_only,
    )
    results = run_eval(args.ollama_url, args.model, args.embed_model, args.top_k, corpus)
    print_summary(results)

    if args.out:
        out_data = [r.__dict__ for r in results]
        Path(args.out).write_text(json.dumps(out_data, indent=2))
        print(f"\nFull results written to {args.out}")


if __name__ == "__main__":
    main()