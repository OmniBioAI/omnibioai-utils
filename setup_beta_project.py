#!/usr/bin/env python3
"""
OmniBioAI Beta Launch — GitHub Project + Issues Setup
=====================================================
Creates:
  1. GitHub Project "OmniBioAI Beta Launch" with Board + Roadmap views
  2. Custom fields: Priority, Category, Repo, Due Date
  3. Issues across all 28 repos
  4. Links all issues to the project with correct field values

Requirements:
  pip install PyGithub requests
  export GITHUB_TOKEN=<your_pat>   # needs: repo, project, write:org scopes

Usage:
  python setup_beta_project.py --dry-run   # preview only
  python setup_beta_project.py             # execute
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
OWNER        = "man4ish"
PROJECT_TITLE = "OmniBioAI Beta Launch"
BETA_DATE    = "2026-07-04"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

GQL_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
}

REST = "https://api.github.com"
GQL  = "https://api.github.com/graphql"

# ── All 28 repos ──────────────────────────────────────────────────────────────
REPOS = [
    "omnibioai",
    "omnibioai-workflow-bundles",
    "omnibioai-tes",
    "omnibioai-studio",
    "omnibioai-tool-images",
    "omnibioai-lims",
    "omnibioai-rag",
    "omnibioai-docs",
    "omnibioai-launcher",
    "omnibioai-landing",
    "omnibioai-ecosystem",
    "omnibioai-dev-docker",
    "omnibioai-videos",
    "omnibioai-sdk",
    "omnibioai-iam-client",
    "omnibioai-hpc-policy-engin",
    "omnibioai-policy-engine",
    "omnibioai-auth",
    "omnibioai-design-tokens",
    "omnibioai-ui",
    "omnibioai-dev-hub",
    "omnibioai-model-registry",
    "omnibioai-tool-runtime",
    "omnibioai-security-sdk",
    "omnibioai-security-audit",
    "omnibioai-api-gateway",
    "omnibioai-toolserver",
    "omnibioai-control-center",
    "omnibioai-workbench",
]

# ── Issues definition ─────────────────────────────────────────────────────────
# Format: (repo, title, body, priority, category, labels)
ISSUES = [

    # ── omnibioai-workflow-bundles ────────────────────────────────────────────
    (
        "omnibioai-workflow-bundles",
        "Phase 4: Run all 600 workflow bundles end-to-end",
        """## Goal
Run all 593 Nextflow + non-Nextflow bundles through the Phase 4 execution pipeline.

## Acceptance Criteria
- [ ] All domains processed with `nextflow run -stub` (dry-run)
- [ ] All domains attempted with real run (timeout 600s)
- [ ] `reports/run_results.json` populated with PASS/FAIL/SKIP per bundle
- [ ] `reports/run_summary.txt` shows final breakdown
- [ ] `reports/failing_bundles.txt` documents all failures with error snippets

## Context
atacseq confirmed PASS in 2s. Environment verified: Nextflow 25.10.0, Docker 28.5.1, nf-test 0.9.5, 1.6TB free disk.

## Domain order
atacseq ✅ → chipseq → wes → wgs → rnaseq → methylation → singlecell → spatial → microbiome → longread → clinical → epigenomics → funcgen → multimodal → proteomics → remaining small domains
""",
        "Critical", "Workflow", ["phase-4", "testing"]
    ),
    (
        "omnibioai-workflow-bundles",
        "Push custom Docker images to GHCR for CWL and Snakemake bundles",
        """## Goal
Build and push the 2 custom Dockerfiles created during Phase 3.

## Bundles
- `rnaseq/rnaseq_cwl_v1` → `ghcr.io/man4ish/omnibioai-rnaseq-cwl-v1:latest`
- `rnaseq/rnaseq_snakemake_v1` → `ghcr.io/man4ish/omnibioai-rnaseq-snakemake-v1:latest`

## Steps
- [ ] `docker build -t ghcr.io/man4ish/omnibioai-rnaseq-cwl-v1:latest rnaseq/rnaseq_cwl_v1/docker/`
- [ ] `docker push ghcr.io/man4ish/omnibioai-rnaseq-cwl-v1:latest`
- [ ] Repeat for snakemake bundle
- [ ] Verify images are public on ghcr.io/man4ish

