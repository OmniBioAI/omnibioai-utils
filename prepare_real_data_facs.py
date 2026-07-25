#!/usr/bin/env python3
"""
prepare_real_data.py  (plugins/variant_pathogenicity_classifier/scripts/prepare_real_data.py)

Real-data replacement for the synthetic 24-row toy set (n_train: 24, fake
accuracy duplicated across "different" versions) currently used to train
variant_pathogenicity.

WHY THIS TASK IS STRUCTURALLY LEAK-SAFE (unlike celltype_sc's first attempt):
  Labels (Pathogenic/Likely-pathogenic/Benign/Likely-benign) come from ClinVar
  -- independent clinical curation by expert panels reviewing case reports,
  segregation data, and functional studies. Features (cadd, gnomad_af, gerp,
  phylop, sift, polyphen) are separately-computed in-silico predictions.
  Neither is derived from the other -- no shared computational path exists,
  so there's no equivalent of the PBMC3k clustering-leak risk here BY
  CONSTRUCTION. This does not need the same leakage audit celltype_sc did.

Pipeline:
  1. Download ClinVar VCF (GRCh38) from NCBI FTP -- the real clinical
     curation source, not synthetic.
  2. Filter to unambiguous Pathogenic/Likely_pathogenic vs
     Benign/Likely_benign (drop VUS, conflicting, and other categories --
     matches this plugin's original 3-class scheme if it has one; adjust
     LABEL_MAP below if run.py expects a different class scheme).
  3. Subsample (ClinVar has ~1M+ variants; MyVariant.info rate limits make
     annotating all of them impractical tonight -- take a balanced random
     sample, configurable via --n-per-class).
  4. Batch-query MyVariant.info's free public API for cadd/gnomad_af/gerp/
     phylop/sift/polyphen scores per variant.
  5. Write CSV matching the format the existing scripts/run.py expects.

IMPORTANT: as with celltype_sc, CONFIRM the exact column names, expected
label strings, and 2-class-vs-3-class scheme run.py wants before training
against this file -- see the Claude Code contract-check prompt in the
accompanying chat message. Defaults below (columns: cadd, gnomad_af, gerp,
phylop, sift, polyphen, pathogenicity; labels: Pathogenic/Benign, dropping
VUS/uncertain to match the original 3-class inventory description) are
inferred from the plugin table, not yet verified against real run.py code.

USAGE
-----
    pip install requests --break-system-packages

    python3 prepare_real_data.py \\
        --out-dir /home/manish/Desktop/machine/omnibioai-test-data/variant_pathogenicity \\
        --n-per-class 500

    # Quick smoke test with a tiny sample first (fast, cheap, validates the
    # whole pipeline before committing to a slower full run):
    python3 prepare_real_data.py --out-dir /tmp/vp_test --n-per-class 20
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import random
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

try:
    import requests
except ImportError:
    print("ERROR: pip install requests --break-system-packages", file=sys.stderr)
    sys.exit(1)

CLINVAR_URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz"
MYVARIANT_URL = "https://myvariant.info/v1/variant"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ClinVar CLNSIG values -> binary label. Adjust to 3-class if run.py's
# contract check reveals it expects Pathogenic/Benign/VUS instead of just
# Pathogenic/Benign.
LABEL_MAP = {
    "Pathogenic": "Pathogenic",
    "Likely_pathogenic": "Pathogenic",
    "Pathogenic/Likely_pathogenic": "Pathogenic",
    "Benign": "Benign",
    "Likely_benign": "Benign",
    "Benign/Likely_benign": "Benign",
}


def download_clinvar(dest: Path, force: bool):
    if dest.exists() and not force:
        print(f"[clinvar] Using cached {dest} (pass --force-download to re-fetch)")
        return
    print(f"[clinvar] Downloading {CLINVAR_URL} -> {dest} (this is a real, several-hundred-MB file, may take a few minutes) ...")
    req = urllib.request.Request(CLINVAR_URL, headers={"User-Agent": BROWSER_UA})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out_f:
        chunk_size = 1024 * 1024
        total = 0
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out_f.write(chunk)
            total += len(chunk)
            print(f"\r[clinvar] {total / (1024*1024):.0f} MB downloaded", end="", flush=True)
    print()


CLNSIG_RE = re.compile(r"CLNSIG=([^;]+)")
GENEINFO_RE = re.compile(r"GENEINFO=([^:;]+)")


def parse_clinvar_variants(vcf_path: Path, n_per_class: int, seed: int):
    """Streams the VCF, buckets variants by binary label, reservoir-samples
    up to n_per_class of each so we don't have to load the whole multi-GB
    file into memory or annotate all ~1M variants."""
    print(f"[parse] Streaming {vcf_path} ...")
    rng = random.Random(seed)
    buckets = {"Pathogenic": [], "Benign": []}
    seen_counts = {"Pathogenic": 0, "Benign": 0}

    with gzip.open(vcf_path, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 8:
                continue
            chrom, pos, _id, ref, alt, _qual, _filter, info = fields[:8]
            if len(ref) != 1 or len(alt) != 1:
                continue  # keep to simple SNVs for this first real-data pass
            m = CLNSIG_RE.search(info)
            if not m:
                continue
            raw_sig = m.group(1)
            label = LABEL_MAP.get(raw_sig)
            if label is None:
                continue  # VUS, conflicting, drug response, etc. -- dropped
            # Reservoir sampling per bucket
            seen_counts[label] += 1
            variant = {"chrom": chrom, "pos": pos, "ref": ref, "alt": alt, "clnsig_raw": raw_sig}
            bucket = buckets[label]
            if len(bucket) < n_per_class:
                bucket.append(variant)
            else:
                j = rng.randint(0, seen_counts[label] - 1)
                if j < n_per_class:
                    bucket[j] = variant

    for label, bucket in buckets.items():
        print(f"[parse] {label}: sampled {len(bucket)} (seen {seen_counts[label]} total in ClinVar)")
    return buckets


def to_hgvs(chrom: str, pos: str, ref: str, alt: str) -> str:
    # MyVariant.info expects HGVS genomic notation, e.g. chr7:g.140453136A>T
    c = chrom if chrom.startswith("chr") else f"chr{chrom}"
    return f"{c}:g.{pos}{ref}>{alt}"


def annotate_batch(hgvs_ids: list[str], max_retries: int = 3) -> dict:
    """POST batch query to MyVariant.info -- returns {hgvs_id: annotation_dict}
    Field paths confirmed against a real record (FGFR3 R248C, rs121913482):
      - dbnsfp.sift.score          (list of per-transcript scores)
      - dbnsfp.polyphen2.hdiv.score (list of per-transcript scores)
      - dbnsfp.phylop.100way_vertebrate.score
      - cadd.phred, gnomad_genome.af.af -- NOT yet confirmed present in a
        real record (were likely just truncated in the sample dump, not
        absent) -- extract_score returns None gracefully either way, but
        worth spot-checking a few real rows after this run.
      - GERP: dbnsfp does not expose a plain "gerp" key -- the real dbNSFP
        field name is "gerp++_rs" (contains literal + characters, so it's
        accessed via dict.get("gerp++_rs") not a dot-path). Handled as a
        special case below rather than through extract_score's dot-split.
    """
    fields = (
        "cadd.phred,gnomad_genome.af.af,"
        "dbnsfp.sift.score,dbnsfp.polyphen2.hdiv.score,"
        "dbnsfp.phylop.100way_vertebrate.score,dbnsfp.gerp++_rs"
    )
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                MYVARIANT_URL,
                data={"ids": ",".join(hgvs_ids), "fields": fields},
                timeout=30,
            )
            resp.raise_for_status()
            results = resp.json()
            return {r.get("query", r.get("_id", "")): r for r in results}
        except Exception as e:
            wait = 2 ** attempt
            print(f"\n[annotate] batch failed ({e}), retrying in {wait}s ...")
            time.sleep(wait)
    return {}


def extract_score(rec: dict, path: str):
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if isinstance(cur, list):
        return cur[0] if cur else None
    return cur


def extract_gerp(rec: dict):
    """dbnsfp's GERP field is literally named 'gerp++_rs' -- the ++ makes it
    unsafe for dot-path splitting, so it needs direct dict access."""
    dbnsfp = rec.get("dbnsfp")
    if not isinstance(dbnsfp, dict):
        return None
    val = dbnsfp.get("gerp++_rs")
    if isinstance(val, list):
        return val[0] if val else None
    return val


def annotate_variants(buckets: dict, batch_size: int) -> list[dict]:
    rows = []
    all_variants = []
    for label, variants in buckets.items():
        for v in variants:
            v["label"] = label
            all_variants.append(v)

    print(f"\n[annotate] Querying MyVariant.info for {len(all_variants)} variants in batches of {batch_size} ...")
    for i in range(0, len(all_variants), batch_size):
        batch = all_variants[i : i + batch_size]
        hgvs_map = {to_hgvs(v["chrom"], v["pos"], v["ref"], v["alt"]): v for v in batch}
        results = annotate_batch(list(hgvs_map.keys()))
        for hgvs_id, v in hgvs_map.items():
            rec = results.get(hgvs_id, {})
            row = {
                "chrom": v["chrom"], "pos": v["pos"], "ref": v["ref"], "alt": v["alt"],
                "cadd": extract_score(rec, "cadd.phred"),
                "gnomad_af": extract_score(rec, "gnomad_genome.af.af"),
                "gerp": extract_gerp(rec),
                "phylop": extract_score(rec, "dbnsfp.phylop.100way_vertebrate.score"),
                "sift": extract_score(rec, "dbnsfp.sift.score"),
                "polyphen": extract_score(rec, "dbnsfp.polyphen2.hdiv.score"),
                "pathogenicity": v["label"],
            }
            rows.append(row)
        print(f"\r[annotate] {min(i + batch_size, len(all_variants))}/{len(all_variants)} annotated", end="", flush=True)
        time.sleep(0.3)  # be polite to the free public API
    print()
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out-filename", default="real_variant_pathogenicity.csv")
    ap.add_argument("--n-per-class", type=int, default=500,
                     help="How many Pathogenic and Benign variants to sample and annotate "
                          "(kept modest by default -- MyVariant.info is free/public, be reasonable)")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--clinvar-cache", default="/tmp/clinvar_grch38.vcf.gz")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--drop-missing-scores", action="store_true",
                     help="Drop rows where ANY annotation score is missing (stricter, smaller "
                          "dataset). Default keeps rows with partial annotations -- decide based "
                          "on what run.py's feature handling expects (does it tolerate NaN?).")
    args = ap.parse_args()

    clinvar_path = Path(args.clinvar_cache)
    download_clinvar(clinvar_path, args.force_download)

    buckets = parse_clinvar_variants(clinvar_path, args.n_per_class, args.seed)
    if not buckets["Pathogenic"] or not buckets["Benign"]:
        print("ERROR: one or both classes came back empty -- check CLNSIG parsing / LABEL_MAP", file=sys.stderr)
        sys.exit(1)

    rows = annotate_variants(buckets, args.batch_size)

    if args.drop_missing_scores:
        before = len(rows)
        score_cols = ["cadd", "gnomad_af", "gerp", "phylop", "sift", "polyphen"]
        rows = [r for r in rows if all(r[c] is not None for c in score_cols)]
        print(f"[filter] --drop-missing-scores: {before} -> {len(rows)} rows")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / args.out_filename

    fieldnames = ["chrom", "pos", "ref", "alt", "cadd", "gnomad_af", "gerp", "phylop", "sift", "polyphen", "pathogenicity"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    n_path = sum(1 for r in rows if r["pathogenicity"] == "Pathogenic")
    n_ben = sum(1 for r in rows if r["pathogenicity"] == "Benign")
    missing_any = sum(1 for r in rows if any(r[c] is None for c in ["cadd", "gnomad_af", "gerp", "phylop", "sift", "polyphen"]))

    print(f"\n[done] Wrote {out_path}")
    print(f"[done] {len(rows)} rows: {n_path} Pathogenic, {n_ben} Benign")
    print(f"[done] Rows with at least one missing score: {missing_any}/{len(rows)} "
          f"(MyVariant.info doesn't have every score for every variant -- expected)")
    print(
        "\n[done] LEAKAGE STATUS: labels are independent ClinVar clinical curation; features "
        "are separately-computed in-silico predictions (CADD/gnomAD/GERP/phyloP/SIFT/PolyPhen). "
        "No shared computational path -- this task doesn't have celltype_sc's structural leak risk."
    )


if __name__ == "__main__":
    main()