"""Dominio das salas: dados, validacoes de politica, conflito e alternativas.

Sem regra de sessao, sem LLM. As reservas vivem em memoria, carregadas uma vez
na subida a partir de dados/reservas.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_SAO_PAULO = ZoneInfo("America/Sao_Paulo")
JANELA_ABERTURA = time(8, 0)
JANELA_FECHAMENTO = time(20, 0)
DURACAO_MAXIMA = timedelta(hours=2)


class ErroDominio(Exception):
    """Erro de execucao de tool: vira isError=true com a mensagem exata."""


def _dados_dir() -> Path:
    override = os.environ.get("DADOS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "dados"


@dataclass
class Sala:
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


@dataclass
class Reserva:
    id: str
    sala: str
    inicio: datetime
    fim: datetime
    responsavel: str


@dataclass
class Dominio:
    salas: list[Sala] = field(default_factory=list)
    reservas: list[Reserva] = field(default_factory=list)
    politica_texto: str = ""
    politica_versao: str = ""

    @classmethod
    def carregar(cls) -> "Dominio":
        base = _dados_dir()
        salas_json = json.loads((base / "salas.json").read_text(encoding="utf-8"))
        reservas_json = json.loads((base / "reservas.json").read_text(encoding="utf-8"))
        politica_texto = (base / "politica-de-uso.md").read_text(encoding="utf-8")

        salas = [
            Sala(id=s["id"], nome=s["nome"], capacidade=s["capacidade"], recursos=list(s["recursos"]))
            for s in salas_json
        ]
        reservas = [
            Reserva(
                id=r["id"],
                sala=r["sala"],
                inicio=datetime.fromisoformat(r["inicio"]),
                fim=datetime.fromisoformat(r["fim"]),
                responsavel=r["responsavel"],
            )
            for r in reservas_json
        ]
        primeira_linha = politica_texto.splitlines()[0] if politica_texto else ""
        versao = primeira_linha.split(":", 1)[1].strip() if ":" in primeira_linha else ""
        return cls(salas=salas, reservas=reservas, politica_texto=politica_texto, politica_versao=versao)

    def sala_por_id(self, id_: str) -> Sala | None:
        return next((s for s in self.salas if s.id == id_), None)

    def proximo_id_reserva(self) -> str:
        maior = 0
        for r in self.reservas:
            try:
                n = int(r.id.split("-")[-1])
            except ValueError:
                continue
            maior = max(maior, n)
        return f"res-{maior + 1:04d}"

    def validar_sala_e_intervalo(self, sala_id: str, inicio_s: str, fim_s: str) -> tuple[Sala, datetime, datetime]:
        """Levanta ErroDominio com a mensagem exata na primeira violacao encontrada."""
        sala = self.sala_por_id(sala_id)
        if sala is None:
            raise ErroDominio(f"Sala inexistente: {sala_id}")

        try:
            inicio = datetime.fromisoformat(inicio_s)
        except ValueError as e:
            raise ErroDominio(f"Data de inicio invalida: {inicio_s}") from e
        try:
            fim = datetime.fromisoformat(fim_s)
        except ValueError as e:
            raise ErroDominio(f"Data de fim invalida: {fim_s}") from e

        if fim <= inicio:
            raise ErroDominio("Intervalo invalido: fim deve ser posterior a inicio")

        inicio_local = inicio.astimezone(TZ_SAO_PAULO)
        fim_local = fim.astimezone(TZ_SAO_PAULO)
        if not (JANELA_ABERTURA <= inicio_local.time() <= JANELA_FECHAMENTO) or not (
            JANELA_ABERTURA <= fim_local.time() <= JANELA_FECHAMENTO
        ):
            raise ErroDominio("Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00")

        if fim - inicio > DURACAO_MAXIMA:
            raise ErroDominio("Duracao acima do limite: a politica permite no maximo 2 horas")

        return sala, inicio, fim

    def conflitos(self, sala_id: str, inicio: datetime, fim: datetime) -> list[Reserva]:
        return [
            r
            for r in self.reservas
            if r.sala == sala_id and r.inicio < fim and inicio < r.fim
        ]

    def livre(self, sala_id: str, inicio: datetime, fim: datetime) -> bool:
        return len(self.conflitos(sala_id, inicio, fim)) == 0

    def alternativas(self, sala_pedida: Sala, inicio: datetime, fim: datetime) -> list[str]:
        candidatas = [
            s
            for s in self.salas
            if s.id != sala_pedida.id and s.capacidade >= sala_pedida.capacidade and self.livre(s.id, inicio, fim)
        ]
        candidatas.sort(key=lambda s: (s.capacidade, s.id))
        return [s.id for s in candidatas[:3]]

    def criar_reserva(self, sala_id: str, inicio: datetime, fim: datetime, responsavel: str) -> Reserva:
        reserva = Reserva(id=self.proximo_id_reserva(), sala=sala_id, inicio=inicio, fim=fim, responsavel=responsavel)
        self.reservas.append(reserva)
        return reserva