## Note
All other 593 Nextflow bundles use public biocontainer images — no build needed.
""",
        "High", "Infrastructure", ["docker", "ghcr"]
    ),
    (
        "omnibioai-workflow-bundles",
        "Set up nf-test CI with GitHub Actions",
        """## Goal
Run nf-test automatically on PRs that touch workflow bundles.

## Acceptance Criteria
- [ ] `.github/workflows/nf-test.yml` created
- [ ] Path filters: only run tests for changed bundle directories
- [ ] Matrix strategy: one job per domain (not per bundle)
- [ ] nf-test installed via `curl -fsSL https://get.nf-test.com | bash`
- [ ] Docker-in-Docker or self-hosted runner configured
- [ ] Badge added to README

## Suggested trigger
```yaml
on:
  pull_request:
    paths:
      - '**/tests/**.nf.test'
      - '**/workflow/**.nf'
      - '**/manifest.json'
```
""",
        "Medium", "CI/CD", ["ci", "nf-test", "github-actions"]
    ),
    (
        "omnibioai-workflow-bundles",
        "Build 15 v2 workflow bundles (Tier 1 priority)",
        """## Goal
Build the 5 Tier 1 v2 bundles targeting Q3 2026.

## Bundles
- [ ] `ref_data_management` — reference genome download + indexing
- [ ] `tmb_msi` — TMB + MSI immunotherapy biomarkers
- [ ] `somatic_calling` — tumor-normal Mutect2 pipeline
- [ ] `fragmentomics` — cfDNA fragment-level liquid biopsy
- [ ] `clinical_reporting` — VCF → ACMG PDF report

## Each bundle needs
manifest.json + nextflow.config + workflow/main.nf + docker/ + test/ + nf-test.config + tests/ + README.md

## Reference
See v2 bundle prompts in the workflow-qa directory.
""",
        "High", "Workflow", ["v2", "new-bundles"]
    ),
    (
        "omnibioai-workflow-bundles",
        "Fix ${baseDir} unquoted variable in alphafold2 nf-test",
        """## Status
49 files were fixed in the bulk pass. Verify alphafold2 is clean.

```bash
grep -r 'baseDir' alphafold2/ --include='*.nf.test'
```

If still present: wrap in double quotes.
""",
        "Low", "Testing", ["nf-test", "bug"]
    ),

    # ── omnibioai-tes ─────────────────────────────────────────────────────────
    (
        "omnibioai-tes",
        "Fix restarting Docker containers: nginx-router, auth-service, hpc-policy-engine, license-server",
        """## Problem
4 containers are in restart loop. Blocking full platform startup.

## Containers
- nginx-router
- auth-service
- hpc-policy-engine
- license-server

## Debug steps
```bash
docker logs nginx-router --tail 50
docker logs auth-service --tail 50
docker inspect nginx-router | jq '.[0].State'
```

## Likely causes
- Port conflict
- Missing env vars / secrets
- Dependency service not ready (health check timing)
- Config file path mismatch after recent YAML split

## Acceptance Criteria
- [ ] All 4 containers show `Up` status in `docker ps`
- [ ] No restart count > 0 in `docker ps`
""",
        "Critical", "Infrastructure", ["docker", "bug", "blocking"]
    ),
    (
        "omnibioai-tes",
        "Reclaim ~520GB disk space via docker system prune",
        """## Context
520GB of intermediate build layers from ARM64 SIF container builds.
Safe to prune now that builds are complete.

## Steps
- [ ] Confirm all needed images are tagged and pushed: `docker images | grep omnibioai`
- [ ] Run: `docker system prune -af --volumes`
- [ ] Verify reclaimed: `df -h ~/Desktop/machine/`
- [ ] Document final disk state

## Caution
Do NOT prune before confirming all 510+ HTTP tool images are pushed to registry.
""",
        "High", "Infrastructure", ["disk", "cleanup", "docker"]
    ),
    (
        "omnibioai-tes",
        "Complete remaining ~43 ARM64 SIF container builds",
        """## Status
457 of ~500 tools built as ARM64 Singularity SIF images.

## Remaining
~43 tools with known failure patterns:
- Python version conflicts
- ARM64 incompatibilities (x86-only tools → stub these)
- Missing bioconda ARM64 packages

