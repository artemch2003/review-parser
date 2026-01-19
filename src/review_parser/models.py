from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Review(BaseModel):
    source: Literal["yandex_maps"] = "yandex_maps"
    org_id: str | None = None
    org_url: str

    author: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    date: datetime | None = None
    text: str | None = None

    likes: int | None = Field(default=None, ge=0)
    dislikes: int | None = Field(default=None, ge=0)

    raw: dict[str, Any] | None = None

