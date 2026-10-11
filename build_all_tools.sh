#!/bin/bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ecosystem_path.sh"
set -e

MACHINE_DIR="$OMNIBIOAI_ROOT"
ECR="068155050939.dkr.ecr.us-east-1.amazonaws.com/omnibioai"
REGION="us-east-1"
JOB_QUEUE="arn:aws:batch:us-east-1:068155050939:job-queue/omnibioai-jq"
JOB_ROLE="arn:aws:iam::068155050939:role/omnibioaiBatchJobRole"

# ── Tool definitions ──────────────────────────────────────────────
# format: tool_name|apt_packages|pip_packages
declare -a TOOLS=(
    "samtools|samtools|"
    "bcftools|bcftools|"
    "bwa|bwa|"
    "bedtools|bedtools|"
    "trimmomatic|trimmomatic|"
    "multiqc||multiqc"
    "fastqc|fastqc|"
    "deeptools||deeptools"
    "macs2||macs2"
    "kallisto|kallisto|"
    "hisat2|hisat2|"
    "stringtie|stringtie|"
    "salmon||salmon"
    "deseq2||pydeseq2"
    "kraken2|kraken2|"
    "minimap2|minimap2|"
    "busco||busco"
    "quast||quast"
    "nanostat||NanoStat"
    "fastp|fastp|"
    "vcftools|vcftools|"
    "snpeff|snpeff|"
    "diamond|diamond-aligner|"
    "prodigal|prodigal|"
    "hmmer|hmmer|"
    "muscle|muscle|"
    "iqtree|iqtree|"
    "sklearn||scikit-learn"
    "xgboost||xgboost"
    "scanpy||scanpy"
    "scrublet||scrublet"
)

# ── Step 1: ECR Login ─────────────────────────────────────────────
echo "=== Step 1: ECR Login ==="
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin \
  068155050939.dkr.ecr.us-east-1.amazonaws.com
echo "✅ ECR logged in!"

# ── Step 2: Build Dockerfiles ─────────────────────────────────────
echo ""
echo "=== Step 2: Building Dockerfiles ==="