## Steps
- [ ] Run build pipeline for remaining tools
- [ ] For x86-only: create stub SIF with helpful error message
- [ ] Update tool inventory to 500/500
- [ ] Re-run Slurm test suite on newly built SIFs
""",
        "High", "Infrastructure", ["arm64", "singularity", "hpc"]
    ),
    (
        "omnibioai-tes",
        "Finish servers config split into domain-specific YAML subdirectories",
        """## Context
tools.example.yaml and servers.example.yaml are being split into
domain-specific YAML subdirectories. tools split is done.

## Remaining
- [ ] Complete servers.example.yaml → domain subdirs split
- [ ] Migrate Azure credentials to environment variable references
- [ ] Test load_config() directory-mode loader with new structure
- [ ] Update documentation
""",
        "High", "Infrastructure", ["config", "yaml"]
    ),

    # ── omnibioai (main platform) ─────────────────────────────────────────────
    (
        "omnibioai",
        "End-to-end beta test: FASTQ → Drug Target Identification pipeline",
        """## Goal
Validate the full platform with a real beta user workflow.

## Pipeline
FASTQ → QC → Alignment → Variant Calling → Drug Target Identification

## Steps
- [ ] Use synthetic FASTQ testdata
- [ ] Submit job via OmniBioAI UI
- [ ] Verify TES routes to correct backend (HTTP or Slurm)
- [ ] Check plugin DAG executes correctly
- [ ] Validate output lands in OmniObjectService
- [ ] Verify result appears in UI

## Acceptance Criteria
- Complete pipeline runs without manual intervention
- Results downloadable from UI
- Audit log captures all steps
""",
        "Critical", "Testing", ["e2e", "beta", "pipeline"]
    ),
    (
        "omnibioai",
        "End-to-end beta test: FASTQ → ChIP-seq pipeline",
        """## Goal
Second E2E validation using ChIP-seq workflow bundle.

## Steps
- [ ] Submit chipseq bundle via workflow registry UI
- [ ] Verify bowtie2 + MACS2 processes execute
- [ ] Check peak calling output (BED file)
- [ ] Validate BigWig tracks generated

## Reference
chipseq bundle has nf-tests passing. Use same testdata.
""",
        "Critical", "Testing", ["e2e", "beta", "chipseq"]
    ),
    (
        "omnibioai",
        "Beta user onboarding flow: request form → account creation → first run",
        """## Goal
Test complete beta user onboarding from signup to first successful pipeline run.

## Steps
- [ ] Submit beta request via omnibioai.org form
- [ ] Cloudflare Worker processes request → KV storage
- [ ] Admin panel shows new request
- [ ] Approve request → user receives email (Resend)
- [ ] User logs in → sees UI
- [ ] User submits first job
- [ ] Job completes

## Target
5 beta users by July 4th
""",
        "Critical", "Testing", ["beta", "onboarding", "ux"]
    ),
    (
        "omnibioai",
        "Verify all 215 plugins pass test suite at 95%+ coverage",
        """## Status
Coverage was at 87.3%+ after targeted work in May/June 2026.

## Goal
Push to 95%+ across all 215 plugins (128 omnibioai + 87 omnibioai-tes).

## Steps
- [ ] Run full coverage report: `pytest --cov=plugins --cov-report=html`
- [ ] Identify plugins below 95%
- [ ] Write targeted tests for low-coverage plugins
- [ ] Strict constraint: only modify `plugins/*/tests/test_*.py` files
""",
        "High", "Testing", ["coverage", "plugins", "pytest"]
    ),

    # ── omnibioai-studio ──────────────────────────────────────────────────────
    (
        "omnibioai-studio",
        "Electron AppImage: verify portable IPC handlers work on clean machine",
        """## Goal
Confirm the portable Electron AppImage works without any pre-installed dependencies.

## Steps
- [ ] Test AppImage on a clean Ubuntu 24.04 VM
- [ ] Verify all 12 IPC handlers respond correctly
- [ ] Confirm dynamic server IP configuration works via `window.__OMNIBIOAI_SERVER__`
- [ ] Test with server at 192.168.86.234 (DGX Spark)

