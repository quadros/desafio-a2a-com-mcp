"""Entrypoint: python -m servidor_mcp"""

from __future__ import annotations

import os

import uvicorn

from .app import build_asgi_app


def main() -> None:
    porta = int(os.environ.get("MCP_PORT", "7301"))
    uvicorn.run(build_asgi_app(), host="0.0.0.0", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
