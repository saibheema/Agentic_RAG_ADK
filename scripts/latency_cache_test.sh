#!/usr/bin/env bash
# Latency + correctness test for response-cache + turn-collapse optimizations.
set -u
BASE=https://agentic-rag-adk-664984131730.us-central1.run.app
APP="agentic_rag"

run_query () {
  local user_id="$1" sess="$2" query="$3"
  local t0 t1
  t0=$(python3 -c 'import time;print(time.time())')
  resp=$(curl -s -X POST "$BASE/run" -H "Content-Type: application/json" --max-time 180 \
    -d "{\"appName\":\"$APP\",\"userId\":\"$user_id\",\"sessionId\":\"$sess\",\"newMessage\":{\"role\":\"user\",\"parts\":[{\"text\":\"$query\"}]}}")
  t1=$(python3 -c 'import time;print(time.time())')
  dur=$(python3 -c "print(f'{$t1-$t0:.2f}')")
  # Extract the last text part (final answer) from the event array
  ans=$(printf '%s' "$resp" | python3 -c '
import sys,json
try:
    ev=json.load(sys.stdin)
except Exception as e:
    print("PARSE_ERROR:",e); sys.exit()
last=""
for e in ev:
    for p in (e.get("content") or {}).get("parts") or []:
        if p.get("text"): last=p["text"]
print(last[:400].replace(chr(10)," "))
')
  echo "  time=${dur}s  ans=${ans:0:300}"
}

new_sess () { echo "lat-$(date +%s)-$RANDOM"; }

echo "=================== YIS (SQL Server) ==================="
U="lat-yis"; S=$(new_sess)
curl -s -X POST "$BASE/apps/$APP/users/$U/sessions/$S" -H "Content-Type: application/json" \
  -d '{"user_name":"Test User","role_name":"Internal","replevel":1,"salesperson_id":""}' >/dev/null
Q="Who are the top 5 salespersons by total revenue this year?"
echo "[YIS cold/miss]"; run_query "$U" "$S" "$Q"
echo "[YIS cached]";    run_query "$U" "$S" "$Q"

echo
echo "=================== Odoo (ERP) ==================="
U2="lat-odoo"; S2=$(new_sess)
curl -s -X POST "$BASE/apps/$APP/users/$U2/sessions/$S2" -H "Content-Type: application/json" \
  -d '{"user_name":"Test User","role_name":"Internal","replevel":1,"salesperson_id":""}' >/dev/null
Q2="Show me top 5 customers by total order value from Odoo sale orders. Include customer name and total amount."
echo "[Odoo cold/miss]"; run_query "$U2" "$S2" "$Q2"
echo "[Odoo cached]";    run_query "$U2" "$S2" "$Q2"
