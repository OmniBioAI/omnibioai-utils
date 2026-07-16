#!/usr/bin/env bash
set -uo pipefail

# Rebuilds + pushes every plugin image whose Dockerfile builds FROM one of the
# shared omnibioai-ml-* base images. These only had their FROM line text
# updated by the man4ish->omnibioai migration - the actual image bytes still
# need a real `docker build` to pick up the new base layer.
#
# Run this from the repo root (omnibioai-workbench / omnibioai checkout).

PLUGINS=(
  microbiome_taxonomic_classifier
  structural_variant_classifier
  histopath_tumor_classifier
  prs_deep_predictor
  crispr_guide_efficiency_predictor
  admet_property_predictor
  scrna_celltype_classifier
  spatial_domain_classifier
  atac_accessibility_classifier
  ms_spectrum_classifier
  protein_function_classifier_esm
  nanopore_methylation_caller
  drug_target_binding_affinity
  histopath_nuclei_segmentation
  ptm_site_predictor
  scrna_batch_integration_ae
  variant_pathogenicity_classifier
)

if [ ! -d "plugins" ]; then
  echo "!! 'plugins' directory not found in current dir. Run this from the repo root."
  exit 1
fi

LOG_FILE="./rebuild_ml_plugins.log"
touch "$LOG_FILE"

already_done() {
  grep -Fxq "$1" "$LOG_FILE" 2>/dev/null
}

mark_done() {
  echo "$1" >> "$LOG_FILE"
}

built=0
skipped=0
failed=0
failed_list=()

for plugin in "${PLUGINS[@]}"; do
  dockerfile="plugins/${plugin}/Dockerfile"
  executor="plugins/${plugin}/executor.py"

  if [ ! -f "$dockerfile" ]; then
    echo "!! $dockerfile not found, skipping $plugin"
    failed=$((failed+1))
    failed_list+=("$plugin (no Dockerfile)")
    continue
  fi

  # Pull the exact tag this plugin expects from its executor.py
  if [ -f "$executor" ]; then
    tag=$(grep -oP 'DOCKER_IMAGE\s*=\s*"\K[^"]+' "$executor" | head -1)
  fi
  if [ -z "${tag:-}" ]; then
    echo "!! Could not determine DOCKER_IMAGE tag for $plugin from $executor, skipping"
    failed=$((failed+1))
    failed_list+=("$plugin (no tag found)")
    continue
  fi

  if already_done "$tag"; then
    echo "[skip] $plugin -> $tag already built+pushed this session"
    skipped=$((skipped+1))
    continue
  fi

  # Auto-detect a --platform flag if the Dockerfile's header comments or the
  # plugin's README document one (several of these were built for amd64
  # explicitly since the ML base images are arm64-native).
  platform_flag=""
  for src in "$dockerfile" "plugins/${plugin}/README.md"; do
    if [ -f "$src" ]; then
      match=$(grep -oP -- '--platform[= ]\K[a-zA-Z0-9/]+' "$src" | head -1)
      if [ -n "$match" ]; then
        platform_flag="--platform $match"
        break
      fi
    fi
  done

  echo ""
  echo "=== Building $plugin -> $tag ${platform_flag:+(with $platform_flag)} ==="
  if docker build $platform_flag -f "$dockerfile" -t "$tag" .; then
    echo "  build ok, pushing..."
    if docker push "$tag"; then
      mark_done "$tag"
      built=$((built+1))
    else
      echo "  !! push failed for $tag"
      failed=$((failed+1))
      failed_list+=("$plugin (push failed)")
    fi
  else
    echo "  !! build failed for $plugin"
    failed=$((failed+1))
    failed_list+=("$plugin (build failed)")
  fi
done

echo ""
echo "==== Summary ===="
echo "Built + pushed: $built"
echo "Skipped (already done): $skipped"
echo "Failed: $failed"
if [ ${#failed_list[@]} -gt 0 ]; then
  echo ""
  echo "Failed plugins:"
  for f in "${failed_list[@]}"; do
    echo "  - $f"
  done
fi
echo ""
echo "Safe to re-run - already-pushed tags are skipped via $LOG_FILE."