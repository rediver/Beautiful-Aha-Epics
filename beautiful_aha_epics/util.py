from __future__ import annotations
from typing import Iterable
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

BANNER = r"""
██████  ███████  █████  ██    ██ ███████ ████████ ██    ██ ██      
██   ██ ██      ██   ██ ██    ██ ██         ██     ██  ██  ██      
██████  █████   ███████ ██    ██ ███████    ██      ████   ██      
██   ██ ██      ██   ██  ██  ██       ██    ██       ██    ██      
██   ██ ███████ ██   ██   ████   ███████    ██       ██    ███████ 
"""

console = Console()

def banner(app_name: str = "Beautiful Aha Epics") -> None:
    console.print(Panel.fit(Text.from_markup(f":rainbow[{app_name}]"), title="✨🦋✨", subtitle="Make epics [bold green]beautiful[/]!"))
    console.print(f"[bold magenta]{BANNER}[/]")


def table_ok(items: Iterable[dict]) -> Table:
    t = Table(title="✨ Beautiful epics (OK)", expand=True, show_lines=False)
    t.add_column("Ref", style="bold green")
    t.add_column("Name", style="white")
    t.add_column("Release", style="cyan")
    for it in items:
        t.add_row(it.get("reference_num", "?"), it.get("name", ""), it.get("release_name", ""))
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
