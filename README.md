# omnibioai-utils

Developer utilities, automation scripts, and ecosystem management tools for the OmniBioAI platform. Covers stack lifecycle management, build automation, CI/CD control, coverage reporting, GitHub project setup, and ecosystem health monitoring.

---

## Scripts

### Stack Management

| Script | Description |
|--------|-------------|
| `omnibioai-up.sh` | Starts the core OmniBioAI development services in a tmux session (core, TES, ToolServer, and LIMS) |
| `omnibioai-down.sh` | Tears down the tmux session and stops all services |
| `start_all.sh` | Starts all OmniBioAI services sequentially |
| `start_stack_tmux.sh` | Launches the full stack in named tmux windows with port management |
| `start-all-uis.sh` | Starts all React frontend UIs and backend APIs |
| `stop-all-uis.sh` | Stops all running React UI dev servers |
| `smoke_test_stack.sh` | Runs HTTP health checks against all core service endpoints |

### Build Automation

| Script | Description |
|--------|-------------|
| `build-all-new.sh` | Builds the listed service images locally using each repo's `Dockerfile.new`; it does not push them |
| `build_all_tools.sh` | Builds all bioinformatics tool images and pushes to ECR and GHCR |
| `build_cython.sh` | Compiles all high-priority Cython files across repos before Docker builds |

### Ecosystem Management

