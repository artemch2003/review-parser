from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dateutil import parser as dtparser
from playwright.async_api import Page, async_playwright

from ..models import Review
from ..utils import extract_org_id, normalize_url


@dataclass(frozen=True)
class ScrapeOptions:
    headless: bool = True
    timeout_ms: int = 45_000
    limit: int | None = None
    debug_screenshot_path: str | None = None


_RE_RATING_INT = re.compile(r"([1-5])")


async def scrape_reviews(url: str, *, options: ScrapeOptions) -> list[Review]:
    url = normalize_url(url)
    org_id = extract_org_id(url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=options.headless)
        context = await browser.new_context(locale="ru-RU")
        page = await context.new_page()
        page.set_default_timeout(options.timeout_ms)

        # 1) Навигация
        await page.goto(url, wait_until="domcontentloaded")
        await _try_accept_cookies(page)

        # 2) Переходим к отзывам (разные варианты UI)
        await _open_reviews_section(page, timeout_ms=options.timeout_ms)

        # 3) Собираем отзывы инкрементально (учитываем виртуализацию списка)
        raw_items = await _collect_reviews(page, limit=options.limit)

        if options.debug_screenshot_path:
            await page.screenshot(path=options.debug_screenshot_path, full_page=True)

        await context.close()
        await browser.close()

    reviews: list[Review] = []
    seen: set[str] = set()
    for item in raw_items:
        review = _to_review(item, org_url=url, org_id=org_id)
        key = f"{review.author}|{review.date}|{review.rating}|{review.text}"
        if key in seen:
            continue
        seen.add(key)
        reviews.append(review)
        if options.limit is not None and len(reviews) >= options.limit:
            break

    return reviews


async def _try_accept_cookies(page: Page) -> None:
    # Очень "мягкий" best-effort: если баннер есть — жмём.
    candidates = [
        page.get_by_role("button", name=re.compile(r"^(Принять|Согласен|Accept)$", re.I)),
        page.get_by_text(re.compile(r"Принять", re.I)).first,
    ]
    for loc in candidates:
        try:
            if await loc.count() > 0:
                await loc.first.click(timeout=1500)
                return
        except Exception:
            continue


async def _open_reviews_section(page: Page, *, timeout_ms: int) -> None:
    # Часто отзывы — это вкладка/кнопка "Отзывы"
    locators = [
        page.get_by_role("tab", name=re.compile(r"Отзывы|Reviews", re.I)),
        page.get_by_role("link", name=re.compile(r"Отзывы|Reviews", re.I)),
        page.get_by_role("button", name=re.compile(r"Отзывы|Reviews", re.I)),
        page.get_by_text(re.compile(r"Отзывы", re.I)).first,
    ]
    for loc in locators:
        try:
            if await loc.count() > 0:
                await loc.first.click()
                break
        except Exception:
            continue

    # Ждём появления элементов отзывов (селекторы эвристические)
    await _wait_for_any_selector(
        page,
        selectors=[
            '[class*="business-reviews-card"]',
            '[class*="business-reviews-card-view"]',
            '[data-testid*="review"]',
            'text=/Написать отзыв/i',
        ],
        timeout_ms=timeout_ms,
    )


async def _collect_reviews(page: Page, *, limit: int | None) -> list[dict[str, Any]]:
    """
    Скроллим контейнер отзывов и собираем отзывы на каждой итерации.
    Это нужно, потому что список часто виртуализирован и в DOM одновременно
    находится только небольшое число карточек.
    """
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    no_new_rounds = 0
    at_bottom_rounds = 0

    for _ in range(800):  # предохранитель (на большие карточки)
        await _click_show_more_if_present(page)

        visible = await _extract_reviews_dom(page)
        added = 0
        for it in visible:
            key = f"{it.get('author')}|{it.get('date_text')}|{it.get('rating')}|{it.get('text')}"
            if key in seen:
                continue
            seen.add(key)
            collected.append(it)
            added += 1
            if limit is not None and len(collected) >= limit:
                return collected

        if added == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0

        scroll_info = await _scroll_reviews_container(page)
        if scroll_info.get("atBottom"):
            at_bottom_rounds += 1
        else:
            at_bottom_rounds = 0

        # Ждём догрузку/рендер после скролла
        await page.wait_for_timeout(450)

        # Условие остановки: несколько итераций подряд на дне и ничего нового не появилось
        if at_bottom_rounds >= 6 and no_new_rounds >= 6:
            return collected

    return collected


async def _scroll_reviews_container(page: Page) -> dict[str, Any]:
    """
    Скроллит контейнер, который реально содержит .business-review-view.
    Фоллбек: скролл всей страницы, если контейнер не найден.
    """
    js = r"""
() => {
  const first = document.querySelector('.business-review-view');
  if (!first) return { mode: 'none', moved: false, atBottom: true };

  // Ищем "лучшего" предка по максимально возможной прокрутке.
  // В Яндекс Картах нужный контейнер может быть довольно высоко по дереву (20+ уровней).
  let best = null;
  let cur = first;
  for (let i = 0; i < 30 && cur; i++) {
    const delta = (cur.scrollHeight || 0) - (cur.clientHeight || 0);
    if (delta > 200) {
      if (!best || delta > best.delta) best = { el: cur, delta };
    }
    cur = cur.parentElement;
  }

  const target = best ? best.el : null;
  if (!target) {
    window.scrollTo(0, document.body.scrollHeight);
    return { mode: 'window', atBottom: true, moved: true };
  }

  const before = target.scrollTop || 0;
  target.scrollTop = before + Math.max(1200, target.clientHeight || 0);
  const after = target.scrollTop || 0;
  const ch = target.clientHeight || 0;
  const sh = target.scrollHeight || 0;
  const atBottom = (after + ch) >= (sh - 5);
  return {
    mode: 'container',
    clientHeight: ch,
    scrollTop: after,
    scrollHeight: sh,
    moved: after !== before,
    atBottom,
  };
}
"""
    try:
        info = await page.evaluate(js)
        return info or {}
    except Exception:
        # worst-case: пробуем колесо мыши
        try:
            await page.mouse.wheel(0, 1800)
        except Exception:
            return {}
    return {}


