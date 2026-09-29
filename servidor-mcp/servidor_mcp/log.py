"""Middleware ASGI que loga no stderr cada request recebido pelo endpoint MCP,
antes do dispatch, e valida os campos obrigatorios de `_meta`.

Registra, no minimo, method, id e traceparent. Nunca loga o requestState
inteiro: no maximo os 8 primeiros caracteres.
"""

from __future__ import annotations

import json
import sys

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"


def _log_linha(corpo: dict) -> None:
    metodo = corpo.get("method", "-")
    id_ = corpo.get("id", "-")
    params = corpo.get("params") or {}
    meta = params.get("_meta") or {}
    traceparent = meta.get("traceparent", "-")
    nome = params.get("name") or params.get("uri") or "-"
    retry = "true" if params.get("requestState") else "false"
    print(
        f"[mcp] method={metodo} id={id_} name={nome} traceparent={traceparent} retry={retry}",
        file=sys.stderr,
        flush=True,
    )


def _meta_invalida(corpo: dict) -> bool:
    """True quando o request precisa dos dois campos obrigatorios de _meta e falta algum.

    initialize e notifications nao carregam esses campos; toda outra chamada carrega.
    """
    metodo = corpo.get("method", "")
    if metodo in ("initialize", "notifications/initialized", "ping"):
        return False
    if "method" not in corpo:
        return False
    params = corpo.get("params")
    if not isinstance(params, dict):
        return True
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return True
    return META_PROTOCOL_VERSION not in meta or META_CLIENT_CAPABILITIES not in meta


def _erro_meta_response(corpo: dict) -> bytes:
    resposta = {
        "jsonrpc": "2.0",
        "id": corpo.get("id"),
        "error": {
            "code": -32602,
            "message": "Invalid params: _meta must declare io.modelcontextprotocol/protocolVersion "
            "and io.modelcontextprotocol/clientCapabilities",
        },
    }
    return json.dumps(resposta).encode("utf-8")


class LogEValidacaoMiddleware:
    """Middleware ASGI puro: le o corpo do POST, loga, valida _meta e reinjeta o corpo."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        corpo_bytes = b""
        mais_corpo = True
        mensagens_recebidas: list[dict] = []
        while mais_corpo:
            mensagem = await receive()
            mensagens_recebidas.append(mensagem)
            corpo_bytes += mensagem.get("body", b"")
            mais_corpo = mensagem.get("more_body", False)

        try:
            corpo = json.loads(corpo_bytes) if corpo_bytes else {}
        except json.JSONDecodeError:
            corpo = {}

        if isinstance(corpo, dict) and corpo:
            _log_linha(corpo)

        if isinstance(corpo, dict) and corpo and _meta_invalida(corpo):
            resposta_bytes = _erro_meta_response(corpo)
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": resposta_bytes})
            return

        indice = {"i": 0}

        async def receive_novamente():
            if indice["i"] < len(mensagens_recebidas):
                m = mensagens_recebidas[indice["i"]]
                indice["i"] += 1
                return m
            return {"type": "http.disconnect"}

        await self.app(scope, receive_novamente, send)
