"""Modelo de Task A2A v1.0 e a maquina de estados.

Estado terminal (COMPLETED, CANCELED, FAILED) e definitivo: uma vez alcancado,
a Task nunca volta a WORKING.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field

SUBMITTED = "TASK_STATE_SUBMITTED"
WORKING = "TASK_STATE_WORKING"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
COMPLETED = "TASK_STATE_COMPLETED"
CANCELED = "TASK_STATE_CANCELED"
FAILED = "TASK_STATE_FAILED"

TERMINAIS = {COMPLETED, CANCELED, FAILED}


class TransicaoInvalida(Exception):
    pass


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{secrets.token_hex(6)}"


@dataclass
class Task:
    id: str
    context_id: str
    state: str = SUBMITTED
    status_message: dict | None = None
    history: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)

    def terminal(self) -> bool:
        return self.state in TERMINAIS

    def transicionar(self, novo_estado: str, texto: str | None = None) -> None:
        if self.terminal():
            raise TransicaoInvalida(f"Task {self.id} em estado terminal {self.state} nao aceita nova transicao")
        self.state = novo_estado
        if texto is not None:
            msg = {
                "messageId": novo_id("msg"),
                "role": "ROLE_AGENT",
                "parts": [{"text": texto}],
                "taskId": self.id,
                "contextId": self.context_id,
            }
            self.status_message = msg
            self.history.append(msg)

    def adicionar_mensagem_usuario(self, mensagem: dict) -> None:
        self.history.append(mensagem)

    def to_dict(self) -> dict:
        status: dict = {"state": self.state}
        if self.status_message is not None:
            status["message"] = self.status_message
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": status,
            "history": self.history,
            "artifacts": self.artifacts,
        }


def criar_task() -> Task:
    return Task(id=novo_id("task"), context_id=novo_id("ctx"))


def artifact_reserva(dados: dict) -> dict:
    return {
        "artifactId": novo_id("art"),
        "name": "reserva",
        "parts": [{"text": json.dumps(dados, ensure_ascii=False)}],
    }
