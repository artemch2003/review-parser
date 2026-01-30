from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .codex_report import CodexReportConfig, generate_markdown_report
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


@app.command("analyze")
def analyze_cmd(
    inp: Path = typer.Argument(..., help="JSON-файл с отзывами (массив объектов Review)"),
    out: Path = typer.Option(Path("report.md"), "--out", "-o", help="Куда сохранить отчёт (Markdown)"),
    max_reviews: Optional[int] = typer.Option(
        None,
        "--max-reviews",
        help="Ограничить число отзывов для анализа (полезно для быстрого прогона)",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Модель для Codex CLI (если нужно переопределить)",
    ),
    codex_bin: str = typer.Option(
        "codex",
        "--codex-bin",
        help="Путь/имя бинаря Codex CLI (по умолчанию: codex)",
    ),
) -> None:
    """
    Генерирует Markdown-отчёт по отзывам через установленный Codex CLI.
    """
    cfg = CodexReportConfig(codex_bin=codex_bin, model=model, sandbox="read-only", output_language="ru")
    try:
        generate_markdown_report(inp, out, cfg, max_reviews=max_reviews)
    except FileNotFoundError as e:
        console.print(
            "[bold red]Не найден Codex CLI.[/bold red]\n"
            "Установи его (например):\n"
            "  [bold]npm install -g @openai/codex[/bold]\n"
            "И проверь, что команда [bold]codex[/bold] доступна в PATH."
        )
        raise typer.Exit(code=2) from e
    except Exception as e:
        console.print(f"[bold red]Ошибка анализа:[/bold red] {e}")
        raise typer.Exit(code=2) from e

    table = Table(title="Готово (анализ)")
    table.add_column("Параметр")
    table.add_column("Значение")
    table.add_row("Вход", str(inp))
    table.add_row("Отчёт", str(out))
    if model:
        table.add_row("Модель", model)
    console.print(table)


if __name__ == "__main__":
    app()

