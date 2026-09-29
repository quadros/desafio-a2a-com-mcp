"""Utilitarios de W3C traceparent: `00-<trace-id 32hex>-<span-id 16hex>-01`."""

from __future__ import annotations

import re
import secrets

_PADRAO = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def trace_id_de(traceparent: str | None) -> str | None:
    if not traceparent:
        return None
    m = _PADRAO.match(traceparent.strip())
    return m.group(1) if m else None


def novo_trace_id() -> str:
    return secrets.token_hex(16)


def novo_traceparent(trace_id: str) -> str:
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01"
