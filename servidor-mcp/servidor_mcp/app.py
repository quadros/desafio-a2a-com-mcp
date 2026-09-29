"""MCPServer da Central de Salas: Streamable HTTP stateless, 3 tools + 1 resource.

O MRTR de `reservar_sala` usa o resolver `Resolve`/`Elicit` de primeira classe do
SDK (`mcp.server.mcpserver`): e o proprio framework quem atribui a chave de
`inputRequests`, sela o `requestState` via `RequestStateBoundary` (instalada
automaticamente a partir de `request_state_security=`) e recusa, com -32021,
clientes que nao declararam a capability de elicitation em form mode. Nada
disso e feito a mao aqui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from mcp.server.elicitation import AcceptedElicitation, CancelledElicitation, DeclinedElicitation, ElicitationResult
from mcp.server.mcpserver import Elicit, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from pydantic import BaseModel, Field, create_model

from .dominio import Dominio, ErroDominio
from .log import LogEValidacaoMiddleware
from .models import ConflitoOut, Disponibilidade, ListaDeSalas, ReservaOut, SalaOut
from .segredo import TTL_SEGUNDOS, carregar_segredo

MENSAGEM_CONFLITO = "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa."

dominio = Dominio.carregar()

mcp = MCPServer(
    name="central-de-salas",
    version="1.0.0",
    request_state_security=RequestStateSecurity(keys=[carregar_segredo()], ttl=TTL_SEGUNDOS),
)


@mcp.tool(name="listar_salas", description="Lista todas as salas com capacidade e recursos.")
def listar_salas() -> ListaDeSalas:
    return ListaDeSalas(
        salas=[SalaOut(id=s.id, nome=s.nome, capacidade=s.capacidade, recursos=s.recursos) for s in dominio.salas]
    )


@mcp.tool(
    name="consultar_disponibilidade",
    description="Diz se uma sala esta livre no intervalo, e quais reservas conflitam.",
)
def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
    try:
        _, inicio_dt, fim_dt = dominio.validar_sala_e_intervalo(sala, inicio, fim)
    except ErroDominio as e:
        raise ToolError(str(e)) from e
    conflitos = dominio.conflitos(sala, inicio_dt, fim_dt)
    return Disponibilidade(
        sala=sala,
        livre=len(conflitos) == 0,
        conflitos=[
            ConflitoOut(id=r.id, inicio=r.inicio.isoformat(), fim=r.fim.isoformat(), responsavel=r.responsavel)
            for r in conflitos
        ],
    )


def _schema_escolha(alternativas: list[str]) -> type[BaseModel]:
    """Schema plano com uma unica propriedade `sala`, restrita as alternativas.

    Literal com 2+ valores renderiza `enum`; com 1 valor, o gerador do pydantic
    pode renderizar `const` — a spec aceita as duas formas.
    """
    return create_model(
        "EscolhaDeSala",
        sala=(Literal[tuple(alternativas)], Field(title="Sala", description="Sala alternativa escolhida")),
    )


def escolha_de_sala(sala: str, inicio: str, fim: str) -> str | Elicit:
    """Resolver: roda antes do corpo de `reservar_sala`, em toda rodada (inicial e retry)."""
    try:
        sala_obj, inicio_dt, fim_dt = dominio.validar_sala_e_intervalo(sala, inicio, fim)
    except ErroDominio as e:
        raise ToolError(str(e)) from e
    if dominio.livre(sala, inicio_dt, fim_dt):
        return sala
    alternativas = dominio.alternativas(sala_obj, inicio_dt, fim_dt)
    if not alternativas:
        raise ToolError("Sem alternativas disponiveis no intervalo")
    return Elicit(MENSAGEM_CONFLITO, _schema_escolha(alternativas))


@mcp.tool(
    name="reservar_sala",
    description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.",
)
def reservar_sala(
    sala: str,
    inicio: str,
    fim: str,
    responsavel: str,
    escolha: Annotated[ElicitationResult[BaseModel], Resolve(escolha_de_sala)],
) -> ReservaOut:
    if isinstance(escolha, (DeclinedElicitation, CancelledElicitation)):
        return ReservaOut(reservado=False, motivo="recusado")

    dado = escolha.data if isinstance(escolha, AcceptedElicitation) else escolha
    sala_final = dado if isinstance(dado, str) else dado.sala

    inicio_dt = datetime.fromisoformat(inicio)
    fim_dt = datetime.fromisoformat(fim)
    reserva = dominio.criar_reserva(sala_final, inicio_dt, fim_dt, responsavel)
    return ReservaOut(
        reserva=reserva.id,
        reservado=True,
        sala=sala_final,
        inicio=inicio,
        fim=fim,
        responsavel=responsavel,
        politica=dominio.politica_versao,
        motivo=None,
    )


@mcp.resource("politica://uso", mime_type="text/markdown")
def politica_uso() -> str:
    return dominio.politica_texto


def build_asgi_app():
    app = mcp.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True)
    return LogEValidacaoMiddleware(app)
