"""A Ponte: nucleo que costura o MRTR do MCP com a maquina de estados da Task A2A.

Aqui e onde o `input_required` do servidor MCP vira `TASK_STATE_INPUT_REQUIRED`
(`_executar`, ramo `PedeInput`) e onde o `requestState` volta ao servidor no
retry (ramo de continuacao de `enviar_mensagem` -> `_executar` ->
`mcp_host.call_tool(..., request_state=pendencia.request_state)`).

Estado em memoria, por processo, nunca sobrevive a um restart do agente.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from . import mcp_host, tasks, trace
from .pedido import PEDIDO_INVALIDO, parse_escolha, parse_pedido, texto_das_parts
from .tasks import CANCELED, COMPLETED, FAILED, INPUT_REQUIRED, WORKING, Task

ESCOLHA_RECUSAR = "recusar"


class ErroA2A(Exception):
    def __init__(self, codigo: int, mensagem: str) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


@dataclass
class Pendencia:
    chave: str
    alternativas: list[str]
    request_state: str
    tool: str
    arguments: dict


_tasks: dict[str, Task] = {}
_pendencias: dict[str, Pendencia] = {}
_locks: dict[str, asyncio.Lock] = {}
_trace_da_task: dict[str, str] = {}


def _log(task_id: str, estado: str, trace_id: str) -> None:
    print(f"[a2a] task={task_id} state={estado} trace={trace_id}", file=sys.stderr, flush=True)


def _lock_de(task_id: str) -> asyncio.Lock:
    if task_id not in _locks:
        _locks[task_id] = asyncio.Lock()
    return _locks[task_id]


def obter_task(task_id: str) -> Task | None:
    return _tasks.get(task_id)


async def enviar_mensagem(mensagem: dict, header_traceparent: str | None) -> Task:
    task_id = mensagem.get("taskId")

    if not task_id:
        return await _iniciar_task(mensagem, header_traceparent)

    task = _tasks.get(task_id)
    if task is None:
        raise ErroA2A(-32001, "Task not found")

    trace_id = trace.trace_id_de(header_traceparent) or _trace_da_task.get(task_id) or trace.novo_trace_id()
    _trace_da_task[task_id] = trace_id

    async with _lock_de(task_id):
        if task.terminal():
            raise ErroA2A(-32004, "Task em estado terminal nao aceita novas mensagens")
        if task.state != INPUT_REQUIRED:
            raise ErroA2A(-32004, "Task nao esta aguardando input")

        task.adicionar_mensagem_usuario(mensagem)
        pendencia = _pendencias.get(task_id)
        if pendencia is None:
            raise ErroA2A(-32004, "Task nao possui pendencia de input")

        texto = texto_das_parts(mensagem.get("parts", []))
        escolha = parse_escolha(texto)

        if escolha == ESCOLHA_RECUSAR:
            resposta_elicitation = {"action": "decline"}
        elif escolha is not None and escolha in pendencia.alternativas:
            resposta_elicitation = {"action": "accept", "content": {"sala": escolha}}
        else:
            task.transicionar(INPUT_REQUIRED, "alternativas: " + ", ".join(pendencia.alternativas))
            _log(task.id, task.state, trace_id)
            return task

        del _pendencias[task_id]
        task.transicionar(WORKING)
        return await _executar(
            task,
            pendencia.tool,
            pendencia.arguments,
            {pendencia.chave: resposta_elicitation},
            pendencia.request_state,
            trace_id,
            recusou=(escolha == ESCOLHA_RECUSAR),
        )


async def _iniciar_task(mensagem: dict, header_traceparent: str | None) -> Task:
    trace_id = trace.trace_id_de(header_traceparent) or trace.novo_trace_id()

    task = tasks.criar_task()
    _tasks[task.id] = task
    _trace_da_task[task.id] = trace_id
    task.adicionar_mensagem_usuario(mensagem)

    texto = texto_das_parts(mensagem.get("parts", []))
    pedido = parse_pedido(texto)

    task.transicionar(WORKING)
    _log(task.id, task.state, trace_id)

    if pedido is None:
        task.transicionar(FAILED, PEDIDO_INVALIDO)
        _log(task.id, task.state, trace_id)
        return task

    await mcp_host.garantir_discovery(trace.novo_traceparent(trace_id))

    if not mcp_host.tool_conhecida("reservar_sala"):
        task.transicionar(FAILED, "Tool reservar_sala nao encontrada no servidor MCP")
        _log(task.id, task.state, trace_id)
        return task

    args = {
        "sala": pedido.sala,
        "inicio": pedido.inicio,
        "fim": pedido.fim,
        "responsavel": pedido.responsavel,
    }
    return await _executar(task, "reservar_sala", args, None, None, trace_id)


async def _executar(
    task: Task,
    tool: str,
    args: dict,
    input_responses: dict | None,
    request_state: str | None,
    trace_id: str,
    recusou: bool = False,
) -> Task:
    resultado = await mcp_host.call_tool(
        tool,
        args,
        input_responses=input_responses,
        request_state=request_state,
        traceparent=trace.novo_traceparent(trace_id),
    )

    if isinstance(resultado, mcp_host.Completo):
        estruturado = resultado.estruturado
        if recusou or estruturado.get("reservado") is False:
            task.transicionar(CANCELED, "Reserva recusada pelo solicitante.")
        else:
            politica = await mcp_host.politica_versao(trace.novo_traceparent(trace_id))
            artifact_dados = {
                "reserva": estruturado.get("reserva"),
                "sala": estruturado.get("sala"),
                "inicio": estruturado.get("inicio"),
                "fim": estruturado.get("fim"),
                "responsavel": estruturado.get("responsavel"),
                "politica": politica,
            }
            task.artifacts = [tasks.artifact_reserva(artifact_dados)]
            task.transicionar(COMPLETED, f"Reserva {estruturado.get('reserva')} confirmada na {estruturado.get('sala')}.")
    elif isinstance(resultado, mcp_host.ErroTool):
        task.transicionar(FAILED, resultado.mensagem)
    elif isinstance(resultado, mcp_host.ErroProtocolo):
        task.transicionar(FAILED, f"Erro de protocolo MCP {resultado.codigo}: {resultado.mensagem}")
    elif isinstance(resultado, mcp_host.PedeInput):
        _pendencias[task.id] = Pendencia(
            chave=resultado.chave,
            alternativas=resultado.alternativas,
            request_state=resultado.request_state,
            tool=tool,
            arguments=args,
        )
        task.transicionar(INPUT_REQUIRED, "alternativas: " + ", ".join(resultado.alternativas))

    _log(task.id, task.state, trace_id)
    return task
