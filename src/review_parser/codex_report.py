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

Your goal: produce a VERY detailed, evidence-based, decision-ready report AND a website-ready content pack.
The output will be used to build a public website page, so extract as much as possible from the reviews.

NON-NEGOTIABLE RULES (follow strictly):
1) No hallucinations. Do not invent facts, numbers, events, or external knowledge.
2) Evidence-first. Every material claim must be supported by direct quotes from the reviews.
3) Privacy: NEVER include author names, personal data, phone numbers, handles, or anything identifying.
4) Deduplicate by meaning. Merge synonyms and near-duplicates. Avoid repeating the same point.
5) Handle uncertainty. If data is weak/contradictory, explicitly label it as unclear / insufficient data.
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
9) Completeness:
   - Aim for maximum coverage. List ALL unique findings you can extract.
   - If a section has no evidence, explicitly write: "нет данных в отзывах".

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
 - Also include:
   - What kinds of visitors appear in the reviews (families, couples, groups, kids, corporate, etc.)
   - A short "что чаще всего упоминают" list (top recurring nouns/themes)

#### Ключевые преимущества
- Be exhaustive: list ALL unique strengths/praises you can find (not only top 3–7).
- Structure:
  - Start with "Основные (повторяющиеся)" — the recurring positives.
  - Then "Редкие (единичные)" — one-off positives (explicitly mark as one-off).
- Each bullet MUST include at least 1 quote (optionally 2).
- If there are many points, keep bullets short and deduplicated by meaning.

#### Ключевые недостатки/боли
- Be exhaustive: list ALL unique pain points/complaints you can find.
- Structure:
  - Start with "Основные (повторяющиеся)" — recurring issues.
  - Then "Редкие (единичные)" — one-off issues (explicitly mark as one-off).
- Each bullet MUST include at least 1 quote (optionally 2).
- Do not overfit. If something is rare, label it as such and lower confidence.

#### Темы и наблюдения
- Cluster insights into themes (service, staff, price/value, location/access, cleanliness, food, atmosphere, activities, kids/family, booking, other).
- For each theme: short synthesis + 1 supporting quote.
 - For each theme, also add:
   - "Для сайта: как это описать" — 1–2 neutral sentences suitable for public copy (must be supported by reviews).

#### Локация / как добраться (максимально детально)
This section is extremely important. The result will be used to build a public website page.
Be as exhaustive as possible while staying 100% grounded in the reviews. Do NOT invent any details.

Rules for this section:
- Extract EVERY unique location/access detail mentioned in the reviews, even if it appears only once.
- Prefer concrete facts/phrases: road quality, distance/time, landmarks, turn-offs, signage, route difficulty, seasonal conditions, parking, public transport, walking path, accessibility, safety, navigation issues.
- When a detail is not present in the data, write explicitly exactly: "нет данных в отзывах".
- Include MANY quotes (up to 10–15) if the reviews contain them; keep each quote <= 160 chars.
- Write in a structured way that is easy to copy into a site: short field-like bullets + short paragraphs where needed.

Output template for this section (follow exactly, fill what you can, otherwise "нет данных в отзывах"):
- **Где находится / контекст**: (что люди говорят — район/направление/рядом с чем)
- **Ориентиры и навигация**: (указатели, как найти, что может запутать)
- **Дорога и подъезд**: (качество дороги, грунтовка/асфальт, сложные места, сезонность)
- **Время в пути / расстояние**: (если упоминается — откуда и сколько)
- **Парковка**: (есть/нет, удобство, безопасность, вместимость — только если есть в отзывах)
- **Общественный транспорт**: нет данных в отзывах / (если есть)
- **Пешком / вело**: нет данных в отзывах / (если есть)
- **Доступность (коляски/маломобильные)**: нет данных в отзывах / (если есть)
- **Связь/интернет на месте**: нет данных в отзывах / (если есть)
- **Что важно знать перед поездкой**: (только из отзывов; если мало — перечисли "нет данных" + что уточнить)

Then add a mini-subsection "Набор цитат про локацию" with bullet quotes.
Then add "Пробелы данных (что спросить/добавить на сайт)" — a checklist of questions to collect missing info
(questions are allowed; but do NOT answer them unless supported by reviews).

#### Материалы для сайта (готовые блоки текста)
This section must be directly usable on a website. Write in Russian. No fluff. Stay grounded in reviews.
If something is not supported, do not include it (or mark "нет данных в отзывах").

Provide the following blocks (in this exact order, with subheadings as bold labels, NOT new #### headings):
- **Стиль и тон копирайта (гайд)**:
  - **Тон**: (например: дружелюбно-деловой / семейный / премиальный — выбрать по отзывам)
  - **Голос бренда**: 5–10 принципов (что подчёркивать, как говорить)
  - **Словарь**: слова/формулировки, которые можно использовать (10–25), и чего избегать (5–15)
  - **Правила доказательности**: как писать, чтобы не обещать лишнего (всё только из отзывов)
- **Структура страницы (рекомендуемый скелет)**:
  - H1 (1 вариант)
  - H2/H3-outline (10–25 пунктов) — в порядке прокрутки страницы
  - "Блок доверия": какие элементы добавить (цитаты, цифры, фото, FAQ) — based on review signals
- **Короткое описание (1–2 предложения)**: neutral, factual, attractive.
- **Ключевые преимущества (буллеты для сайта)**: 6–12 bullets.
- **Кому подойдёт**: 4–8 audience segments with 1-line value proposition each.
- **Что можно делать / активности**: exhaustive list based on reviews; if none, "нет данных в отзывах".
- **Как добраться (короткий блок)**: 5–10 bullets condensed from the location section.
- **Парковка / подъезд (коротко)**: only if mentioned; else "нет данных в отзывах".
- **Что важно знать перед визитом**: 8–15 bullets; include caveats and constraints surfaced in reviews.
- **FAQ (вопрос–ответ)**: 10–20 Q&A items.
  - Questions can be inferred from review themes and data gaps.
  - Answers MUST be grounded in review evidence; otherwise answer: "нет данных в отзывах".
- **SEO**:
  - **Title** (<= 60 chars): 5 вариантов
  - **Description** (<= 160 chars): 5 вариантов
  - **Ключевые фразы**: 20–60 phrases (clustered by intent: informational / transactional / local)
  - **Кластеризация**: 5–12 keyword clusters + suggested target sections
  - **Сниппеты**:
    - "Короткий сниппет" (1–2 предложения)
    - "Пункты-сниппеты" (6–12 буллетов)
  - **Внутренняя перелинковка**: 10–25 идей анкор-текстов и куда вести (generic placeholders allowed)
- **Дисклеймер**: 1–2 sentences that information is based on reviews and may change.

#### Рекомендации (быстрые / стратегические)
- Split into:
  - Быстрые (0–30 дней): 3–6 actions, concrete and testable.
  - Стратегические (1–6 месяцев): 2–5 actions.
- Tie each recommendation to at least one observed issue/opportunity.
 - Add a website-specific subsection at the end:
   - "Рекомендации для сайта" — 8–20 actionable items:
     - what to highlight
     - what to clarify (rules, prices, booking, how to get there)
     - what photos/sections to add
     - how to address recurring concerns transparently
   Each item must reference the underlying review signal (short paraphrase) and include 1 quote when possible.

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
            f"The data to analyze is in a local file at: {tmp_path}\n"
            "Open the file, read the JSON array, and produce the report following the required sections above.\n"
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