build_tool() {
    local tool=$1
    local apt_pkgs=$2
    local pip_pkgs=$3
    
    local dockerfile="$MACHINE_DIR/omnibioai-tool-images/dockerfiles/Dockerfile.$tool"
    
    # Generate Dockerfile
    cat > "$dockerfile" << EOF
FROM python:3.11-slim-bookworm

EOF

    if [ -n "$apt_pkgs" ]; then
        cat >> "$dockerfile" << EOF
RUN apt-get update && apt-get install -y $apt_pkgs && rm -rf /var/lib/apt/lists/*

EOF
    fi

    if [ -n "$pip_pkgs" ]; then
        cat >> "$dockerfile" << EOF
RUN pip install $pip_pkgs --no-cache-dir

EOF
    fi

    cat >> "$dockerfile" << EOF
RUN pip install boto3 azure-storage-blob azure-identity --no-cache-dir

COPY omnibioai-tool-runtime/tools /app/tools
COPY omnibioai-tool-runtime/omni_tool_runtime /app/omni_tool_runtime
COPY omnibioai-tool-runtime/pyproject.toml /app/pyproject.toml
WORKDIR /app
RUN pip install -e . --no-cache-dir

CMD ["python", "-m", "tools.generic_sif_runner.run"]
EOF

    echo "✅ Dockerfile.$tool created"
}

for entry in "${TOOLS[@]}"; do
    IFS='|' read -r tool apt pip <<< "$entry"
    build_tool "$tool" "$apt" "$pip"
done

# ── Step 3: Build & Push to ECR ──────────────────────────────────
echo ""
echo "=== Step 3: Building & Pushing to ECR ==="

cd $MACHINE_DIR

PIDS=()
for entry in "${TOOLS[@]}"; do
    IFS='|' read -r tool apt pip <<< "$entry"
    
    # Create ECR repo
    aws ecr create-repository \
      --repository-name "omnibioai/$tool" \
      --region $REGION 2>/dev/null || true
    
    # Build + push in background
    docker buildx build \
      --platform linux/amd64 \
      -t "$ECR/$tool:latest" \
      -f "omnibioai-tool-images/dockerfiles/Dockerfile.$tool" \
      --push \
      . > /tmp/build_$tool.log 2>&1 &
    
    PIDS+=($!)
    echo "🔨 $tool building (PID: $!)"
done

echo ""
echo "Waiting for all builds to complete..."
for pid in "${PIDS[@]}"; do
    wait $pid
done

echo ""
echo "=== Build Results ==="
SUCCESS=()
FAILED=()
for entry in "${TOOLS[@]}"; do
    IFS='|' read -r tool apt pip <<< "$entry"
    if grep -q "exporting manifest list" /tmp/build_$tool.log 2>/dev/null; then
        echo "✅ $tool - SUCCESS"
        SUCCESS+=($tool)
    else
        echo "❌ $tool - FAILED"
        FAILED+=($tool)
        tail -3 /tmp/build_$tool.log
    fi
done

# ── Step 4: Register Batch Job Definitions ────────────────────────
echo ""
echo "=== Step 4: Registering AWS Batch Job Definitions ==="

for tool in "${SUCCESS[@]}"; do
    aws batch register-job-definition \
      --job-definition-name "omnibioai-${tool}-fargate" \
      --type container \
      --platform-capabilities '["FARGATE"]' \
      --container-properties "{
        \"image\": \"${ECR}/${tool}:latest\",
        \"resourceRequirements\": [
          {\"type\": \"VCPU\", \"value\": \"2\"},
          {\"type\": \"MEMORY\", \"value\": \"4096\"}
        ],
        \"jobRoleArn\": \"${JOB_ROLE}\",
        \"executionRoleArn\": \"${JOB_ROLE}\",
        \"environment\": [
          {\"name\": \"SIF_CACHE_DIR\", \"value\": \"/tmp/omnibioai_sif_cache\"},
          {\"name\": \"SIF_BASE\", \"value\": \"s3://omnibioai-sif-068155050939\"}
        ],
        \"command\": [\"python\", \"-m\", \"tools.generic_sif_runner.run\"],
        \"networkConfiguration\": {\"assignPublicIp\": \"ENABLED\"}
      }" \
      --region $REGION \
      --query 'revision' \
      --output text > /tmp/jd_$tool.txt 2>&1

    REV=$(cat /tmp/jd_$tool.txt)
    echo "✅ omnibioai-${tool}-fargate:${REV}"
done

# ── Step 5: Test each tool ────────────────────────────────────────
echo ""
echo "=== Step 5: Testing tools on AWS Batch ==="

TES_URL="http://127.0.0.1:8081"

# Test inputs per tool
declare -A TEST_INPUTS=(
    ["fastqc"]='{"input_file":"s3://omnibioai-results-068155050939-20260208025003/test/test_sample.fastq"}'
    ["multiqc"]='{"input_dir":"s3://omnibioai-results-068155050939-20260208025003/test/","output_name":"report"}'
    ["samtools_sort"]='{"input_sam":"s3://omnibioai-results-068155050939-20260208025003/test/test_sample.fastq"}'
    ["bcftools_view"]='{"input_vcf":"s3://omnibioai-results-068155050939-20260208025003/test/test_sample.fastq"}'
    ["trimmomatic"]='{"input_file":"s3://omnibioai-results-068155050939-20260208025003/test/test_sample.fastq","output_file":"trimmed.fastq"}'
)

RESULTS=()
for tool_id in fastqc multiqc samtools_sort trimmomatic; do
    inputs="${TEST_INPUTS[$tool_id]}"
    if [ -z "$inputs" ]; then
        echo "⏭ $tool_id - no test input defined"
        continue
    fi
    
    echo "🧪 Testing $tool_id..."
    
    RESPONSE=$(curl -s -X POST "$TES_URL/api/runs/submit" \
      -H "Content-Type: application/json" \
      -d "{
        \"tool_id\": \"$tool_id\",
        \"inputs\": $inputs,
        \"resources\": {\"cpu\": 2},
        \"constraints\": {\"preferred_server_id\": \"aws_batch_demo\"}
      }")
    
    RUN_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id','ERROR'))" 2>/dev/null)
    
    if [ "$RUN_ID" = "ERROR" ]; then
        echo "❌ $tool_id - submission failed: $RESPONSE"
        RESULTS+=("❌ $tool_id - SUBMISSION_FAILED")
        continue
    fi
    
    echo "  run_id: $RUN_ID - waiting..."
    
    # Poll for completion (max 5 min)
    for i in $(seq 1 30); do
        sleep 10
        STATE=$(curl -s "$TES_URL/api/runs/$RUN_ID" | \
          python3 -c "import sys,json; print(json.load(sys.stdin).get('state','unknown'))" 2>/dev/null)
        
        if [ "$STATE" = "COMPLETED" ]; then
            echo "  ✅ $tool_id - COMPLETED!"
            RESULTS+=("✅ $tool_id - COMPLETED")
            break
        elif [ "$STATE" = "FAILED" ]; then
            echo "  ❌ $tool_id - FAILED"
            RESULTS+=("❌ $tool_id - FAILED")
            break
        else
            echo "  ⏳ $tool_id - $STATE (${i}0s)"
        fi
    done
done

# ── Final Summary ─────────────────────────────────────────────────
echo ""
echo "========================================"
echo "         FINAL SUMMARY"
echo "========================================"
echo ""
echo "Build Results:"
echo "  ✅ Success: ${#SUCCESS[@]} tools"
echo "  ❌ Failed:  ${#FAILED[@]} tools"
echo ""
echo "Test Results:"
for r in "${RESULTS[@]}"; do
    echo "  $r"
done
echo ""
echo "ECR Images:"
aws ecr describe-repositories \
  --region $REGION \
  --query 'repositories[*].repositoryName' \
  --output table
echo "========================================"
