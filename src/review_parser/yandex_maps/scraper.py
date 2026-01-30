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

        # 3) Собираем отзывы на лету при скролле.
        # В Яндекс.Картах часто используется виртуализация списка: в DOM одновременно
        # находится ограниченное число карточек (например ~50), поэтому "снять слепок"
        # DOM в конце недостаточно — нужно накапливать элементы по мере прокрутки.
        raw_items = await _collect_reviews_while_scrolling(page, limit=options.limit)

        if options.debug_screenshot_path:
            # Best-effort: дебажный скриншот не должен ломать основной сбор отзывов.
            # full_page=True на тяжёлых страницах Яндекса иногда подвисает на ожидании шрифтов/рендера.
            try:
                await page.screenshot(
                    path=options.debug_screenshot_path,
                    full_page=True,
                    timeout=options.timeout_ms,
                )
            except Exception:
                try:
                    await page.screenshot(
                        path=options.debug_screenshot_path,
                        full_page=False,
                        timeout=min(options.timeout_ms, 15_000),
                    )
                except Exception:
                    pass

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


async def _collect_reviews_while_scrolling(page: Page, *, limit: int | None) -> list[dict[str, Any]]:
    """
    Скроллим вниз и кликаем "Показать ещё", параллельно накапливая отзывы.
    Это важно, потому что список может быть виртуализирован (DOM держит только часть элементов).
    """
    stable_rounds = 0  # сколько итераций подряд без новых уникальных отзывов
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for _ in range(260):  # предохранитель
        # "Показать ещё" встречается довольно часто
        await _click_show_more_if_present(page)

        # Скролл: стараемся скроллить именно контейнер со списком отзывов (если найден),
        # иначе — скроллим окно.
        await _scroll_reviews_area(page, delta=2500)

        # Даём Яндексу время подгрузить следующую порцию.
        await page.wait_for_timeout(900)

        items = await _extract_reviews_dom(page)
        new_in_round = 0
        for it in items:
            # Делаем ключ из наиболее стабильных полей; порядок в out сохраняем.
            author = (it.get("author") or "").strip()
            date_text = (it.get("date_text") or "").strip()
            rating = it.get("rating")
            text = (it.get("text") or "").strip()
            key = f"{author}|{date_text}|{rating}|{text}"
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            new_in_round += 1
            if limit is not None and len(out) >= limit:
                return out[:limit]

        if new_in_round == 0:
            stable_rounds += 1
        else:
            stable_rounds = 0

        # Немного более "терпеливое" завершение: Яндекс иногда догружает пачками.
        if stable_rounds >= 20:
            return out

    return out


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


async def _scroll_reviews_area(page: Page, *, delta: int) -> None:
    """
    Пытаемся прокрутить ближайший скроллируемый контейнер, внутри которого есть отзывы.
    Если не удалось — прокручиваем окно.
    """
    # 0) Попытка: довести последний отзыв до видимости (часто триггерит lazy-load/перерисовку)
    try:
        last = page.locator(".business-review-view").last
        if await last.count() > 0:
            await last.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass

    # 1) Попытка: скролл ближайшего скроллируемого контейнера, содержащего отзывы
    try:
        did = await page.evaluate(
            """
() => {
  const review = document.querySelector('.business-review-view');
  if (!review) return false;
  let el = review.parentElement;
  while (el) {
    const style = window.getComputedStyle(el);
    const overflowY = style ? style.overflowY : '';
    const scrollable =
      (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') &&
      (el.scrollHeight > el.clientHeight + 5);
    if (scrollable) {
      el.scrollTop = el.scrollTop + %d;
      return true;
    }
    el = el.parentElement;
  }
  return false;
}
            """
            % int(delta)
        )
        if did:
            return
    except Exception:
        # Фоллбеки ниже
        pass

    # 2) Попытка: навести мышь на отзыв и покрутить колесом (часто важно для внутреннего контейнера)
    try:
        first = page.locator(".business-review-view").first
        if await first.count() > 0:
            await first.hover(timeout=1500)
        await page.mouse.wheel(0, delta)
    except Exception:
        try:
            await page.keyboard.press("PageDown")
        except Exception:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")


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

