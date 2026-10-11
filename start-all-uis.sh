#!/bin/bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ecosystem_path.sh"
set -e
BASE="$OMNIBIOAI_ROOT"
echo 'Starting OmniBioAI backend APIs...'

# Workflow bundles backend
cd $BASE/omnibioai-workflow-bundles
pkill -f 'python api/server.py' 2>/dev/null || true
nohup python api/server.py > /tmp/api-8098.log 2>&1 &
echo 'Workflow API       → http://localhost:8098'

# Tool images backend
cd $BASE/omnibioai-tool-images
pkill -f 'python api/server.py' 2>/dev/null || true
nohup python api/server.py > /tmp/api-8097.log 2>&1 &
echo 'Tool Images API    → http://localhost:8097'

# RAG backend
cd $BASE/omnibioai-rag
pkill -f 'ragbio.api.server' 2>/dev/null || true
.venv/bin/python -m ragbio.api.server > /tmp/api-8096.log 2>&1 &
echo 'RAG API            → http://localhost:8096'

sleep 3
echo ''
echo 'Starting React UIs...'
pkill -f 'serve -s dist' 2>/dev/null || true
sleep 1

setsid npx serve -s $BASE/omnibioai-model-registry/frontend/omnibioai-model-registry-ui/dist -l tcp://0.0.0.0:5181 -n >> /tmp/ui-5181.log 2>&1 &
echo 'Model Registry     → http://localhost:5181'

setsid npx serve -s $BASE/omnibioai-rag/frontend/ragbio-ui/dist -l tcp://0.0.0.0:5182 -n >> /tmp/ui-5182.log 2>&1 &
echo 'RAG / Query        → http://localhost:5182'

setsid npx serve -s $BASE/omnibioai-workflow-bundles/frontend/workflow-ui/dist -l tcp://0.0.0.0:5183 -n >> /tmp/ui-5183.log 2>&1 &
echo 'Workflow Bundles   → http://localhost:5183'

setsid npx serve -s $BASE/omnibioai-tool-images/frontend/tool-images-ui/dist -l tcp://0.0.0.0:5184 -n >> /tmp/ui-5184.log 2>&1 &
echo 'Tool Images        → http://localhost:5184'

setsid npx serve -s $BASE/omnibioai-tes/frontend/tes-ui/dist -l tcp://0.0.0.0:5185 -n >> /tmp/ui-5185.log 2>&1 &
echo 'TES / Jobs         → http://localhost:5185'

setsid npx serve -s $BASE/omnibioai-control-center/frontend/cc-ui/dist -l tcp://0.0.0.0:5186 -n >> /tmp/ui-5186.log 2>&1 &
echo 'Control Center     → http://localhost:5186'

setsid npx serve -s $BASE/omnibioai_sdk/launcher/build -l tcp://0.0.0.0:5190 -n >> /tmp/ui-5190.log 2>&1 &
echo 'SDK Launcher       → http://localhost:5190'

sleep 5
echo ''
echo 'Verifying all ports...'
for port in 5181 5182 5183 5184 5185 5186 5190; do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:$port)
  echo "  Port $port: HTTP $code"
done

echo ''
echo 'Logs: /tmp/ui-518*.log  /tmp/api-809*.log'
echo 'Stop UIs: pkill -f "serve -s dist"'
