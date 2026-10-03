#!/bin/bash
# build-all-new.sh

BASE=/home/manish/Desktop/machine
REGISTRY=ghcr.io/omnibioai

declare -A SERVICES=(
  ["omnibioai-tes"]="omnibioai-tes:new"
  ["omnibioai-model-registry"]="omnibioai-model-registry:new"
  ["omnibioai-control-center"]="omnibioai-control-center:new"
  ["omnibioai-rag"]="omnibioai-rag:new"
  ["omnibioai-toolserver"]="omnibioai-toolserver:new"
  ["omnibioai-lims"]="omnibioai-lims:new"
  ["omnibioai-tool-runtime"]="omnibioai-tool-runtime:new"
)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Building all new images..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

FAILED=()

for REPO in "${!SERVICES[@]}"; do
  TAG="${SERVICES[$REPO]}"
  IMAGE="$REGISTRY/$TAG"
  DIR="$BASE/$REPO"

  echo ""
  echo "▶ Building $IMAGE from $DIR/Dockerfile.new"

  docker build -f "$DIR/Dockerfile.new" -t "$IMAGE" "$DIR" 2>&1 | tail -5

  if [ $? -eq 0 ]; then
    echo "✅ $IMAGE built successfully"
  else
    echo "❌ $IMAGE FAILED"
    FAILED+=("$REPO")
  fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Size comparison (new vs old):"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for REPO in "${!SERVICES[@]}"; do
  NAME=$(echo "${SERVICES[$REPO]}" | cut -d: -f1)
  echo ""
  echo "── $NAME ──"
  docker images "$REGISTRY/$NAME" --format "  {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Delta layers for new images:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for REPO in "${!SERVICES[@]}"; do
  TAG="${SERVICES[$REPO]}"
  IMAGE="$REGISTRY/$TAG"
  echo ""
  echo "── $IMAGE ──"
  docker history "$IMAGE" \
    --format "{{.Size}}\t{{.CreatedBy}}" 2>/dev/null \
    | grep -v "^0B" | head -5
done

if [ ${#FAILED[@]} -gt 0 ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "❌ Failed builds:"
  for F in "${FAILED[@]}"; do
    echo "  - $F"
  done
fi
