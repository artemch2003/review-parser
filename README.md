## review-parser

CLI на Python для выгрузки отзывов с карточек организаций в Яндекс Картах. Так как отзывы подгружаются динамически, используется **Playwright** (headless Chromium).

### Важно

- **Используйте инструмент ответственно**: соблюдайте правила сайта/сервиса и применимое законодательство.
- Яндекс может менять верстку/селекторы — если что-то перестало работать, включайте `--headful` и `--debug-screenshot` (ниже), чтобы быстро поправить селекторы.

### Установка

1) Создать окружение и поставить пакет:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

Альтернатива без editable-режима (если так удобнее):

```bash
pip install -r requirements.txt
python -m pip install .
```

2) Установить браузер для Playwright:

```bash
python -m playwright install chromium
```

### Использование

Пример URL:
`https://yandex.ru/maps/org/dom_pionerov/1754533743/?ll=65.823801%2C56.971634&z=16.9`

В JSON:

```bash
review-parser reviews "https://yandex.ru/maps/org/dom_pionerov/1754533743/" -o reviews.json --format json
```

В CSV:

```bash
review-parser reviews "https://yandex.ru/maps/org/dom_pionerov/1754533743/" -o reviews.csv --format csv
```

Полезные флаги:

- `--limit 200`: ограничить кол-во отзывов
- `--headful`: открыть браузер в видимом режиме (дебаг)
- `--timeout-ms 60000`: увеличить таймауты ожидания
- `--debug-screenshot debug.png`: сохранить скриншот в конце (для диагностики селекторов)

# review-parser
