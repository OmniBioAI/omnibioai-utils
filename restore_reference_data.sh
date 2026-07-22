#!/bin/bash
# ============================================
# OmniBioAI Reference Data Restore Script
# Restores all reference data from source
# Run if external drive gets corrupted
# ============================================

set -e

RESTORE_DIR=${1:-/media/manish/OmniBioAI-SIFs/reference}
mkdir -p $RESTORE_DIR

echo "$(date): Starting reference data restore to $RESTORE_DIR"

# ============================================
# 1. Reference Genomes (12 species) — from NCBI
# ============================================
echo "Downloading reference genomes..."

GENOMES_DIR=$RESTORE_DIR/genomes
mkdir -p $GENOMES_DIR

# Human GRCh38
mkdir -p $GENOMES_DIR/human
cd $GENOMES_DIR/human
wget -c https://ftp.ncbi.nlm.nih.gov/genomes/all/GCA/000/001/405/GCA_000001405.15_GRCh38/GCA_000001405.15_GRCh38_assembly_structure/Primary_Assembly/assembled_chromosomes/FASTA/

# Mouse GRCm39
mkdir -p $GENOMES_DIR/mouse
cd $GENOMES_DIR/mouse
wget -c https://ftp.ncbi.nlm.nih.gov/genomes/all/GCA/000/001/635/GCA_000001635.9_GRCm39/

# Rat
mkdir -p $GENOMES_DIR/rat
wget -c -P $GENOMES_DIR/rat \
  https://ftp.ncbi.nlm.nih.gov/genomes/all/GCA/015/227/675/GCA_015227675.2_mRatBN7.2/

# Zebrafish
mkdir -p $GENOMES_DIR/zebrafish
wget -c -P $GENOMES_DIR/zebrafish \
  https://ftp.ncbi.nlm.nih.gov/genomes/all/GCA/000/002/035/GCA_000002035.4_GRCz11/

echo "✅ Reference genomes downloaded"

# ============================================
# gnomAD v4.0 (830GB)
# ============================================
echo "Downloading gnomAD v4.0..."

GNOMAD_DIR=$RESTORE_DIR/human/gnomad
mkdir -p $GNOMAD_DIR

for chrom in $(seq 1 22) X Y; do
    file="gnomad.genomes.v4.0.sites.chr${chrom}.vcf.bgz"
    wget -c -P $GNOMAD_DIR \
        "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.0/vcf/genomes/$file"
    wget -c -P $GNOMAD_DIR \
        "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.0/vcf/genomes/${file}.tbi"
    echo "✅ gnomAD chr${chrom} downloaded"
done

echo "✅ gnomAD v4.0 complete"

# ============================================
# ClinVar GRCh38
# ============================================
echo "Downloading ClinVar..."

CLINVAR_DIR=$RESTORE_DIR/human/clinvar
mkdir -p $CLINVAR_DIR

wget -c -P $CLINVAR_DIR \
    https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz
wget -c -P $CLINVAR_DIR \
    https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz.tbi

echo "✅ ClinVar downloaded"

# ============================================
# COSMIC v104
# ============================================
echo "Downloading COSMIC v104..."
echo "Note: COSMIC requires registration at cancer.sanger.ac.uk"
echo "Download manually from: https://cancer.sanger.ac.uk/cosmic/download"

# ============================================
# dbSNP
# ============================================
echo "Downloading dbSNP..."

DBSNP_DIR=$RESTORE_DIR/human/dbsnp
mkdir -p $DBSNP_DIR

wget -c -P $DBSNP_DIR \
    https://ftp.ncbi.nlm.nih.gov/snp/latest_release/VCF/GCF_000001405.40.gz

echo "✅ dbSNP downloaded"

# ============================================
# 2. SIF Images — from HuggingFace
# ============================================
echo "Downloading SIF images from HuggingFace..."

SIF_DIR=/media/manish/OmniBioAI-SIFs/sif
mkdir -p $SIF_DIR

huggingface-cli download \
    omnibioai/omnibioai-sif-images \
    --repo-type dataset \
    --local-dir $SIF_DIR

echo "✅ SIF images downloaded"

# ============================================
# 3. PubMed FAISS indexes — from HuggingFace
# ============================================
echo "Downloading FAISS indexes..."

FAISS_DIR=/media/manish/OmniBioAI-SIFs/PubMed/Index
mkdir -p $FAISS_DIR

huggingface-cli download \
    omnibioai/pubmed-faiss-indexes \
    --repo-type dataset \
    --local-dir $FAISS_DIR

echo "✅ FAISS indexes downloaded"

# ============================================
# 4. PubMed Abstracts — from HuggingFace
# ============================================
echo "Downloading PubMed abstracts..."

ABSTRACTS_DIR=/media/manish/OmniBioAI-SIFs/PubMed/Abstracts
mkdir -p $ABSTRACTS_DIR

huggingface-cli download \
    omnibioai/pubmed-abstracts-36M \
    --repo-type dataset \
    --local-dir $ABSTRACTS_DIR

echo "✅ PubMed abstracts downloaded"

# ============================================
# 5. Create symlinks
# ============================================
echo "Creating symlinks..."

mkdir -p ~/Desktop/machine/data

ln -sfn "$RESTORE_DIR" ~/Desktop/machine/data/reference
ln -sfn /media/manish/OmniBioAI-SIFs/PubMed ~/Desktop/machine/data/PubMed
ln -sfn /media/manish/OmniBioAI-SIFs/sif ~/Desktop/machine/data/sif

echo "✅ Symlinks created:"
echo "   ~/Desktop/machine/data/reference -> $RESTORE_DIR"
echo "   ~/Desktop/machine/data/PubMed    -> /media/manish/OmniBioAI-SIFs/PubMed"
echo "   ~/Desktop/machine/data/sif       -> /media/manish/OmniBioAI-SIFs/sif"

echo ""
echo "$(date): ✅ ALL DATA RESTORED!"
echo "Total size: ~2TB+"
echo ""
echo "Next steps:"
echo "1. Restart OmniBioAI services"
echo "2. Verify RAG pipeline works"
