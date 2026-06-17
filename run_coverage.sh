#!/bin/bash
MACHINE=~/Desktop/machine
OUT=$MACHINE/out/coverage
mkdir -p $OUT
find $MACHINE -name "coverage.json" -exec chmod 666 {} \; 2>/dev/null

# Sidecar file to store actual test counts from runs (not --co estimates)
TEST_COUNTS=$OUT/test_counts.json
echo "{}" > $TEST_COUNTS

# Helper: extract passed test count from pytest tail output
# e.g. "32 failed, 6883 passed, 19 skipped" → 6883
# or   "16159 passed, 8 skipped" → 16159
extract_count() {
  echo "$1" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1
}

# Helper: store count in sidecar JSON
store_count() {
  local repo=$1
  local count=$2
  python3 -c "
import json
f = '$TEST_COUNTS'
d = json.load(open(f))
d['$repo'] = int('${count:-0}')
json.dump(d, open(f,'w'))
" 2>/dev/null
}

# ─── Standard Python repos ────────────────────────────────────────────────────
for repo in omnibioai-tes omnibioai-lims omnibioai-sdk omnibioai-rag \
            omnibioai-toolserver omnibioai-model-registry omnibioai-tool-runtime \
            omnibioai-control-center omnibioai-dev-hub omnibioai-iam-client \
            omnibioai-security-sdk omnibioai-policy-engine omnibioai-security-audit \
            omnibioai-hpc-policy-engine omnibioai-videos omnibioai-auth \
            omnibioai-api-gateway omnibioai-launcher omnibioai-studio; do
  path=$MACHINE/$repo
  [ ! -d "$path" ] && continue
  echo "Running $repo..."
  cd $path
  output=$(python3 -m pytest --cov=. --cov-report=json --tb=no -q 2>/dev/null)
  result=$(echo "$output" | tail -2)
  echo "  $result"
  count=$(extract_count "$result")
  store_count "$repo" "$count"
  [ -f coverage.json ] && cp coverage.json $OUT/$repo.json && echo "  ✅ $repo ($count tests)"
done

# ─── omnibioai — special case: two domains ────────────────────────────────────
echo ""
echo "Running omnibioai (services + plugins)..."
cd $MACHINE/omnibioai

echo "  [1/2] services..."
svc_output=$(python3 -m pytest tests/ \
  --ignore=tests/test_performance_baselines.py \
  --ignore=tests/utils/ \
  --cov=omnibioai/services \
  --cov-report=json:coverage_services.json \
  --tb=no -q 2>/dev/null)
svc_result=$(echo "$svc_output" | tail -2)
svc_count=$(extract_count "$svc_result")
echo "  $svc_result"
[ -f coverage_services.json ] && cp coverage_services.json $OUT/omnibioai-services.json

echo "  [2/2] plugins..."
plg_output=$(python3 -m pytest plugins/ \
  --cov=plugins \
  --cov-report=json:coverage_plugins.json \
  --tb=no -q 2>/dev/null)
plg_result=$(echo "$plg_output" | tail -2)
plg_count=$(extract_count "$plg_result")
echo "  $plg_result"
[ -f coverage_plugins.json ] && cp coverage_plugins.json $OUT/omnibioai-plugins.json

# Store combined omnibioai test count
omni_total=$((${svc_count:-0} + ${plg_count:-0}))
store_count "omnibioai" "$omni_total"
echo "  omnibioai tests: $svc_count (services) + $plg_count (plugins) = $omni_total"

# Merge omnibioai coverage domains into single JSON
python3 - <<'EOF'
import json, os

s = json.load(open("coverage_services.json"))
p = json.load(open("coverage_plugins.json"))

merged_files = {}
merged_files.update(s.get("files", {}))
merged_files.update(p.get("files", {}))

total_stmts   = s["totals"]["num_statements"]  + p["totals"]["num_statements"]
total_covered = s["totals"]["covered_lines"]   + p["totals"]["covered_lines"]
total_missing = s["totals"]["missing_lines"]   + p["totals"]["missing_lines"]
total_pct     = total_covered / total_stmts * 100 if total_stmts else 0

merged = {
    "meta": s.get("meta", {}),
    "files": merged_files,
    "totals": {
        "num_statements":          total_stmts,
        "covered_lines":           total_covered,
        "missing_lines":           total_missing,
        "percent_covered":         round(total_pct, 2),
        "percent_covered_display": f"{total_pct:.2f}%",
    }
}

out = os.path.expanduser("~/Desktop/machine/out/coverage/omnibioai.json")
with open(out, "w") as f:
    json.dump(merged, f, indent=2)
print(f"  ✅ omnibioai merged: {total_covered}/{total_stmts} = {total_pct:.2f}%")
EOF

echo ""
echo "All done! Generating ecosystem report..."
echo ""

# ─── Ecosystem summary ────────────────────────────────────────────────────────
python3 - <<'EOF'
import json, os, glob

out_dir   = os.path.expanduser("~/Desktop/machine/out/coverage")
SKIP      = {"omnibioai-services.json", "omnibioai-plugins.json"}
THRESHOLD = 90.0

# Load actual test counts from sidecar
counts_file = os.path.join(out_dir, "test_counts.json")
try:
    test_counts = json.load(open(counts_file))
except Exception:
    test_counts = {}

files = sorted(glob.glob(f"{out_dir}/*.json"))

rows        = []
eco_stmts   = 0
eco_covered = 0
eco_tests   = 0

for f in files:
    name = os.path.basename(f)
    if name in SKIP or name == "test_counts.json":
        continue
    repo = name.replace(".json", "")
    try:
        data    = json.load(open(f))["totals"]
        stmts   = data["num_statements"]
        covered = data["covered_lines"]
        pct     = data["percent_covered"]
        n_tests = test_counts.get(repo, 0)

        eco_stmts   += stmts
        eco_covered += covered
        eco_tests   += n_tests

        flag = "✅" if pct >= THRESHOLD else ("🔶" if pct >= 80 else "⚠️ ")
        rows.append((flag, repo, stmts, covered, pct, n_tests))

    except Exception as e:
        rows.append(("❓", repo, 0, 0, 0.0, 0))

# Sort by coverage ascending so gaps are obvious
rows.sort(key=lambda x: x[4])

eco_pct = eco_covered / eco_stmts * 100 if eco_stmts else 0

print(f"{'':2} {'Repo':<35} {'Tests':>8} {'Stmts':>10} {'Covered':>10} {'Coverage':>10}")
print("─" * 80)
for flag, repo, stmts, covered, pct, n_tests in rows:
    print(f"{flag} {repo:<35} {n_tests:>8,} {stmts:>10,} {covered:>10,} {pct:>9.2f}%")
print("─" * 80)
print(f"{'':2} {'ECOSYSTEM TOTAL':<35} {eco_tests:>8,} {eco_stmts:>10,} {eco_covered:>10,} {eco_pct:>9.2f}%")
print()
print(f"  Threshold : {THRESHOLD}%  |  ✅ ≥{THRESHOLD}%  🔶 80–{THRESHOLD-0.01:.0f}%  ⚠️  <80%")
print(f"  Tests     : {eco_tests:,} passed")
print(f"  Coverage  : {eco_pct:.2f}% across {eco_stmts:,} statements")

# Flag repos below threshold
below = [(r, p) for _, r, _, _, p, _ in rows if p < THRESHOLD and p > 0]
if below:
    print()
    print(f"  Repos below {THRESHOLD}% ({len(below)}):")
    for r, p in below:
        gap = THRESHOLD - p
        print(f"    {r:<35} {p:.2f}%  (gap: {gap:.2f}%)")
EOF