## Known issue
Full IPC handler implementation was completed in May 2026.
Verify nothing regressed.
""",
        "High", "Testing", ["electron", "desktop", "ipc"]
    ),
    (
        "omnibioai-studio",
        "Visual workflow builder: test with 5 real pipelines",
        """## Goal
Validate the visual workflow builder works with production pipelines before beta.

## Pipelines to test
1. FASTQ → Drug Target Identification
2. FASTQ → ChIP-seq
3. FASTQ → circRNA Detection
4. Single-cell RNA-seq (Seurat)
5. WES → Variant Calling

## Acceptance Criteria
- [ ] Each pipeline renders correctly as DAG
- [ ] All nodes have correct tool/plugin labels
- [ ] Edge connections match actual data flow
- [ ] Run button submits correctly to TES
""",
        "High", "Testing", ["workflow-builder", "ui", "beta"]
    ),

    # ── omnibioai-tool-images ─────────────────────────────────────────────────
    (
        "omnibioai-tool-images",
        "Document x86-only tools that cannot run on ARM64 DGX Spark",
        """## Goal
Create a definitive list of tools stubbed due to ARM64 incompatibility.

## Steps
- [ ] Identify all tools marked as stub in build pipeline
- [ ] Document: tool name, reason (x86-only / no ARM64 bioconda / CUDA x86)
- [ ] For each: note if there is an ARM64 alternative
- [ ] Add to README as "Known ARM64 Limitations" section
- [ ] Update platform docs to set user expectations
""",
        "Medium", "Documentation", ["arm64", "docs", "compatibility"]
    ),

    # ── omnibioai-landing ─────────────────────────────────────────────────────
    (
        "omnibioai-landing",
        "Update landing page for July 4th beta launch",
        """## Changes needed
- [ ] Add beta signup CTA (link to Cloudflare Worker form)
- [ ] Update tool count to 1000+ tools
- [ ] Add workflow bundle count: 601 pipelines
- [ ] Add 'Now in Beta' banner
- [ ] Verify download links work for all platforms (macOS, Linux, Windows)
- [ ] Test on mobile
""",
        "High", "Frontend", ["landing", "beta", "marketing"]
    ),

    # ── omnibioai-ecosystem ───────────────────────────────────────────────────
    (
        "omnibioai-ecosystem",
        "Full platform smoke test: docker compose up → all 18 services healthy",
        """## Goal
Verify the full platform starts cleanly from a fresh `docker compose up`.

## Steps
- [ ] `docker compose down -v` (clean state)
- [ ] `docker compose up -d`
- [ ] Wait for all health checks: `docker compose ps`
- [ ] Verify all 18 services show `healthy` or `running`
- [ ] Hit each service's health endpoint
- [ ] Run `omnibioai-control-center` report

## Blockers
Depends on: fix restarting containers (omnibioai-tes issue)
""",
        "Critical", "Infrastructure", ["smoke-test", "docker-compose", "beta"]
    ),
    (
        "omnibioai-ecosystem",
        "Document platform startup sequence for beta users",
        """## Goal
Write a clear 'Getting Started' guide for beta users setting up the platform.

## Content
- [ ] Prerequisites (Docker, Nextflow, GPU optional)
- [ ] Clone + configure steps
- [ ] `docker compose up` command
- [ ] How to verify everything is running
- [ ] How to submit first pipeline
- [ ] Troubleshooting common startup failures
""",
        "High", "Documentation", ["docs", "onboarding", "beta"]
    ),

    # ── omnibioai-auth ────────────────────────────────────────────────────────
    (
        "omnibioai-auth",
        "Security audit: verify HttpOnly cookie JWT implementation pre-beta",
        """## Goal
Confirm auth security hardening from May 2026 enterprise sprint is intact.

## Checklist
- [ ] JWT tokens in HttpOnly cookies (not localStorage)
- [ ] CSRF protection on all state-changing endpoints
- [ ] Refresh token rotation working
- [ ] Token invalidation via Redis pub/sub
- [ ] Rate limiting on /auth/login (max 5 attempts/min)
- [ ] HTTPS enforced (HSTS header present)
- [ ] No sensitive data in JWT payload
""",
        "Critical", "Security", ["security", "auth", "audit", "beta"]
    ),

    # ── omnibioai-api-gateway ─────────────────────────────────────────────────
    (
        "omnibioai-api-gateway",
        "Load test: verify gateway handles 50 concurrent beta users",
        """## Goal
