"""O agente como host MCP: descoberta, leitura do resource e chamada de tools.

Usa o cliente oficial do SDK (`mcp.client`) sobre Streamable HTTP. O ciclo de
MRTR nunca e respondido aqui: `call_tool` usa `allow_input_required=True` e
devolve o `InputRequiredResult` cru para a ponte decidir. O
`elicitation_callback` registrado abaixo nunca deve ser chamado — existe so
como defesa contra regressao (se o SDK algum dia tentar resolver a elicitation
sozinho, isso levanta um erro alto e visivel em vez de fechar o ciclo calado).

A sessao MCP e um unico objeto mantido vivo por todo o processo do agente
(entrada/saida no lifespan do Starlette). Isso nao e so uma otimizacao: o
gerador de id de JSON-RPC do SDK e por-sessao, comecando em zero a cada nova
`ClientSession`. Abrir uma conexao nova a cada chamada faria o retry de uma
Task pausada correr o risco de repetir o id do request inicial (dois
`ClientSession` distintos, cada um comecando do zero) — exatamente o que a
spec proibe. Uma sessao unica garante um contador sempre crescente.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import mcp_types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")
CLIENT_INFO = types.Implementation(name="agente-central-de-salas", version="1.0.0")


@dataclass
class Completo:
    estruturado: dict[str, Any]
    texto: str


@dataclass
class ErroTool:
    mensagem: str


@dataclass
class PedeInput:
    chave: str
    alternativas: list[str]
    request_state: str


@dataclass
class ErroProtocolo:
    codigo: int
    mensagem: str


async def _elicitation_nunca_chamado(context: Any, params: Any) -> Any:
    raise RuntimeError("a ponte nunca responde elicitation sozinha: o input_required deveria ter sido devolvido cru")


_exit_stack: AsyncExitStack | None = None
_session: ClientSession | None = None
_lock = asyncio.Lock()


async def _sessao() -> ClientSession:
    global _exit_stack, _session
    if _session is not None:
        return _session
    async with _lock:
        if _session is not None:
            return _session
        stack = AsyncExitStack()
        read_stream, write_stream = await stack.enter_async_context(streamable_http_client(MCP_URL))
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                client_info=CLIENT_INFO,
                elicitation_callback=_elicitation_nunca_chamado,
            )
        )
        # Nao ha initialize nem sessao no servidor (stateless): `discover()` so
        # faz o SDK estampar os headers Mcp-* e o _meta corretos a partir daqui.
        await session.discover()
        _exit_stack = stack
        _session = session
        return _session


async def encerrar() -> None:
    global _exit_stack, _session
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _session = None


def _meta(traceparent: str) -> dict[str, Any]:
    return {"traceparent": traceparent}


_catalogo: set[str] | None = None
_politica_versao: str | None = None


async def garantir_discovery(traceparent: str) -> None:
    """tools/list (com cache) antes da primeira chamada, e leitura da politica."""
    global _catalogo, _politica_versao
    session = await _sessao()
    if _catalogo is None:
        resultado = await session.list_tools(params=types.PaginatedRequestParams(meta=_meta(traceparent)))
        _catalogo = {t.name for t in resultado.tools}
    if _politica_versao is None:
        _politica_versao = await _ler_politica_versao(session, traceparent)


async def _ler_politica_versao(session: ClientSession, traceparent: str) -> str:
    resultado = await session.read_resource("politica://uso", meta=_meta(traceparent))
    conteudo = resultado.contents[0] if resultado.contents else None
    texto = getattr(conteudo, "text", "") or ""
    primeira_linha = texto.splitlines()[0] if texto else ""
    return primeira_linha.split(":", 1)[1].strip() if ":" in primeira_linha else ""


async def politica_versao(traceparent: str) -> str:
    await garantir_discovery(traceparent)
    return _politica_versao or ""


def tool_conhecida(nome: str) -> bool:
    return _catalogo is not None and nome in _catalogo


async def call_tool(
    tool: str,
    arguments: dict[str, Any],
    *,
    input_responses: dict[str, Any] | None,
    request_state: str | None,
    traceparent: str,
) -> Completo | ErroTool | PedeInput | ErroProtocolo:
    session = await _sessao()
    try:
        resultado = await session.call_tool(
            tool,
            arguments,
            input_responses=input_responses,
            request_state=request_state,
            meta=_meta(traceparent),
            allow_input_required=True,
        )
    except MCPError as e:
        return ErroProtocolo(codigo=e.code, mensagem=e.message)

    if isinstance(resultado, types.InputRequiredResult):
        chave, pedido = next(iter(resultado.input_requests.items()))
        schema_sala = (pedido.params.requested_schema.get("properties") or {}).get("sala", {})
        alternativas = schema_sala.get("enum") or ([schema_sala["const"]] if "const" in schema_sala else [])
        return PedeInput(chave=chave, alternativas=list(alternativas), request_state=resultado.request_state or "")

    texto = " ".join(getattr(bloco, "text", "") for bloco in resultado.content)
    if resultado.is_error:
        return ErroTool(mensagem=texto)
    return Completo(estruturado=resultado.structured_content or {}, texto=texto)
