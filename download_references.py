#!/usr/bin/env python3
"""
Comprehensive reference genome and database downloader for OmniBioAI.

Usage:
    python download_references.py --help
    python download_references.py --base-dir ~/Desktop/machine/omnibioai-data/reference \
        --species human mouse --assemblies GRCh38 GRCm39 \
        --include-variants --include-databases --threads 8
    python download_references.py --status --base-dir ~/Desktop/machine/omnibioai-data/reference
    python download_references.py --dry-run --species human --assemblies GRCh38
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

REGISTRY_FILENAME = "dataset_registry.json"


def load_registry(registry_path: Path) -> dict:
    if registry_path.exists():
        with open(registry_path) as f:
            return json.load(f)
    return {}


def save_registry(registry_path: Path, registry: dict) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)
    print(f"[REGISTRY] Updated {registry_path}")


def register_file(registry: dict, category: str, entry: dict) -> None:
    """Upsert an entry under registry[category] keyed by 'name'."""
    bucket = registry.setdefault(category, [])
    name = entry["name"]
    for i, existing in enumerate(bucket):
        if existing.get("name") == name:
            bucket[i] = entry
            return
    bucket.append(entry)


# ---------------------------------------------------------------------------
# Core downloader
# ---------------------------------------------------------------------------

class ReferenceDownloader:
    def __init__(self, base_dir: Path, threads: int = 8, dry_run: bool = False):
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.threads = threads
        self.dry_run = dry_run
        self.registry_path = self.base_dir / REGISTRY_FILENAME
        self.registry = load_registry(self.registry_path)
        self._downloaded: list[dict] = []   # track what we fetched this run

    # ------------------------------------------------------------------
    # Low-level download
    # ------------------------------------------------------------------

    def download_file(self, url: str, dest: Path, desc: str = "") -> bool:
        """Download a file with resume support; skip if already present."""
        dest = Path(dest)
        label = desc or dest.name

        if dest.exists() and dest.stat().st_size > 0:
            print(f"[SKIP]  {label} — already exists ({dest})")
            return False

        if self.dry_run:
            print(f"[DRY]   Would download {label}\n        {url}\n        -> {dest}")
            return False

        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[GET]   {label}")
        print(f"        {url}")
        subprocess.run(
            ["wget", "-c", "--progress=bar:force", "-O", str(dest), url],
            check=True,
        )
        print(f"[DONE]  {label}")
        self._downloaded.append({"desc": label, "path": str(dest), "url": url})
        return True

    def _batch(self, files: dict[str, str], base: Path, prefix: str = "") -> None:
        """Download a dict of {filename: url} into base directory."""
        for fname, url in files.items():
            dest = base / fname
            self.download_file(url, dest, f"{prefix} {fname}".strip())

    # ------------------------------------------------------------------
    # Genome downloads
    # ------------------------------------------------------------------

    def download_human_GRCh38(self) -> None:
        base = self.base_dir / "organisms/human/GRCh38"
        files = {
            "genome.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/GRCh38.primary_assembly.genome.fa.gz",
            "gencode.v46.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.annotation.gtf.gz",
            "gencode.v46.transcripts.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.transcripts.fa.gz",
            "gencode.v46.basic.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.basic.annotation.gtf.gz",
            "gencode.v46.lncRNA_transcripts.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.lncRNA_transcripts.fa.gz",
        }
        self._batch(files, base, "Human GRCh38")
        register_file(self.registry, "genomes", {
            "name": "human_GRCh38_gencode46",
            "organism": "human",
            "assembly": "GRCh38",
            "annotation": "gencode_v46",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_human_GRCh37(self) -> None:
        base = self.base_dir / "organisms/human/GRCh37"
        files = {
            "gencode.v46lift37.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/GRCh37_mapping/gencode.v46lift37.annotation.gtf.gz",
            "gencode.v46lift37.transcripts.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/GRCh37_mapping/gencode.v46lift37.transcripts.fa.gz",
        }
        self._batch(files, base, "Human GRCh37")
        register_file(self.registry, "genomes", {
            "name": "human_GRCh37_gencode46lift37",
            "organism": "human",
            "assembly": "GRCh37",
            "annotation": "gencode_v46lift37",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_human_T2T(self) -> None:
        base = self.base_dir / "organisms/human/T2T-CHM13"
        files = {
            "chm13v2.0.fa.gz":
                "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/assemblies/analysis_set/chm13v2.0.fa.gz",
            "chm13v2.0_RefSeq_Liftoff_v5.1.gff3.gz":
                "https://s3-us-west-2.amazonaws.com/human-pangenomics/T2T/CHM13/assemblies/annotation/chm13v2.0_RefSeq_Liftoff_v5.1.gff3.gz",
        }
        self._batch(files, base, "Human T2T-CHM13")
        register_file(self.registry, "genomes", {
            "name": "human_T2T_CHM13v2",
            "organism": "human",
            "assembly": "T2T-CHM13v2",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_mouse_GRCm39(self) -> None:
        base = self.base_dir / "organisms/mouse/GRCm39"
        files = {
            "genome.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M33/GRCm39.primary_assembly.genome.fa.gz",
            "gencode.vM33.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M33/gencode.vM33.annotation.gtf.gz",
            "gencode.vM33.transcripts.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M33/gencode.vM33.transcripts.fa.gz",
            "gencode.vM33.basic.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M33/gencode.vM33.basic.annotation.gtf.gz",
        }
        self._batch(files, base, "Mouse GRCm39")
        register_file(self.registry, "genomes", {
            "name": "mouse_GRCm39_gencode_M33",
            "organism": "mouse",
            "assembly": "GRCm39",
            "annotation": "gencode_vM33",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_mouse_GRCm38(self) -> None:
        base = self.base_dir / "organisms/mouse/GRCm38"
        files = {
            "genome.fa.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M25/GRCm38.primary_assembly.genome.fa.gz",
            "gencode.vM25.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M25/gencode.vM25.annotation.gtf.gz",
        }
        self._batch(files, base, "Mouse GRCm38")
        register_file(self.registry, "genomes", {
            "name": "mouse_GRCm38_gencode_M25",
            "organism": "mouse",
            "assembly": "GRCm38",
            "annotation": "gencode_vM25",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_rat_GRCr8(self) -> None:
        base = self.base_dir / "organisms/rat/GRCr8"
        files = {
            "genome.fa.gz":
                "https://ftp.ensembl.org/pub/release-112/fasta/rattus_norvegicus/dna/Rattus_norvegicus.GRCr8.dna.toplevel.fa.gz",
            "annotation.gtf.gz":
                "https://ftp.ensembl.org/pub/release-112/gtf/rattus_norvegicus/Rattus_norvegicus.GRCr8.112.gtf.gz",
        }
        self._batch(files, base, "Rat GRCr8")
        register_file(self.registry, "genomes", {
            "name": "rat_GRCr8_ensembl112",
            "organism": "rat",
            "assembly": "GRCr8",
            "annotation": "ensembl_112",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_zebrafish_GRCz11(self) -> None:
        base = self.base_dir / "organisms/zebrafish/GRCz11"
        files = {
            "genome.fa.gz":
                "https://ftp.ensembl.org/pub/release-112/fasta/danio_rerio/dna/Danio_rerio.GRCz11.dna.toplevel.fa.gz",
            "annotation.gtf.gz":
                "https://ftp.ensembl.org/pub/release-112/gtf/danio_rerio/Danio_rerio.GRCz11.112.gtf.gz",
            "transcripts.fa.gz":
                "https://ftp.ensembl.org/pub/release-112/fasta/danio_rerio/cdna/Danio_rerio.GRCz11.cdna.all.fa.gz",
        }
        self._batch(files, base, "Zebrafish GRCz11")
        register_file(self.registry, "genomes", {
            "name": "zebrafish_GRCz11_ensembl112",
            "organism": "zebrafish",
            "assembly": "GRCz11",
            "annotation": "ensembl_112",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_drosophila_BDGP6(self) -> None:
        base = self.base_dir / "organisms/drosophila/BDGP6"
        files = {
            "genome.fa.gz":
                "https://ftp.ensembl.org/pub/release-112/fasta/drosophila_melanogaster/dna/Drosophila_melanogaster.BDGP6.46.dna.toplevel.fa.gz",
            "annotation.gtf.gz":
                "https://ftp.ensembl.org/pub/release-112/gtf/drosophila_melanogaster/Drosophila_melanogaster.BDGP6.46.112.gtf.gz",
        }
        self._batch(files, base, "Drosophila BDGP6")
        register_file(self.registry, "genomes", {
            "name": "drosophila_BDGP6_ensembl112",
            "organism": "drosophila",
            "assembly": "BDGP6",
            "annotation": "ensembl_112",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    def download_yeast_R64(self) -> None:
        base = self.base_dir / "organisms/yeast/R64"
        files = {
            "genome.fa.gz":
                "https://ftp.ensembl.org/pub/release-112/fasta/saccharomyces_cerevisiae/dna/Saccharomyces_cerevisiae.R64-1-1.dna.toplevel.fa.gz",
            "annotation.gtf.gz":
                "https://ftp.ensembl.org/pub/release-112/gtf/saccharomyces_cerevisiae/Saccharomyces_cerevisiae.R64-1-1.112.gtf.gz",
        }
        self._batch(files, base, "Yeast R64")
        register_file(self.registry, "genomes", {
            "name": "yeast_R64_ensembl112",
            "organism": "yeast",
            "assembly": "R64-1-1",
            "annotation": "ensembl_112",
            "path": str(base),
            "files": list(files.keys()),
            "downloaded_at": _now(),
        })

    # ------------------------------------------------------------------
    # Annotation downloads (separate annotation directory)
    # ------------------------------------------------------------------

    def download_annotation_human(self) -> None:
        ann_base = self.base_dir / "annotation/human"

        # GENCODE v46
        gencode_dir = ann_base / "gencode"
        self._batch({
            "gencode.v46.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.annotation.gtf.gz",
            "gencode.v46.chr_patch_hapl_scaff.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_46/gencode.v46.chr_patch_hapl_scaff.annotation.gtf.gz",
        }, gencode_dir, "GENCODE v46")

        # Ensembl v112
        ensembl_dir = ann_base / "ensembl"
        self._batch({
            "Homo_sapiens.GRCh38.112.gtf.gz":
                "https://ftp.ensembl.org/pub/release-112/gtf/homo_sapiens/Homo_sapiens.GRCh38.112.gtf.gz",
        }, ensembl_dir, "Ensembl 112 human")

        # RefSeq
        refseq_dir = ann_base / "refseq"
        self._batch({
            "GRCh38_latest_genomic.gff.gz":
                "https://ftp.ncbi.nlm.nih.gov/refseq/H_sapiens/annotation/GRCh38_latest/refseq_identifiers/GRCh38_latest_genomic.gff.gz",
        }, refseq_dir, "RefSeq GRCh38")

    def download_annotation_mouse(self) -> None:
        ann_base = self.base_dir / "annotation/mouse"

        gencode_dir = ann_base / "gencode"
        self._batch({
            "gencode.vM33.annotation.gtf.gz":
                "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M33/gencode.vM33.annotation.gtf.gz",
        }, gencode_dir, "GENCODE vM33 mouse")

        ensembl_dir = ann_base / "ensembl"
        self._batch({
            "Mus_musculus.GRCm39.112.gtf.gz":
                "https://ftp.ensembl.org/pub/release-112/gtf/mus_musculus/Mus_musculus.GRCm39.112.gtf.gz",
        }, ensembl_dir, "Ensembl 112 mouse")

    # ------------------------------------------------------------------
    # Variant databases
    # ------------------------------------------------------------------

    def download_variants_human(self) -> None:
        base = self.base_dir / "variants/human"

        # dbSNP 156 (latest b156 build, GRCh38)
        self.download_file(
            "https://ftp.ncbi.nih.gov/snp/latest_release/VCF/GCF_000001405.40.gz",
            base / "dbsnp/dbsnp_GRCh38_latest.vcf.gz",
            "dbSNP latest GRCh38",
        )
        self.download_file(
            "https://ftp.ncbi.nih.gov/snp/latest_release/VCF/GCF_000001405.40.gz.tbi",
            base / "dbsnp/dbsnp_GRCh38_latest.vcf.gz.tbi",
            "dbSNP latest index",
        )

        # ClinVar
        self.download_file(
            "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz",
            base / "clinvar/clinvar_GRCh38.vcf.gz",
            "ClinVar GRCh38",
        )
        self.download_file(
            "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz.tbi",
            base / "clinvar/clinvar_GRCh38.vcf.gz.tbi",
            "ClinVar index",
        )

        # GATK bundle
        gatk_base_url = "https://storage.googleapis.com/genomics-public-data/resources/broad/hg38/v0"
        gatk_files = {
            "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz":
                f"{gatk_base_url}/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz",
            "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi":
                f"{gatk_base_url}/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi",
            "1000G_phase1.snps.high_confidence.hg38.vcf.gz":
                f"{gatk_base_url}/1000G_phase1.snps.high_confidence.hg38.vcf.gz",
            "1000G_phase1.snps.high_confidence.hg38.vcf.gz.tbi":
                f"{gatk_base_url}/1000G_phase1.snps.high_confidence.hg38.vcf.gz.tbi",
        }
        self._batch(gatk_files, base / "gatk_bundle", "GATK bundle")

        register_file(self.registry, "variants", {
            "name": "human_variants_GRCh38",
            "organism": "human",
            "assembly": "GRCh38",
            "sources": ["dbsnp", "clinvar", "gatk_bundle"],
            "path": str(base),
            "downloaded_at": _now(),
        })

    def download_variants_mouse(self) -> None:
        base = self.base_dir / "variants/mouse"
        self.download_file(
            "https://ftp.ensembl.org/pub/release-112/vcf/mus_musculus/Mus_musculus.GRCm39.vcf.gz",
            base / "ensembl_variants_GRCm39.vcf.gz",
            "Mouse Ensembl variants GRCm39",
        )
        register_file(self.registry, "variants", {
            "name": "mouse_variants_GRCm39",
            "organism": "mouse",
            "assembly": "GRCm39",
            "sources": ["ensembl_112"],
            "path": str(base),
            "downloaded_at": _now(),
        })

    # ------------------------------------------------------------------
    # Protein / functional databases
    # ------------------------------------------------------------------

    def download_databases(self) -> None:
        base = self.base_dir / "databases"

        # UniProt Swiss-Prot
        self.download_file(
            "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_sprot.fasta.gz",
            base / "uniprot/swissprot/uniprot_sprot.fasta.gz",
            "UniProt SwissProt",
        )
        # UniProt TrEMBL (large — ~100 GB)
        # self.download_file(
        #     "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/complete/uniprot_trembl.fasta.gz",
        #     base / "uniprot/trembl/uniprot_trembl.fasta.gz",
        #     "UniProt TrEMBL",
        # )

        # Pfam-A HMM profiles
        self.download_file(
            "https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.hmm.gz",
            base / "pfam/Pfam-A.hmm.gz",
            "Pfam-A HMM",
        )
        self.download_file(
            "https://ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.hmm.dat.gz",
            base / "pfam/Pfam-A.hmm.dat.gz",
            "Pfam-A HMM metadata",
        )

        # InterPro
        self.download_file(
            "https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/interpro.xml.gz",
            base / "interpro/interpro.xml.gz",
            "InterPro XML",
        )
        self.download_file(
            "https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/entry.list",
            base / "interpro/entry.list",
            "InterPro entry list",
        )

        # Gene Ontology
        self.download_file(
            "https://purl.obolibrary.org/obo/go/go-basic.obo",
            base / "go/go-basic.obo",
            "Gene Ontology (basic)",
        )
        self.download_file(
            "https://purl.obolibrary.org/obo/go.obo",
            base / "go/go-full.obo",
            "Gene Ontology (full)",
        )

        register_file(self.registry, "databases", {
            "name": "functional_databases",
            "sources": ["uniprot_swissprot", "pfam", "interpro", "go"],
            "path": str(base),
            "downloaded_at": _now(),
        })

    # ------------------------------------------------------------------
    # Directory scaffolding
    # ------------------------------------------------------------------

    def scaffold_directories(self) -> None:
        """Create all expected subdirectories so the tree is always present."""
        dirs = [
            "organisms/human/GRCh38",
            "organisms/human/GRCh37",
            "organisms/human/T2T-CHM13",
            "organisms/mouse/GRCm39",
            "organisms/mouse/GRCm38",
            "organisms/rat/GRCr8",
            "organisms/zebrafish/GRCz11",
            "organisms/drosophila/BDGP6",
            "organisms/yeast/R64",
            "indexes/star/human_GRCh38_gencode46",
            "indexes/star/human_GRCh37_gencode46lift37",
            "indexes/star/mouse_GRCm39_gencode33",
            "indexes/salmon/human_GRCh38_gencode46",
            "indexes/salmon/mouse_GRCm39_gencode33",
            "indexes/bwa/human_GRCh38",
            "indexes/bwa/mouse_GRCm39",
            "indexes/bowtie2/human_GRCh38",
            "indexes/bowtie2/mouse_GRCm39",
            "indexes/cellranger/human_2024-A",
            "indexes/cellranger/mouse_2024-A",
            "variants/human/dbsnp",
            "variants/human/clinvar",
            "variants/human/gnomad",
            "variants/human/cosmic",
            "variants/human/gatk_bundle",
            "variants/mouse",
            "annotation/human/gencode",
            "annotation/human/ensembl",
            "annotation/human/refseq",
            "annotation/human/ucsc",
            "annotation/mouse/gencode",
            "annotation/mouse/ensembl",
            "databases/uniprot/swissprot",
            "databases/uniprot/trembl",
            "databases/pfam",
            "databases/interpro",
            "databases/go",
        ]
        if self.dry_run:
            print("[DRY] Would scaffold the following directories:")
            for d in dirs:
                print(f"      {self.base_dir / d}")
            return
        for d in dirs:
            (self.base_dir / d).mkdir(parents=True, exist_ok=True)
        print(f"[OK]  Directory scaffold complete under {self.base_dir}")

    # ------------------------------------------------------------------
    # Status report
    # ------------------------------------------------------------------

    def print_status(self) -> None:
        print(f"\n{'='*70}")
        print(f"Reference data status: {self.base_dir}")
        print(f"{'='*70}\n")

        checks: list[tuple[str, Path]] = [
            # Genomes
            ("Human GRCh38 genome",           self.base_dir / "organisms/human/GRCh38/genome.fa.gz"),
            ("Human GRCh38 GTF",              self.base_dir / "organisms/human/GRCh38/gencode.v46.annotation.gtf.gz"),
            ("Human GRCh38 transcripts",      self.base_dir / "organisms/human/GRCh38/gencode.v46.transcripts.fa.gz"),
            ("Human GRCh37 lift GTF",         self.base_dir / "organisms/human/GRCh37/gencode.v46lift37.annotation.gtf.gz"),
            ("Human T2T genome",              self.base_dir / "organisms/human/T2T-CHM13/chm13v2.0.fa.gz"),
            ("Mouse GRCm39 genome",           self.base_dir / "organisms/mouse/GRCm39/genome.fa.gz"),
            ("Mouse GRCm39 GTF",              self.base_dir / "organisms/mouse/GRCm39/gencode.vM33.annotation.gtf.gz"),
            ("Mouse GRCm38 genome",           self.base_dir / "organisms/mouse/GRCm38/genome.fa.gz"),
            ("Rat GRCr8 genome",              self.base_dir / "organisms/rat/GRCr8/genome.fa.gz"),
            ("Zebrafish GRCz11 genome",       self.base_dir / "organisms/zebrafish/GRCz11/genome.fa.gz"),
            ("Drosophila BDGP6 genome",       self.base_dir / "organisms/drosophila/BDGP6/genome.fa.gz"),
            ("Yeast R64 genome",              self.base_dir / "organisms/yeast/R64/genome.fa.gz"),
            # Annotation
            ("GENCODE v46 GTF",               self.base_dir / "annotation/human/gencode/gencode.v46.annotation.gtf.gz"),
            ("Ensembl 112 human GTF",         self.base_dir / "annotation/human/ensembl/Homo_sapiens.GRCh38.112.gtf.gz"),
            ("GENCODE vM33 mouse GTF",        self.base_dir / "annotation/mouse/gencode/gencode.vM33.annotation.gtf.gz"),
            # Variants
            ("dbSNP GRCh38",                  self.base_dir / "variants/human/dbsnp/dbsnp_GRCh38_latest.vcf.gz"),
            ("ClinVar GRCh38",                self.base_dir / "variants/human/clinvar/clinvar_GRCh38.vcf.gz"),
            ("GATK Mills indels",             self.base_dir / "variants/human/gatk_bundle/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz"),
            # Databases
            ("UniProt SwissProt",             self.base_dir / "databases/uniprot/swissprot/uniprot_sprot.fasta.gz"),
            ("Pfam-A HMM",                    self.base_dir / "databases/pfam/Pfam-A.hmm.gz"),
            ("InterPro XML",                  self.base_dir / "databases/interpro/interpro.xml.gz"),
            ("Gene Ontology OBO",             self.base_dir / "databases/go/go-basic.obo"),
            # CellRanger (pre-existing)
            ("CellRanger human 2024-A",       self.base_dir / "organisms/human/refdata-gex-GRCh38-2024-A"),
        ]

        present = 0
        missing = 0
        for label, path in checks:
            exists = path.exists()
            size = ""
            if exists and path.is_file():
                size = _human_size(path.stat().st_size)
            status = f"[OK]  {size:>10}  {label}" if exists else f"[--]             {label}"
            print(status)
            if exists:
                present += 1
            else:
                missing += 1

        print(f"\n{present} present, {missing} missing\n")

        # Registry summary
        if self.registry:
            print(f"Registry entries: {sum(len(v) for v in self.registry.values())} across {len(self.registry)} categories")
        else:
            print("Registry: empty")

    # ------------------------------------------------------------------
    # Commit registry changes
    # ------------------------------------------------------------------

    def flush_registry(self) -> None:
        if not self.dry_run:
            save_registry(self.registry_path, self.registry)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


# ---------------------------------------------------------------------------
# Assembly → downloader method mapping
# ---------------------------------------------------------------------------

ASSEMBLY_MAP: dict[str, tuple[str, str]] = {
    # (species, assembly): method_name
    "GRCh38":    ("human",      "download_human_GRCh38"),
    "GRCh37":    ("human",      "download_human_GRCh37"),
    "T2T-CHM13": ("human",      "download_human_T2T"),
    "GRCm39":    ("mouse",      "download_mouse_GRCm39"),
    "GRCm38":    ("mouse",      "download_mouse_GRCm38"),
    "GRCr8":     ("rat",        "download_rat_GRCr8"),
    "GRCz11":    ("zebrafish",  "download_zebrafish_GRCz11"),
    "BDGP6":     ("drosophila", "download_drosophila_BDGP6"),
    "R64":       ("yeast",      "download_yeast_R64"),
}

SPECIES_DEFAULT_ASSEMBLIES: dict[str, list[str]] = {
    "human":      ["GRCh38", "GRCh37", "T2T-CHM13"],
    "mouse":      ["GRCm39", "GRCm38"],
    "rat":        ["GRCr8"],
    "zebrafish":  ["GRCz11"],
    "drosophila": ["BDGP6"],
    "yeast":      ["R64"],
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OmniBioAI reference genome and database downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--base-dir",
        default=os.path.expanduser("~/Desktop/machine/omnibioai-data/reference"),
        help="Base reference data directory (default: ~/Desktop/machine/omnibioai-data/reference)",
    )
    parser.add_argument(
        "--species",
        nargs="+",
        choices=list(SPECIES_DEFAULT_ASSEMBLIES),
        metavar="SPECIES",
        help="Organisms to download (human mouse rat zebrafish drosophila yeast)",
    )
    parser.add_argument(
        "--assemblies",
        nargs="+",
        choices=list(ASSEMBLY_MAP),
        metavar="ASSEMBLY",
        help="Specific assemblies (GRCh38 GRCh37 T2T-CHM13 GRCm39 GRCm38 GRCr8 GRCz11 BDGP6 R64)",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["genome", "annotation", "transcripts"],
        metavar="TYPE",
        help="Restrict to these data types (genome annotation transcripts) — currently informational",
    )
    parser.add_argument(
        "--include-variants",
        action="store_true",
        help="Download human and mouse variant databases",
    )
    parser.add_argument(
        "--include-databases",
        action="store_true",
        help="Download protein/functional databases (UniProt, Pfam, GO, InterPro)",
    )
    parser.add_argument(
        "--include-annotation",
        action="store_true",
        help="Download separate annotation files into annotation/ tree",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="Number of wget threads (default: 8, reserved for future parallel downloads)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without actually downloading",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print a status report of what files exist and exit",
    )
    parser.add_argument(
        "--scaffold",
        action="store_true",
        help="Create the full directory skeleton without downloading anything",
    )

    return parser.parse_args()


def resolve_assemblies(species: list[str] | None, assemblies: list[str] | None) -> list[str]:
    """Build a deduplicated list of assemblies to download.

    Rules:
    - --assemblies only  → exactly those assemblies
    - --species only     → all default assemblies for those species
    - both               → intersection: assemblies that belong to the given species
    """
    if assemblies and not species:
        return list(assemblies)

    if species and not assemblies:
        result: list[str] = []
        for sp in species:
            for asm in SPECIES_DEFAULT_ASSEMBLIES.get(sp, []):
                if asm not in result:
                    result.append(asm)
        return result

    if species and assemblies:
        # Keep only assemblies that belong to one of the requested species
        valid: set[str] = set()
        for sp in species:
            valid.update(SPECIES_DEFAULT_ASSEMBLIES.get(sp, []))
        return [a for a in assemblies if a in valid]

    return []


def main() -> None:
    args = parse_args()
    base = Path(args.base_dir).expanduser().resolve()

    dl = ReferenceDownloader(base_dir=base, threads=args.threads, dry_run=args.dry_run)

    if args.status:
        dl.print_status()
        return

    if args.scaffold:
        dl.scaffold_directories()
        dl.flush_registry()
        return

    # Always scaffold dirs first (no-op if dry_run)
    dl.scaffold_directories()

    # Resolve target assemblies
    targets = resolve_assemblies(args.species, args.assemblies)

    if not targets and not args.include_variants and not args.include_databases and not args.include_annotation:
        print("Nothing to do. Specify --species, --assemblies, --include-variants,")
        print("--include-databases, or --include-annotation. Use --help for details.")
        sys.exit(0)

    if args.only:
        print(f"[INFO] --only filter: {args.only} (currently informational; all files downloaded)")

    # Download genomes/annotations bundled per assembly
    for asm in targets:
        if asm not in ASSEMBLY_MAP:
            print(f"[WARN] Unknown assembly '{asm}', skipping.")
            continue
        method_name = ASSEMBLY_MAP[asm][1]
        method = getattr(dl, method_name, None)
        if method is None:
            print(f"[WARN] No downloader for assembly '{asm}', skipping.")
            continue
        print(f"\n--- {asm} ---")
        method()

    if args.include_annotation:
        print("\n--- Annotation ---")
        if not args.species or "human" in (args.species or []):
            dl.download_annotation_human()
        if not args.species or "mouse" in (args.species or []):
            dl.download_annotation_mouse()

    if args.include_variants:
        print("\n--- Variants ---")
        if not args.species or "human" in (args.species or []):
            dl.download_variants_human()
        if not args.species or "mouse" in (args.species or []):
            dl.download_variants_mouse()

    if args.include_databases:
        print("\n--- Functional databases ---")
        dl.download_databases()

    dl.flush_registry()

    if not args.dry_run:
        print(f"\n[DONE] Run completed. Registry saved to {dl.registry_path}")


if __name__ == "__main__":
    main()