| Script | Description |
|--------|-------------|
| `ecosystem_status.sh` | Reports git branch and clean/dirty status across all discovered repositories under the machine root |
| `check_unpushed_work.sh` | Focused specifically on "is anything at risk of being lost" — non-zero exit if any repo has unpushed *commits* (untracked/modified files alone don't fail it; unpushed commits are the real risk). Supports `--root`, `--json`, `--quiet` |
| `backup-system-state.sh` | Daily backup of machine state that isn't in git — `.env` files, cloudflared config, systemd units, crontab — deliberately excludes `.ssh/`, `.aws/`, `.kube/`, `.gnupg/`, and other high-blast-radius credential paths |
| `clock_count.sh` | Counts lines of code across the full ecosystem using `cloc` |
| `run_coverage.sh` | Aggregates pytest coverage reports across all repos into `out/coverage/` |
| `disable_cicd.sh` | Moves `.github/workflows` to `workflows_disabled` across all repos |
| `update_descriptions.sh` | Updates GitHub repo descriptions for all OmniBioAI repos via API |
| `update_topics.sh` | Sets GitHub topics for all OmniBioAI repos via `gh api` |

### Project Setup

| Script / File | Description |
|---------------|-------------|
| `setup_beta_project.py` | Creates GitHub Project "OmniBioAI Beta Launch" with Board + Roadmap views, custom fields (Priority, Category, Repo, Due Date), and issues across all repos linked to the project |
| `build-results.txt` | Latest build results log |

### PubMed Data Pipeline

| Script | Description |
|--------|-------------|
| [`pubmed/download_pubmed.sh`](pubmed/download_pubmed.sh) | Loads PubMed abstracts into the RAG FAISS index via `ragbio.utils.rag_data_loader`, run inside the `omnibioai-studio-rag-1` container for a set of predefined disease/topic studies |
| [`pubmed/split_general_corpus.sh`](pubmed/split_general_corpus.sh) | Splits the `_general_corpus` abstract directory (tens of millions of files) into fixed-size chunk subdirectories so `embedding_engine` can process them per-chunk without code changes |
| `test_split_on_sample.sh` | Dry-run of [`pubmed/split_general_corpus.sh`](pubmed/split_general_corpus.sh) against a small sample copied into `/tmp`; verifies chunk counts match before running the real split on the full corpus |
| [`pubmed/run_chunks.sh`](pubmed/run_chunks.sh) | Runs `embedding_engine.py` over each `_general_corpus_chunk*` directory with bounded concurrency, to avoid the OOM/swap issues seen when running unbounded |
| [`pubmed/sync_pubmed_updates.py`](pubmed/sync_pubmed_updates.py) | Daily incremental PubMed sync — pulls new/updated files from the NCBI FTP update feed, updates existing abstract JSON files in place, and tracks progress in `sync_state.json` |
| [`pubmed/create_new_chunks.py`](pubmed/create_new_chunks.py) | Creates new `_general_corpus_chunk*` directories from abstracts updated by [`pubmed/sync_pubmed_updates.py`](pubmed/sync_pubmed_updates.py), continuing the existing chunk numbering |
| [`pubmed/reindex_one_shard.py`](pubmed/reindex_one_shard.py) | Reindexes one PubMed shard with 1024-D mxbai embeddings on CUDA/FP16 into a separate staging index |
| [`pubmed/reindex_one_shard_ollama.py`](pubmed/reindex_one_shard_ollama.py) | Reindexes one PubMed shard with 1024-D mxbai embeddings through Ollama into a separate staging index |
| [`pubmed/test_index.py`](pubmed/test_index.py) | Runs an Ollama embedding benchmark on one PubMed chunk; an operational script, not a pytest unit test |
| [`pubmed/reindex_1024_mac.py`](pubmed/reindex_1024_mac.py) | Resumable MPS worker for general corpus and domain indexes, with embedding checkpoints, verified Hugging Face uploads, and transient-file cleanup |

See [pubmed/README.md](pubmed/README.md) for discovery, data-root configuration,
checkpoint recovery, and CLI examples. PubMed source data and generated indexes
remain outside this repository.

### Reference Data

| Script | Description |
|--------|-------------|
| `download_references.py` | Downloads reference genomes, variant sets, and databases by species/assembly, with resume support and a JSON dataset registry; supports `--dry-run` and `--status` |
| `restore_reference_data.sh` | Intended to restore reference data, SIFs, and indexes — currently an empty file (0 bytes), not yet implemented |

### Container Image Management

| Script | Description |
|--------|-------------|
| `migrate_public_images.sh` | Copies public container images from `ghcr.io/man4ish` to `ghcr.io/omnibioai` via `skopeo`, skipping private packages |
| `set_public_visibility.sh` | Bulk-sets `visibility=public` on `omnibioai` org container packages via the GitHub API |
| `set_packages_public.sh` | Lists all container packages in the `omnibioai` org and PATCHes any non-public ones to public |
| `make_public_browser.py` | Playwright browser automation to set package visibility to public, for cases the REST API doesn't support (no visibility-update endpoint) |
| `push_sifs.sh` | Pushes local `.sif` Singularity images to `ghcr.io/omnibioai/omnibioai-sif/<name>:arm64` via `oras`, skipping images already pushed |
| `rebuild_ml_base_plugins.sh` | Rebuilds and pushes plugin images whose Dockerfiles build `FROM` a shared `omnibioai-ml-*` base image, after a base-image migration |
| `update_ghcr_refs.sh` | Finds, and optionally replaces (with `.bak` backups), lingering `ghcr.io/man4ish` references across a repo |
| `sweep_all_repos.sh` | Runs `update_ghcr_refs.sh` in dry-run mode across all sibling OmniBioAI repos and summarizes which ones still reference `ghcr.io/man4ish` |
| `verify_migration.sh` | Compares packages and tags between the `man4ish` and `omnibioai` GHCR namespaces and writes a migration verification report |
| `delete_old_packages.sh` | Deletes packages listed in `old_packages.txt` (produced by `verify_migration.sh`) from the `man4ish` namespace; dry-run by default, requires typed `DELETE` confirmation |
| `delete_packages_browser.py` | Playwright browser automation fallback for bulk package deletion, for use when the API token lacks `delete:packages` scope |
| `check_base_images.sh` | Compares the ~12 shared `omnibioai-base`/`omnibioai-ml-*` base images against ghcr.io/omnibioai directly (no pagination needed); flags any local build newer than the registry's last push |
| `check_platform_workflows.sh` | Checks GitHub Actions run health (not local push state) for the ~20 platform/system repos whose CI workflow builds+pushes their Docker image; distinguishes disabled/stale/failing/OK |
| `check_plugin_image_sync.sh` | Paginated, rate-limit-aware read-only gap check between locally-built `omnibioai-plugin-*` images and their ghcr.io/omnibioai packages; no local push log exists for plugins, so this rebuilds the comparison each run and logs clearly if a sweep is cut short by rate limiting |

### Testing & Evaluation

| Script | Description |
|--------|-------------|
| `agent_tool_selection_eval.py` | Evaluates how reliably a local Ollama model selects the correct tool and fills valid arguments from a semantically-narrowed shortlist drawn from the TES tool corpus |
| `prepare_real_data_facs.py` | Builds a real ClinVar-derived training set (CADD, gnomAD, GERP, PhyloP, SIFT, PolyPhen features) for the `variant_pathogenicity_classifier` plugin, replacing its synthetic 24-row toy dataset |

---

## Usage

### Check ecosystem status
```bash
bash ecosystem_status.sh
# or from machine root:
bash utils/ecosystem_status.sh
```

### Start the full stack
```bash
bash omnibioai-up.sh
```

### Smoke test all services
```bash
bash smoke_test_stack.sh
```

### Build and push all images to GHCR
```bash
bash build-all-new.sh
```

`build-all-new.sh` currently builds and tags images locally; it does not push
them to GHCR despite the historical name of this usage section. Push images
only after reviewing the build results and registry target.

### Run coverage across all repos
```bash
bash run_coverage.sh
# Output: ~/Desktop/machine/out/coverage/
```

### Update all GitHub repo descriptions and topics
```bash
export GITHUB_TOKEN=<your_pat>
bash update_descriptions.sh
bash update_topics.sh
```

### Set up GitHub Beta Launch project
```bash
export GITHUB_TOKEN=<your_pat>
python setup_beta_project.py --dry-run   # preview
python setup_beta_project.py             # execute
```

### Disable CI/CD across all repos
```bash
bash disable_cicd.sh
```

`disable_cicd.sh` moves workflow files, commits the changes, and pushes each
affected repository. Review the target repository list before running it.

### Operations that change external state

The following scripts modify repositories, GitHub metadata, or container
registries and should be reviewed before execution:

- `update_descriptions.sh` edits GitHub repository descriptions.
- `update_topics.sh` changes GitHub repository topics.
- `disable_cicd.sh` commits and pushes workflow changes across repositories.
- `set_public_visibility.sh` and `set_packages_public.sh` change GHCR package visibility.
- `migrate_public_images.sh` copies images between registries.
- `delete_old_packages.sh` and `delete_packages_browser.py` delete packages. Use their dry-run mode first; deletion requires explicit confirmation.

Where supported, use `--dry-run` before applying changes.

---

## Requirements

```bash
# Core shell utilities
sudo apt-get install tmux cloc curl jq lsof

# Container tooling used by selected scripts
# Docker is required for stack/build scripts; skopeo and oras are needed for
# registry/image migration scripts.
docker --version
# Install skopeo and oras separately when using those scripts.

# Python (for setup_beta_project.py)
pip install PyGithub requests

# Only needed by browser fallbacks:
pip install playwright
python -m playwright install chromium

# GitHub CLI (for update_topics.sh, update_descriptions.sh)
gh auth login
```

---

## Related Repositories

- [`omnibioai-studio`](../omnibioai-studio) — Docker Compose stack and desktop environment used by these scripts
- [`omnibioai-control-center`](../omnibioai-control-center) — live health dashboard complementing `smoke_test_stack.sh`
- [`omnibioai-test-data`](../omnibioai-test-data) — test suite run via these build and stack scripts

---

## Scheduled Tasks (Cron)

These are the intended schedules, not an installer. Confirm that each
referenced repository and script exists on the target machine before adding
entries to crontab. Most scripts assume the default root
`~/Desktop/machine`; use their documented arguments or environment variables
where available when deploying on another host.

| Time | Script | Purpose |
|------|--------|---------|
| 2AM daily | [`run_coverage_host.py`](../omnibioai-control-center/scripts/run_coverage_host.py) | Test coverage |
| 3AM daily | [`pubmed/sync_pubmed_updates.py`](pubmed/sync_pubmed_updates.py) | PubMed sync |
| 4AM daily | [`backup-mysql.sh`](../omnibioai-studio/scripts/backup-mysql.sh) | Database backup |
| Hourly | [`check_and_reindex.sh`](../omnibioai-dev-hub/scripts/check_and_reindex.sh) | Re-index check |
| 8AM daily | `check_base_images.sh` | Base image freshness vs. ghcr.io/omnibioai |
| 8:15AM daily | `check_platform_workflows.sh` | Platform-image CI/CD workflow health |
| 8:30AM daily | `check_plugin_image_sync.sh` | Plugin image vs. registry gap sweep |