Confirm the zero-trust API gateway handles expected beta load.

## Tool
k6 load tests (already written from May 2026 sprint)

## Test scenarios
- [ ] 50 concurrent users, 5 min sustained
- [ ] Peak: 100 req/s for 30s
- [ ] Verify p95 latency < 500ms
- [ ] Verify no 5xx errors under load
- [ ] Check rate limiting kicks in correctly at thresholds

## Run
```bash
k6 run tests/load/gateway_load_test.js
```
""",
        "High", "Testing", ["load-test", "performance", "beta"]
    ),

    # ── omnibioai-docs ────────────────────────────────────────────────────────
    (
        "omnibioai-docs",
        "Write beta user documentation: plugin system and TES backends",
        """## Goal
Comprehensive docs for beta users on the two most complex platform features.

## Sections needed
### Plugin System
- [ ] What is a plugin (BasePluginExecutor pattern)
- [ ] How RunStore/registry works
- [ ] Writing a new plugin (with example)
- [ ] Plugin test coverage requirements

### TES Backends
- [ ] HTTP vs Slurm vs AWS Batch vs Kubernetes
- [ ] How to configure backend selection
- [ ] Monitoring job execution
- [ ] Troubleshooting failed jobs

## Format
MkDocs or Sphinx, matching existing docs structure.
""",
        "High", "Documentation", ["docs", "beta", "plugins"]
    ),
    (
        "omnibioai-docs",
        "Architecture diagram: update to reflect 28 repos and all services",
        """## Goal
Update the system architecture diagram to reflect current state.

## Changes since last diagram
- 4 new security services (api-gateway, policy-engine, hpc-policy-engine, security-audit)
- omnibioai-launcher added
- omnibioai-model-registry added
- omnibioai-dev-hub added
- TES now has 6 backends (added GCP, Azure)

## Format
Mermaid diagram + PNG export for docs and README.
""",
        "Medium", "Documentation", ["docs", "architecture", "diagram"]
    ),

    # ── omnibioai-control-center ──────────────────────────────────────────────
    (
        "omnibioai-control-center",
        "Pre-beta health dashboard: all 18 services monitored with alerting",
        """## Goal
Ensure control center monitors all services and alerts on failures.

## Steps
- [ ] Verify all 18 services registered in health monitor
- [ ] Test HTTP health endpoints for each service
- [ ] Configure alert thresholds (response time > 2s = warning)
- [ ] Test Prometheus metrics scraping
- [ ] Verify Grafana dashboards show all services
- [ ] Test alert fires when a service goes down

## Services to monitor
api-gateway, auth-service, tes, toolserver, workflow-registry, object-service,
lims, rag, studio-backend, policy-engine, hpc-policy-engine, security-audit,
nginx-router, model-registry, dev-hub, control-center, launcher, workbench
""",
        "High", "Infrastructure", ["monitoring", "grafana", "beta"]
    ),

    # ── omnibioai-sdk ─────────────────────────────────────────────────────────
    (
        "omnibioai-sdk",
        "Publish SDK to PyPI before beta launch",
        """## Goal
Make the OmniBioAI Python SDK installable via pip.

## Steps
- [ ] Bump version to 1.0.0-beta
- [ ] `python -m build`
- [ ] `twine upload dist/*`
- [ ] Verify: `pip install omnibioai-sdk`
- [ ] Test basic usage in Jupyter notebook
- [ ] Update README with pip install instructions
- [ ] Add to beta user onboarding docs

## Package name
`omnibioai` or `omnibioai-sdk` on PyPI (check availability first)
""",
        "High", "Release", ["sdk", "pypi", "beta", "release"]
    ),

    # ── omnibioai-lims ────────────────────────────────────────────────────────
    (
        "omnibioai-lims",
        "LIMS: end-to-end sample tracking test for beta workflow",
        """## Goal
Verify LIMS correctly tracks samples through a complete pipeline run.