async def _click_show_more_if_present(page: Page) -> None:
    locators = [
        page.get_by_role("button", name=re.compile(r"Показать.*ещ", re.I)),
        page.get_by_text(re.compile(r"Показать.*ещ", re.I)),
    ]
    for loc in locators:
        try:
            if await loc.count() > 0:
                await loc.first.click(timeout=1000)
                await page.wait_for_timeout(250)
                return
        except Exception:
            continue


async def _wait_for_any_selector(page: Page, *, selectors: list[str], timeout_ms: int) -> None:
    last_exc: Exception | None = None
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout_ms)
            return
        except Exception as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc


async def _extract_reviews_dom(page: Page) -> list[dict[str, Any]]:
    """
    Эвристический парсер DOM.
    Возвращает список dict, чтобы дальше можно было преобразовать в pydantic-модель.
    """
    js = r"""
() => {
  const pickText = (el) => (el && el.textContent ? el.textContent.trim() : null);
  const q = (root, sel) => root.querySelector(sel);

  const parseRating = (root) => {
    const r = q(root, '.business-review-view__rating [aria-label]') || q(root, '[aria-label]');
    if (!r) return null;
    const aria = r.getAttribute('aria-label') || '';
    if (!/оцен|рейтинг|rating/i.test(aria)) return null;
    const m = aria.match(/([1-5])/);
    return m ? parseInt(m[1], 10) : null;
  };

  const parseAuthor = (root) =>
    pickText(q(root, '.business-review-view__author-name')) ||
    pickText(q(root, '.business-review-view__author-info'));

  const parseDate = (root) => pickText(q(root, '.business-review-view__date'));

  const parseBody = (root) => pickText(q(root, '.business-review-view__body'));

  const nodes = Array.from(document.querySelectorAll('.business-review-view'));

  const out = [];
  for (const n of nodes) {
    const rating = parseRating(n);
    const dateText = parseDate(n);
    const author = parseAuthor(n);
    const text = parseBody(n);
    if (!rating && !text) continue;
    out.push({
      author,
      rating,
      date_text: dateText,
      text,
    });
  }
  return out;
}
"""
    items: list[dict[str, Any]] = await page.evaluate(js)
    return items or []


def _to_review(item: dict[str, Any], *, org_url: str, org_id: str | None) -> Review:
    rating = _coerce_rating(item.get("rating"))
    date = _coerce_date(item.get("date_text"))
    author = _coerce_str(item.get("author"))
    text = _coerce_str(item.get("text"))

    return Review(
        org_id=org_id,
        org_url=org_url,
        author=author,
        rating=rating,
        date=date,
        text=text,
        raw=item,
    )


def _coerce_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _coerce_rating(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int) and 1 <= v <= 5:
        return v
    m = _RE_RATING_INT.search(str(v))
    if not m:
        return None
    r = int(m.group(1))
    return r if 1 <= r <= 5 else None


def _coerce_date(v: Any) -> datetime | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    low = s.lower()
    # Защита от мусора вроде "317 отзывов" / "По умолчанию"
    if "отзыв" in low or "по умолчанию" in low:
        return None

    # 1) Русские даты вида "4 сентября 2025" / "29 июля" (возможны без года)
    months = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
    }
    m = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", low, flags=re.I)
    if m:
        day = int(m.group(1))
        mon_name = m.group(2).lower()
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        mon = months.get(mon_name)
        if mon and 1 <= day <= 31:
            try:
                return datetime(year, mon, day)
            except ValueError:
                return None

    # 2) Относительные даты: "сегодня", "вчера", "3 дня назад"
    now = datetime.now()
    if "сегодня" in low:
        return datetime(now.year, now.month, now.day)
    if "вчера" in low:
        d = now - timedelta(days=1)
        return datetime(d.year, d.month, d.day)
    m2 = re.search(r"\b(\d+)\s+(дн|дня|дней)\s+назад\b", low)
    if m2:
        d = now - timedelta(days=int(m2.group(1)))
        return datetime(d.year, d.month, d.day)

    # 3) Фоллбек: dateutil для числовых/английских дат
    try:
        dt = dtparser.parse(s, dayfirst=True, fuzzy=True)
        # Отсекаем явные артефакты (например, год 0317)
        if dt.year < 1990 and not any(x in low for x in ["сегодня", "вчера", "дн", "нед"]):
            return None
        return dt
    except Exception:
        return None


def scrape_reviews_sync(
    url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 45_000,
    limit: int | None = None,
    debug_screenshot_path: str | None = None,
) -> list[Review]:
    options = ScrapeOptions(
        headless=headless,
        timeout_ms=timeout_ms,
        limit=limit,
        debug_screenshot_path=debug_screenshot_path,
    )
    return asyncio.run(scrape_reviews(url, options=options))

