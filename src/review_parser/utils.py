from __future__ import annotations

import re
from urllib.parse import urlparse


_ORG_ID_RE = re.compile(r"/(\d{6,})/?(?:\?|$)")


def extract_org_id(url: str) -> str | None:
    """
    Пытается достать org_id из URL вида:
    https://yandex.ru/maps/org/some-name/1754533743/
    """
    m = _ORG_ID_RE.search(url)
    if m:
        return m.group(1)

    # Фоллбек: иногда org_id бывает последним сегментом пути
    try:
        path = urlparse(url).path.rstrip("/")
        last = path.split("/")[-1]
        return last if last.isdigit() else None
    except Exception:
        return None


def normalize_url(url: str) -> str:
    return url.strip()

