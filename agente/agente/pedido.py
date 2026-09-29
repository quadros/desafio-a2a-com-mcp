"""Parser do formato fixo de pedido e de continuacao da skill reservar-sala.

O agente nao valida sala, datas nem politica: so repassa ao MCP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PEDIDO = re.compile(r"^reservar sala=(\S+) inicio=(\S+) fim=(\S+) responsavel=(.+)$")
_ESCOLHA = re.compile(r"^escolha=(\S+)$")

PEDIDO_INVALIDO = "Pedido invalido: use reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>"


@dataclass
class Pedido:
    sala: str
    inicio: str
    fim: str
    responsavel: str


def parse_pedido(texto: str) -> Pedido | None:
    m = _PEDIDO.match(texto.strip())
    if not m:
        return None
    sala, inicio, fim, responsavel = m.groups()
    return Pedido(sala=sala, inicio=inicio, fim=fim, responsavel=responsavel)


def parse_escolha(texto: str) -> str | None:
    m = _ESCOLHA.match(texto.strip())
    if not m:
        return None
    return m.group(1)


def texto_das_parts(parts: list[dict]) -> str:
    return " ".join(p.get("text", "") for p in parts if "text" in p)
