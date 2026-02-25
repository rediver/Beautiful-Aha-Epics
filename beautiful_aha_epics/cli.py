from __future__ import annotations
import json
import os
from typing import List, Optional

import typer
from rich import box
from rich.console import Console
from rich.progress import track

from .client import AhaClient
from .config import AppConfig
from .checks import evaluate_epic
from .util import banner, table_ok, table_issues

app = typer.Typer(add_completion=False, help="Beautiful Aha Epics ✨🦋 – keep your Aha! epics beautiful per IBM PM checks")
console = Console()


@app.command()
def init_config(path: str = typer.Option("bae.config.example.yaml", help="Where to write example config")):
    """Create an example config file you can tweak."""
    AppConfig.dump_example(path)
    console.print(f"Wrote example config to [bold]{path}[/]. Edit it and copy to [bold]bae.config.yaml[/] if desired ✍️")


@app.command()
def check(
    product_name: Optional[str] = typer.Option(None, help="Aha! Product/Workspace name"),
    releases: List[str] = typer.Option([], "--releases", help="Release names to include (multiple)"),
    tags: List[str] = typer.Option(["scanners"], "--tags", help="Required tags (all must be present)"),
    tags_one_of: List[str] = typer.Option(["lineage dev commited"], help="At least one of these tags must be present"),
    pm_owner: Optional[str] = typer.Option(None, help="Expected Product Management owner value (custom field)"),
    json_output: bool = typer.Option(False, "--json", help="Print machine JSON instead of fancy tables"),
    verbose: bool = typer.Option(False, "-v", help="Verbose logs"),
):
    """Validate epics and print a glorious, colorful report with emojis and ASCII art."""
    banner()
    cfg = AppConfig.load()

    # CLI overrides config
    product_name = product_name or cfg.product_name
    pm_owner = pm_owner or cfg.filters.pm_owner
    if not product_name:
        typer.echo("--product-name or config.product_name is required", err=True)
        raise typer.Exit(code=2)

    if not releases:
        releases = cfg.filters.releases
    required_tags = list(set(tags or cfg.filters.tags_include))
    required_one_of = list(set(tags_one_of or cfg.filters.tags_one_of))

    # Auth check (env vars) – don't print the token
    if not (os.getenv("BAE_AHA_ACCOUNT") or cfg.account):
        typer.echo("Set BAE_AHA_ACCOUNT=<subdomain>, e.g., bigblue", err=True)
        raise typer.Exit(code=2)
    if not (os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")):
        typer.echo("Set BAE_AHA_TOKEN={{AHA_API_TOKEN}} in your environment (do not commit it)", err=True)
        raise typer.Exit(code=2)

    client = AhaClient(account=cfg.account)

    # Resolve product -> release ids
    product = client.find_product_by_name(product_name)
    if not product:
        typer.echo(f"Product '{product_name}' not found in Aha!", err=True)
        raise typer.Exit(code=2)

    release_ids = []
    if releases:
        name_to_id = client.map_release_names_to_ids(product["id"], releases)
        missing = [n for n, rid in name_to_id.items() if not rid]
        if missing:
            console.print(f"[yellow]⚠️ Some releases not found:[/]: {', '.join(missing)} – they will be ignored")
        release_ids = [rid for rid in name_to_id.values() if rid]

    # Gather epic IDs from releases (or fallback to all epics filtered by tag)
    raw_epics = []
    if release_ids:
        for rid in release_ids:
            for e in client.iter_release_epics(rid, tag=None):  # tag filter will be applied later to be safe
                raw_epics.append(e)
    else:
        for e in client.iter_all_epics(tag=None):
            raw_epics.append(e)

    if verbose:
        console.log(f"Fetched {len(raw_epics)} epics (pre-filter)")

    # Filter by tags and PM owner (using custom_fields requires full fetch per epic)
    # We'll fetch full details for each epic now for accurate checks.
    full_epics = []
    for e in track(raw_epics, description="Downloading epic details"):
        eid = str(e.get("id"))
        full = client.get_epic(eid)
        full_epics.append(full)

    # Apply release-name filter (epic might have been pulled without release context)
    if releases:
        wanted = set([r.lower() for r in releases])
        def rel_ok(ep):
            name = None
            if isinstance(ep.get("release"), dict):
                name = ep.get("release", {}).get("name")
            name = name or ep.get("release_name")
            return (name or "").strip().lower() in wanted
        full_epics = [e for e in full_epics if rel_ok(e)]

    # Tags filter (all required must be present)
    def has_tags(ep) -> bool:
        tags_set = set([str(t).lower() for t in (ep.get("tags") or [])])
        return all(t.lower() in tags_set for t in required_tags)

    full_epics = [e for e in full_epics if has_tags(e)]

    # Evaluate
    results = []
    for e in full_epics:
        res = evaluate_epic(
            e,
            cfg.fields,
            required_tag_all=required_tags,
            required_tag_one_of=required_one_of,
            pm_owner_expect=pm_owner,
        )
        # Keep some lightweight projection for table rendering
        results.append({
            "id": res.epic_id,
            "reference_num": e.get("reference_num") or e.get("reference_num_with_prefix") or res.epic_id,
            "name": e.get("name") or e.get("title") or "<no title>",
            "release_name": (e.get("release") or {}).get("name") if isinstance(e.get("release"), dict) else (e.get("release_name") or ""),
            "ok": res.ok,
            "problems": res.problems,
        })

    beautiful = [r for r in results if r["ok"]]
    not_beautiful = [r for r in results if not r["ok"]]

    if json_output:
        typer.echo(json.dumps({"ok": beautiful, "not_ok": not_beautiful}, ensure_ascii=False, indent=2))
        raise typer.Exit(0)

    # Pretty print with emojis and colors
    console.rule("🦋 Results 🦋")
    if beautiful:
        console.print(table_ok(beautiful))
    else:
        console.print("[cyan]No beautiful epics found yet — time to add some ✨![/]")

    console.print()
    if not_beautiful:
        console.print(table_issues(not_beautiful))
        console.print("\n[bold red]Action needed:[/] bring these epics to beauty with love, colors and discipline 💪🌈")
        raise typer.Exit(1)
    else:
        console.print("[bold green]All checked epics are BEAUTIFUL! ✨🌟🎉[/]")
        raise typer.Exit(0)


if __name__ == "__main__":
    app()