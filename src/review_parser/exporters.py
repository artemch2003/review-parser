from __future__ import annotations

import csv
import json
from enum import Enum
from pathlib import Path
from typing import Iterable

from .models import Review

class ExportFormat(str, Enum):
    json = "json"
    csv = "csv"


def export_reviews(reviews: Iterable[Review], out_path: Path, fmt: ExportFormat) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == ExportFormat.json:
        payload = [r.model_dump(mode="json") for r in reviews]
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return

    if fmt == ExportFormat.csv:
        rows = [r.model_dump(mode="json") for r in reviews]
        fieldnames: list[str] = sorted({k for row in rows for k in row.keys()})
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return

    raise ValueError(f"Unsupported export format: {fmt}")

