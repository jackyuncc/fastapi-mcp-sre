#!/usr/bin/env bash
set -euo pipefail

# Starter for running the FastAPI app.
# Usage: ./start.sh [all|web|grafana|oncall|k8s|multi]
# - all/web/grafana/oncall/k8s: run a single instance in that mode (defaults shown below)
# - multi: run four processes (grafana/oncall/k8s/web) on separate ports
#
# Common env vars: OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
# Single-instance overrides: HOST, PORT (defaults depend on mode)
# Multi-instance overrides: GRAFANA_HOST/PORT, ONCALL_HOST/PORT, K8S_HOST/PORT, WEB_HOST/PORT

set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-${APP_MODE:-all}}"

default_port_for() {
  case "$1" in
    grafana) echo "8011" ;;
    oncall) echo "8012" ;;
    k8s) echo "8013" ;;
    *) echo "8000" ;; # all or web
  esac
}

run_uvicorn() {
  local mode="$1" host="$2" port="$3"; shift 3
  echo "[start] mode=${mode} host=${host} port=${port}"
  # shellcheck disable=SC2086
  APP_MODE="$mode" HOST="$host" PORT="$port" "$@" uvicorn app.main:app --host "$host" --port "$port" --reload &
  pids+=("$!")
}

if [[ "$MODE" == "multi" ]]; then
  pids=()
  GRAFANA_HOST="${GRAFANA_HOST:-127.0.0.1}"
  GRAFANA_PORT="${GRAFANA_PORT:-8011}"
  ONCALL_HOST="${ONCALL_HOST:-127.0.0.1}"
  ONCALL_PORT="${ONCALL_PORT:-8012}"
  K8S_HOST="${K8S_HOST:-127.0.0.1}"
  K8S_PORT="${K8S_PORT:-8013}"
  WEB_HOST="${WEB_HOST:-127.0.0.1}"
  WEB_PORT="${WEB_PORT:-8000}"

  run_uvicorn grafana "$GRAFANA_HOST" "$GRAFANA_PORT"
  run_uvicorn oncall "$ONCALL_HOST" "$ONCALL_PORT"
  run_uvicorn k8s "$K8S_HOST" "$K8S_PORT"

  grafana_url="http://${GRAFANA_HOST}:${GRAFANA_PORT}/api/mcp/grafana"
  oncall_url="http://${ONCALL_HOST}:${ONCALL_PORT}/api/mcp/oncall"
  k8s_url="http://${K8S_HOST}:${K8S_PORT}/api/mcp/k8s"
  run_uvicorn web "$WEB_HOST" "$WEB_PORT" env GRAFANA_MCP_URL="$grafana_url" ONCALL_MCP_URL="$oncall_url" K8S_MCP_URL="$k8s_url"

  cat <<EOF
Multi mode running (simulated separate servers):
- Grafana MCP @ ${GRAFANA_HOST}:${GRAFANA_PORT}
  • GET ${grafana_url}/services
  • GET ${grafana_url}/{service}
- Oncall MCP @ ${ONCALL_HOST}:${ONCALL_PORT}
  • GET ${oncall_url}/incidents
  • POST ${oncall_url}/incidents/ack
- K8s MCP @ ${K8S_HOST}:${K8S_PORT}
  • GET ${k8s_url}/overview
  • GET ${k8s_url}/pods
  • POST ${k8s_url}/pods/restart
- Web/LLM @ ${WEB_HOST}:${WEB_PORT}
  • UI:   http://${WEB_HOST}:${WEB_PORT}/
  • Chat: POST http://${WEB_HOST}:${WEB_PORT}/api/web/chat
  • LLM:  POST http://${WEB_HOST}:${WEB_PORT}/api/web/chat-llm (calls MCPs above)

Stop with Ctrl+C.
EOF

  trap 'echo "Stopping..."; kill "${pids[@]}" 2>/dev/null || true' INT TERM EXIT
  wait "${pids[@]}"
else
  HOST="${HOST:-127.0.0.1}"
  PORT="${PORT:-$(default_port_for "$MODE")}"
  echo "Running single instance: mode=${MODE} host=${HOST} port=${PORT}"
  if [[ "$MODE" == "all" || "$MODE" == "web" ]]; then
    cat <<EOF
Endpoints:
- Web UI:            http://${HOST}:${PORT}/
- Web chat:          POST http://${HOST}:${PORT}/api/web/chat
- Web chat (LLM):    POST http://${HOST}:${PORT}/api/web/chat-llm
- Legacy services:   GET  http://${HOST}:${PORT}/api/services
- Legacy grafana:    GET  http://${HOST}:${PORT}/api/grafana/{service}
- Legacy incidents:  GET  http://${HOST}:${PORT}/api/incidents
EOF
  fi
  if [[ "$MODE" == "all" || "$MODE" == "grafana" ]]; then
    echo "- Grafana MCP:     GET  http://${HOST}:${PORT}/api/mcp/grafana/services and /api/mcp/grafana/{service}"
  fi
  if [[ "$MODE" == "all" || "$MODE" == "oncall" ]]; then
    echo "- Oncall MCP:      GET  http://${HOST}:${PORT}/api/mcp/oncall/incidents ; POST /api/mcp/oncall/incidents/ack"
  fi
  if [[ "$MODE" == "all" || "$MODE" == "k8s" ]]; then
    echo "- K8s MCP:         GET  http://${HOST}:${PORT}/api/mcp/k8s/overview ; GET /api/mcp/k8s/pods ; POST /api/mcp/k8s/pods/restart"
  fi
  APP_MODE="$MODE" HOST="$HOST" PORT="$PORT" exec uvicorn app.main:app --reload --host "$HOST" --port "$PORT"
fi
