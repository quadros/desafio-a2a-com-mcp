"""Leitura e validacao de REQUEST_STATE_SECRET.

A chave nunca vem do codigo: sempre da variavel de ambiente, com no minimo
32 bytes de aleatoriedade (64 caracteres hex).
"""

from __future__ import annotations

import os
import sys

TTL_SEGUNDOS = 600  # 10 minutos, dentro da faixa exigida de 5 a 30 minutos


def carregar_segredo() -> bytes:
    valor = os.environ.get("REQUEST_STATE_SECRET")
    if not valor:
        sys.exit(
            "REQUEST_STATE_SECRET ausente. Gere um com: "
            'python3 -c "import secrets; print(secrets.token_hex(32))"'
        )
    try:
        bruto = bytes.fromhex(valor)
    except ValueError:
        sys.exit("REQUEST_STATE_SECRET precisa ser hexadecimal (use secrets.token_hex(32))")
    if len(bruto) < 32:
        sys.exit("REQUEST_STATE_SECRET precisa ter no minimo 32 bytes (64 caracteres hex)")
    return bruto
