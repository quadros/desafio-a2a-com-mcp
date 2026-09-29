#!/usr/bin/env bash
# Sobe o servidor MCP (porta 7301), criando o venv se necessario.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../servidor-mcp"

if [ -z "${REQUEST_STATE_SECRET:-}" ]; then
  echo "REQUEST_STATE_SECRET ausente. Gere um com:" >&2
  echo '  export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")' >&2
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -e .
fi

exec ./.venv/bin/python -m servidor_mcp
