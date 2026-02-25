from __future__ import annotations
from typing import Iterable
from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()

try:
    from pyfiglet import Figlet  # type: ignore
    def _ascii(text: str) -> str:
        return Figlet(font="big").renderText(text)
except Exception:  # pragma: no cover - fallback when pyfiglet not installed
    def _ascii(text: str) -> str:
        return f"*** {text} ***\n"


def banner(text: str = "BeautifulEpics") -> None:
    console.print(Text(_ascii(text), style="bold magenta"))
    console.print(Text("Make epics beautiful! ✨🦋", style="bold magenta"))


def table_ok(items: Iterable[dict]) -> Table:
    t = Table(title="✨ Beautiful epics (OK)", expand=True, show_lines=False)
    t.add_column("✅", style="bold green")
    t.add_column("Ref", style="bold green")
    t.add_column("Name", style="green")
    t.add_column("Release", style="cyan")
    for it in items:
        t.add_row("✅", it.get("reference_num", "?"), f"[green]{it.get('name','')}[/]", it.get("release_name", ""), style="green")
    return t


def table_issues(items: Iterable[dict]) -> Table:
    t = Table(title="💥 Not beautiful (fix me)", expand=True, show_lines=True)
    t.add_column("Ref", style="bold red")
    t.add_column("Name", style="white")
    t.add_column("Problems", style="yellow")
    for it in items:
        problems = "\n".join(f"• [red]{p}[/]" for p in it.get("problems", []))
        t.add_row(it.get("reference_num", "?"), it.get("name", ""), problems)
    return t
