"""Entrypoint: python -m agente"""

from __future__ import annotations

import os

import uvicorn

from .a2a_http import app


def main() -> None:
    porta = int(os.environ.get("AGENTE_PORT", "7300"))
    uvicorn.run(app, host="0.0.0.0", port=porta, log_level="info")


if __name__ == "__main__":
    main()
