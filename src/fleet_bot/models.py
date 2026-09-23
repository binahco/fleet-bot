from __future__ import annotations

from pydantic import BaseModel


class DailyDigest(BaseModel):
    """Resumen del día de la flota (`daily-digest-v1`)."""

    date: str
    summary: str
    tasks: list[str] = []
    watch: list[str] = []