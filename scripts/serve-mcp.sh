#!/usr/bin/env bash
set -euo pipefail

SUPERVISOR_ROOT="${RPGK_SUPERVISOR_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MCP_PYTHON="${RPGK_MCP_PYTHON:-$SUPERVISOR_ROOT/.venv-mcp/bin/python}"
if [[ ! -x "$MCP_PYTHON" ]]; then
  echo "MCP runtime missing. See docs/MCP_ACCESS.md for the one-time venv setup." >&2
  exit 1
fi
exec "$MCP_PYTHON" "$SUPERVISOR_ROOT/scripts/supervisor_mcp.py" "$@"
