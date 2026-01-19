from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .exporters import ExportFormat, export_reviews
from .yandex_maps.scraper import scrape_reviews_sync

app = typer.Typer(add_completion=False, help="CLI для выгрузки отзывов из Яндекс Карт.")
console = Console()


@app.callback()
def main() -> None:
    """
    Утилита для выгрузки отзывов из Яндекс Карт.
    """


@app.command("reviews")
def reviews_cmd(
    url: str = typer.Argument(..., help="URL карточки организации в Яндекс Картах"),
    out: Path = typer.Option(Path("reviews.json"), "--out", "-o", help="Куда сохранить результат"),
    fmt: ExportFormat = typer.Option(ExportFormat.json, "--format", "-f", help="Формат: json|csv"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Ограничить число отзывов"),
    headful: bool = typer.Option(False, "--headful", help="Показать браузер (для дебага)"),
    timeout_ms: int = typer.Option(45_000, "--timeout-ms", help="Таймаут ожиданий, мс"),
    debug_screenshot: Optional[Path] = typer.Option(
        None,
        "--debug-screenshot",
        help="Сохранить скриншот после скрейпа (полезно для диагностики)",
    ),
) -> None:
    """
    Вытягивает отзывы с карточки организации.
    """
    console.print(f"[bold]URL[/bold]: {url}")
    console.print(f"[bold]Вывод[/bold]: {out} ({fmt})")
    if limit:
        console.print(f"[bold]Лимит[/bold]: {limit}")

    try:
        reviews = scrape_reviews_sync(
            url,
            headless=not headful,
            timeout_ms=timeout_ms,
            limit=limit,
            debug_screenshot_path=str(debug_screenshot) if debug_screenshot else None,
        )
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            console.print(
                "[bold red]Не найден браузер Playwright.[/bold red]\n"
                "Похоже, Playwright установлен/обновлён, но Chromium ещё не скачан.\n\n"
                "Запусти:\n"
                "  [bold]python -m playwright install chromium[/bold]\n"
            )
            raise typer.Exit(code=2) from e
        raise

    export_reviews(reviews, out, fmt)

    table = Table(title="Готово")
    table.add_column("Параметр")
    table.add_column("Значение")
    table.add_row("Отзывы", str(len(reviews)))
    table.add_row("Файл", str(out))
    console.print(table)


if __name__ == "__main__":
    app()

