from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Review


@dataclass(frozen=True)
class CodexReportConfig:
    """
    Конфиг генерации отчёта через установленный локально Codex CLI.

    Требования на машине пользователя:
    - установлен бинарь `codex`
    - выставлен OPENAI_API_KEY (или иной способ авторизации, поддерживаемый Codex CLI)
    """

    codex_bin: str = "codex"
    model: str | None = None
    sandbox: str = "read-only"
    output_language: str = "ru"


def _strip_reviews_to_minimal(reviews: list[Review]) -> list[dict[str, Any]]:
    """
    Убираем потенциально персональные поля (author/raw) и оставляем только
    то, что нужно для анализа: рейтинг, дата, текст.
    """
    minimal: list[dict[str, Any]] = []
    for r in reviews:
        text = (r.text or "").strip()
        if not text:
            continue
        minimal.append(
            {
                "rating": r.rating,
                "date": r.date.date().isoformat() if r.date else None,
                "text": text,
            }
        )
    return minimal


def _load_reviews(path: Path) -> list[Review]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Ожидался JSON-массив отзывов (list)")
    return [Review.model_validate(item) for item in payload]


def _system_prompt_en(*, output_language: str) -> str:
    # Важно: Codex CLI не имеет отдельного параметра "system prompt" в простом exec-режиме,
    # поэтому мы встраиваем инструкции в начало пользовательского промпта.
    return f"""\
You are a senior customer-review analyst (CX + Product + Marketing), operating at principal level.
You will analyze a JSON array of reviews. Each item has:
- rating: 1..5 or null
- date: YYYY-MM-DD or null
- text: string

Your goal: produce a concise, evidence-based, decision-ready report.

NON-NEGOTIABLE RULES (follow strictly):
1) No hallucinations. Do not invent facts, numbers, events, or external knowledge.
2) Evidence-first. Every material claim must be supported by direct quotes from the reviews.
3) Privacy: NEVER include author names, personal data, phone numbers, handles, or anything identifying.
4) Deduplicate by meaning. Merge synonyms and near-duplicates. Avoid repeating the same point.
5) Handle uncertainty. If data is weak/contradictory, explicitly label as "неясно" / "недостаточно данных".
6) Weight signals:
   - If a point appears once, label it as a one-off.
   - Prefer recurring signals.
7) Use rating signal when present:
   - 1–2 = negative
   - 3 = mixed/neutral
   - 4–5 = positive
   If rating is missing, infer sentiment from text and mark uncertainty.
8) Quotes:
   - For each key point include 1–2 SHORT verbatim quotes (<= 160 chars).
   - Quotes must be copied exactly. You may lightly trim with "…".
   - Do NOT paraphrase quotes.

OUTPUT FORMAT (critical):
- Output MUST be ONLY Markdown. No JSON. No code fences. No preambles.
- Output language MUST be {output_language} (all free text in Russian). Even though this prompt is in English.
- Use headings with exactly "####" as specified below.

REQUIRED SECTIONS (in this exact order):
#### Общая оценка
- Briefly describe overall sentiment and confidence.
- Include basic stats derived from the data (do the math):
  - Кол-во отзывов (consider only items with non-empty text)
  - Доля 1–2 / 3 / 4–5 (approximate % is ok)
  - Если возможно, средняя оценка по тем отзывам, где rating задан

#### Ключевые преимущества
- 3–7 bullet points: what people consistently like.
- Each bullet must include 1 quote (optionally 2 if very strong).

#### Ключевые недостатки/боли
- 2–7 bullet points: recurring issues or frustrations.
- Each bullet must include 1 quote (optionally 2).

#### Темы и наблюдения
- Cluster insights into themes (service, staff, price/value, location/access, cleanliness, food, atmosphere, activities, kids/family, booking, other).
- For each theme: short synthesis + 1 supporting quote.

#### Рекомендации (быстрые / стратегические)
- Split into:
  - Быстрые (0–30 дней): 3–6 actions, concrete and testable.
  - Стратегические (1–6 месяцев): 2–5 actions.
- Tie each recommendation to at least one observed issue/opportunity.

#### Риски и что мониторить
- 3–6 risks/unknowns + what metric/signal to monitor.
- Include contradictions and data gaps explicitly.

#### Резюме
- 2–4 sentences: who the place/service is best for, what to fix first, and expected impact.
"""


def generate_markdown_report(
    inp: Path,
    out: Path,
    cfg: CodexReportConfig,
    *,
    max_reviews: int | None = None,
) -> None:
    reviews = _load_reviews(inp)
    if max_reviews is not None:
        reviews = reviews[:max_reviews]

    minimal = _strip_reviews_to_minimal(reviews)
    tmp_path = out.parent / f".{inp.stem}.minimal.json"
    tmp_path.write_text(json.dumps(minimal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        prompt = (
            f"{_system_prompt_en(output_language=cfg.output_language)}\n\n"
            f"Данные для анализа лежат в файле: {tmp_path}\n"
            "Открой файл, прочитай JSON-массив и сделай отчёт по структуре выше.\n"
        )

        cmd: list[str] = [cfg.codex_bin, "--sandbox", cfg.sandbox, "exec"]
        if cfg.model:
            cmd += ["--model", cfg.model]
        cmd += [prompt]

        proc = subprocess.run(cmd, text=True, capture_output=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            raise RuntimeError(
                "Codex CLI завершился с ошибкой.\n"
                + (f"stderr:\n{stderr}\n" if stderr else "")
                + "Проверь, что `codex` установлен и настроен (например, OPENAI_API_KEY)."
            )

        md = (proc.stdout or "").strip()
        if not md:
            raise RuntimeError("Codex CLI не вернул текст отчёта (пустой stdout).")

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md + "\n", encoding="utf-8")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            # не критично
            pass

