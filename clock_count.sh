source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ecosystem_path.sh"
cloc \
  "$OMNIBIOAI_ROOT"/omnibioai \
  "$OMNIBIOAI_ROOT"/omnibioai-landing \
  "$OMNIBIOAI_ROOT"/omnibioai-api-gateway \
  "$OMNIBIOAI_ROOT"/omnibioai-tes \
  "$OMNIBIOAI_ROOT"/omnibioai_sdk \
  "$OMNIBIOAI_ROOT"/omnibioai-rag \
  "$OMNIBIOAI_ROOT"/omnibioai-toolserver \
  "$OMNIBIOAI_ROOT"/omnibioai-model-registry \
  "$OMNIBIOAI_ROOT"/omnibioai-dev-hub \
  "$OMNIBIOAI_ROOT"/omnibioai-policy-engine \
  "$OMNIBIOAI_ROOT"/omnibioai-security-audit \
  "$OMNIBIOAI_ROOT"/omnibioai-hpc-policy-engine \
  "$OMNIBIOAI_ROOT"/omnibioai-auth \
  "$OMNIBIOAI_ROOT"/omnibioai-tool-images \
  "$OMNIBIOAI_ROOT"/omnibioai-control-center \
  "$OMNIBIOAI_ROOT"/omnibioai-workflow-bundles \
  "$OMNIBIOAI_ROOT"/omnibioai-studio \
  "$OMNIBIOAI_ROOT"/omnibioai-lims \
  --exclude-dir=node_modules,.venv,__pycache__,migrations,htmlcov,.git,obsolete,locale,data,work,uploads,downloads,results,output,cache,logs,tmp \
  --exclude-ext=json,lock,svg,min.js,map,po,pot,txt,csv,log,sql \
  --include-lang=Python,JavaScript,TypeScript,CSS,HTML,Shell,R,Dockerfile,YAML,Groovy,JSX,SCSS \
  2>/dev/null | tail -15
