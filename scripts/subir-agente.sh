#!/usr/bin/env bash
# Sobe o agente (porta 7300), criando o venv se necessario.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../agente"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -e .
fi

exec ./.venv/bin/python -m agente