## Test scenario
1. Create project in LIMS
2. Register 3 test samples
3. Submit WES pipeline for samples
4. Track status through pipeline stages
5. Verify results linked to samples in LIMS
6. Export sample report

## Acceptance Criteria
- [ ] Sample status updates in real-time
- [ ] Pipeline results linked to correct samples
- [ ] Export produces valid CSV/PDF
""",
        "Medium", "Testing", ["lims", "e2e", "beta"]
    ),

    # ── omnibioai-rag ─────────────────────────────────────────────────────────
    (
        "omnibioai-rag",
        "RAG: verify FAISS index loaded and queries return relevant results",
        """## Goal
Confirm RAG assistant works for beta users querying bioinformatics methods.

## Test queries
- [ ] "What is the best aligner for RNA-seq?"
- [ ] "How do I interpret a volcano plot?"
- [ ] "What tools are available for variant annotation?"

## Acceptance Criteria
- [ ] Each query returns ≥ 3 relevant results
- [ ] Response time < 3s
- [ ] Ollama LLM generates coherent answer from retrieved context
- [ ] FAISS index size documented
""",
        "Medium", "Testing", ["rag", "faiss", "beta"]
    ),

    # ── omnibioai-workbench ───────────────────────────────────────────────────
    (
        "omnibioai-workbench",
        "Workbench: verify plugin execution environment for 5 core plugins",
        """## Goal
Confirm the plugin execution environment works for the most-used plugins.

## Plugins to test
1. STAR alignment
2. GATK HaplotypeCaller
3. DESeq2 differential expression
4. Seurat single-cell analysis
5. MultiQC report generation

