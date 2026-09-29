"""Rotas HTTP do agente: o Agent Card e o endpoint JSON-RPC 2.0 do A2A."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import mcp_host, ponte
from .card import montar_agent_card


def _erro_jsonrpc(id_, codigo: int, mensagem: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": codigo, "message": mensagem}}


def _resultado_task(id_, task) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": {"task": task.to_dict()}}


async def agent_card(_request: Request) -> JSONResponse:
    return JSONResponse(montar_agent_card())


async def a2a_endpoint(request: Request) -> JSONResponse:
    try:
        corpo = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(_erro_jsonrpc(None, -32700, "Parse error"), status_code=400)

    id_ = corpo.get("id")
    metodo = corpo.get("method")
    params = corpo.get("params") or {}
    traceparent = request.headers.get("traceparent")

    if metodo == "SendMessage":
        mensagem = params.get("message")
        if not isinstance(mensagem, dict):
            return JSONResponse(_erro_jsonrpc(id_, -32602, "Invalid params: message ausente"), status_code=400)
        try:
            task = await ponte.enviar_mensagem(mensagem, traceparent)
        except ponte.ErroA2A as e:
            return JSONResponse(_erro_jsonrpc(id_, e.codigo, e.mensagem))
        return JSONResponse(_resultado_task(id_, task))

    if metodo == "GetTask":
        task_id = params.get("id")
        task = ponte.obter_task(task_id) if task_id else None
        if task is None:
            return JSONResponse(_erro_jsonrpc(id_, -32001, "Task not found"))
        return JSONResponse(_resultado_task(id_, task))

    return JSONResponse(_erro_jsonrpc(id_, -32601, "Method not found"))


@asynccontextmanager
async def _lifespan(_app: Starlette):
    try:
        yield
    finally:
        await mcp_host.encerrar()


app = Starlette(
    routes=[
        Route("/.well-known/agent-card.json", agent_card, methods=["GET"]),
        Route("/a2a", a2a_endpoint, methods=["POST"]),
    ],
    lifespan=_lifespan,
)
