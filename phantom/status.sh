#!/usr/bin/env bash
# PHANTOM — Check status of all services
cd "$(dirname "$0")/.."

echo "━━━ Docker services ━━━"
docker compose ps

echo ""
echo "━━━ Health checks ━━━"
for name_url in \
    "Prometheus|http://localhost:9090/-/ready" \
    "Grafana|http://localhost:3000/api/health" \
    "Graph_Builder|http://localhost:8000/health" \
    "ML_Service|http://localhost:8001/health" \
    "Dashboard|http://localhost:3001"; do
    name="${name_url%%|*}"
    url="${name_url##*|}"
    if curl -sf "$url" > /dev/null 2>&1; then
        echo "  ✓ $name"
    else
        echo "  ✗ $name  ($url)"
    fi
done

echo ""
echo "━━━ ML prediction test ━━━"
curl -s "http://localhost:8001/predict/frontend?namespace=phantom" \
    | python3 -m json.tool 2>/dev/null || echo "  ML service not ready"

echo ""
echo "━━━ Graph snapshot ━━━"
curl -s http://localhost:8000/graph \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f'  Nodes: {len(d.get(\"nodes\",[]))}')
print(f'  Edges: {len(d.get(\"edges\",[]))}')
for n in d.get('nodes',[])[:4]:
    print(f'    {n[\"id\"]}: {n.get(\"rps\",0):.1f} rps')
" 2>/dev/null || echo "  Graph builder not ready"