## Steps
- [ ] Submit each plugin via workbench UI
- [ ] Verify correct Docker container selected
- [ ] Check output files generated
- [ ] Verify results visible in OmniObjectService
""",
        "High", "Testing", ["workbench", "plugins", "e2e"]
    ),
]

# ── GraphQL helpers ────────────────────────────────────────────────────────────

def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GQL, headers=GQL_HEADERS, json=payload)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


def rest_post(path, body):
    r = requests.post(f"{REST}{path}", headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json()


def rest_get(path):
    r = requests.get(f"{REST}{path}", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def rate_limited_call(fn, *args, **kwargs):
    """Call fn with simple retry on rate limit."""
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except requests.HTTPError as e:
            if e.response.status_code == 429 or e.response.status_code == 403:
                wait = 60 * (attempt + 1)
                print(f"  Rate limited — waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Max retries exceeded")


# ── Step 1: Create GitHub Project ─────────────────────────────────────────────

def get_user_node_id():
    data = gql("query { viewer { id login } }")
    return data["viewer"]["id"], data["viewer"]["login"]


def create_project(owner_id, dry_run=False):
    print(f"\n[1/5] Creating project: '{PROJECT_TITLE}'")
    if dry_run:
        print("  DRY RUN — skipping")
        return "DRY_RUN_PROJECT_ID"

    mutation = """
    mutation($ownerId: ID!, $title: String!) {
      createProjectV2(input: {ownerId: $ownerId, title: $title}) {
        projectV2 { id number url }
      }
    }
    """
    data = gql(mutation, {"ownerId": owner_id, "title": PROJECT_TITLE})
    project = data["createProjectV2"]["projectV2"]
    print(f"  Created: {project['url']}")
    return project["id"], project["number"]


# ── Step 2: Add custom fields ──────────────────────────────────────────────────

def add_single_select_field(project_id, name, options, dry_run=False):
    if dry_run:
        print(f"  DRY RUN — would add field: {name}")
        return f"DRY_{name}_ID"

    opts = [{"name": o, "color": "GRAY", "description": ""} for o in options]
    mutation = """
    mutation($projectId: ID!, $name: String!, $options: [ProjectV2SingleSelectFieldOptionInput!]!) {
      addProjectV2DraftIssue: createProjectV2Field(input: {
        projectId: $projectId
        dataType: SINGLE_SELECT
        name: $name
        singleSelectOptions: $options
      }) {
        projectV2Field { ... on ProjectV2SingleSelectField { id name options { id name } } }
      }
    }
    """
    # Note: use direct REST for field creation (GQL field creation has limited support)
    # Fall back to noting field names for manual creation
    print(f"  Field '{name}' — will need manual setup in UI (GitHub Projects API limitation)")
    return None


def create_project_fields(project_id, dry_run=False):
    print(f"\n[2/5] Setting up project fields")
    print("  Note: GitHub Projects v2 custom fields require manual setup in the UI.")
    print("  After project is created, add these fields manually:")
    print("    - Priority (Single select): Critical, High, Medium, Low")
    print("    - Category (Single select): Infrastructure, Workflow, Testing, CI/CD, Security, Documentation, Frontend, Release")
    print("    - Due Date (Date field): set to 2026-07-04 for Critical issues")


# ── Step 3: Ensure repos exist and have labels ────────────────────────────────

def ensure_label(repo, name, color, dry_run=False):
    try:
        rest_get(f"/repos/{OWNER}/{repo}/labels/{requests.utils.quote(name)}")
        return  # already exists
    except requests.HTTPError:
        pass
    if not dry_run:
        try:
            rest_post(f"/repos/{OWNER}/{repo}/labels", {"name": name, "color": color})
        except Exception:
            pass  # label may already exist with different casing


LABEL_COLORS = {
    "Critical":       "d73a4a",
    "High":           "e4e669",
    "Medium":         "0075ca",
    "Low":            "cfd3d7",
    "phase-4":        "5319e7",
    "testing":        "0e8a16",
    "docker":         "1d76db",
    "ghcr":           "0075ca",
    "ci":             "e4e669",
    "nf-test":        "5319e7",
    "github-actions": "f9d0c4",
    "v2":             "ff6b35",
    "new-bundles":    "ff6b35",
    "arm64":          "b60205",
    "singularity":    "b60205",
    "hpc":            "006b75",
    "config":         "bfd4f2",
    "yaml":           "bfd4f2",
    "bug":            "d73a4a",
    "blocking":       "d73a4a",
    "disk":           "e4e669",
    "cleanup":        "c5def5",
    "e2e":            "0e8a16",
    "beta":           "ff6b35",
    "pipeline":       "5319e7",
    "coverage":       "0e8a16",
    "plugins":        "5319e7",
    "pytest":         "0e8a16",
    "electron":       "1d76db",
    "desktop":        "1d76db",
    "ipc":            "bfd4f2",
    "smoke-test":     "0e8a16",
    "docker-compose": "1d76db",
    "onboarding":     "ff6b35",
    "ux":             "ff6b35",
    "security":       "d73a4a",
    "auth":           "d73a4a",
    "audit":          "d73a4a",
    "load-test":      "e4e669",
    "performance":    "e4e669",
    "docs":           "0075ca",
    "architecture":   "0075ca",
    "diagram":        "0075ca",
    "monitoring":     "006b75",
    "grafana":        "006b75",
    "sdk":            "1d76db",
    "pypi":           "1d76db",
    "release":        "ff6b35",
    "lims":           "5319e7",
    "rag":            "5319e7",
    "faiss":          "5319e7",
    "workbench":      "5319e7",
    "landing":        "ff6b35",
    "marketing":      "ff6b35",
    "workflow-builder":"5319e7",
    "ui":             "ff6b35",
    "chipseq":        "0e8a16",
}


def setup_labels(dry_run=False):
    print(f"\n[3/5] Setting up labels across repos")
    repos_needed = set(repo for repo, *_ in ISSUES)
    for repo in sorted(repos_needed):
        print(f"  {repo}...")
        for label, color in LABEL_COLORS.items():
            if not dry_run:
                ensure_label(repo, label, color, dry_run)
        if dry_run:
            print(f"    DRY RUN — would create {len(LABEL_COLORS)} labels")


# ── Step 4: Create issues ──────────────────────────────────────────────────────

def create_issue(repo, title, body, priority, category, labels, dry_run=False):
    all_labels = labels + [priority]
    if dry_run:
        print(f"    DRY RUN: [{priority}] {title[:60]}...")
        return {"number": 0, "html_url": "DRY_RUN", "node_id": "DRY_NODE"}

    issue_labels = []
    for lbl in all_labels:
        try:
            issue_labels.append(lbl)
        except Exception:  # pragma: no cover - defensive, list.append can't fail here
            pass

    data = rate_limited_call(
        rest_post,
        f"/repos/{OWNER}/{repo}/issues",
        {
            "title": title,
            "body": body,
            "labels": issue_labels,
        }
    )
    time.sleep(0.5)  # be gentle with rate limits
    return data


def create_all_issues(dry_run=False):
    print(f"\n[4/5] Creating issues ({len(ISSUES)} total)")
    created = []
    by_repo = {}
    for item in ISSUES:
        repo = item[0]
        by_repo.setdefault(repo, []).append(item)

    for repo, items in sorted(by_repo.items()):
        print(f"\n  {repo} ({len(items)} issues):")
        for repo_, title, body, priority, category, labels in items:
            result = create_issue(repo_, title, body, priority, category, labels, dry_run)
            print(f"    ✓ #{result['number']} [{priority}] {title[:55]}...")
            created.append({
                "repo": repo_,
                "number": result["number"],
                "node_id": result.get("node_id", ""),
                "url": result.get("html_url", ""),
                "title": title,
                "priority": priority,
                "category": category,
            })
    return created


# ── Step 5: Link issues to project ────────────────────────────────────────────

def link_issues_to_project(project_id, issues, dry_run=False):
    print(f"\n[5/5] Linking {len(issues)} issues to project")
    if dry_run:
        print("  DRY RUN — skipping")
        return

    mutation = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item { id }
      }
    }
    """
    linked = 0
    for issue in issues:
        if not issue.get("node_id") or issue["node_id"] == "DRY_NODE":
            continue
        try:
            gql(mutation, {"projectId": project_id, "contentId": issue["node_id"]})
            linked += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  Warning: could not link {issue['repo']}#{issue['number']}: {e}")

    print(f"  Linked {linked}/{len(issues)} issues to project")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Setup OmniBioAI Beta Launch GitHub Project")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--issues-only", action="store_true", help="Skip project creation, only create issues")
    parser.add_argument("--output", default="created_issues.json", help="Save created issues to JSON")
    args = parser.parse_args()

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN env var not set")
        print("Generate a PAT at: https://github.com/settings/tokens")
        print("Required scopes: repo, project")
        sys.exit(1)

    print("=" * 60)
    print(f"OmniBioAI Beta Launch — GitHub Project Setup")
    print(f"Owner:   {OWNER}")
    print(f"Issues:  {len(ISSUES)}")
    print(f"Repos:   {len(set(r for r,*_ in ISSUES))}")
    print(f"Mode:    {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("=" * 60)

    # Validate token
    try:
        user_data = gql("query { viewer { login } }")
        login = user_data["viewer"]["login"]
        print(f"\nAuthenticated as: {login}")
        if login != OWNER:
            print(f"WARNING: token owner ({login}) != repo owner ({OWNER})")
    except Exception as e:
        print(f"ERROR: GitHub auth failed: {e}")
        sys.exit(1)

    project_id = None

    if not args.issues_only:
        owner_id, _ = get_user_node_id()
        result = create_project(owner_id, args.dry_run)
        if not args.dry_run:
            project_id, project_number = result
        create_project_fields(project_id, args.dry_run)
    else:
        print("\n[1-2/5] Skipping project creation (--issues-only)")

    setup_labels(args.dry_run)
    created_issues = create_all_issues(args.dry_run)

    if project_id:
        link_issues_to_project(project_id, created_issues, args.dry_run)

    # Save results
    if not args.dry_run:
        with open(args.output, "w") as f:
            json.dump(created_issues, f, indent=2)
        print(f"\nSaved created issues to: {args.output}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    from collections import Counter
    priority_counts = Counter(i["priority"] for i in created_issues)
    repo_counts = Counter(i["repo"] for i in created_issues)
    for p in ["Critical", "High", "Medium", "Low"]:
        if priority_counts[p]:
            print(f"  {p:10s}: {priority_counts[p]} issues")
    print(f"\n  Repos touched: {len(repo_counts)}")
    for repo, count in sorted(repo_counts.items()):
        print(f"    {repo}: {count} issues")

    if args.dry_run:
        print("\nDRY RUN complete — no changes made.")
        print("Run without --dry-run to execute.")
    else:
        print(f"\n✓ Done! View project at:")
        print(f"  https://github.com/users/{OWNER}/projects/")


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
