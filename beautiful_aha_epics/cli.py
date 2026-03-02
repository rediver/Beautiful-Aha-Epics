from __future__ import annotations
import json
import os
from typing import List, Optional

import typer
from rich.console import Console
from rich.progress import track
import yaml
import stat

from .client import AhaClient
from .config import AppConfig
from .checks import evaluate_epic, evaluate_feature, _custom_to_dict, _norm_text
from .util import banner, table_ok, table_issues
from rich.table import Table
from typing import Set
import re

app = typer.Typer(
    add_completion=False,
    help=(
        "BeautifulEpics – colorful CLI to keep your Aha! items beautiful.\n\n"
        "Defaults: reads bae.config.yaml (account/product_key/release_ids/fields).\n"
        "When run via 'beauty' with no args, executes 'check'. Use 'beauty --help' to see 'check' flags.\n"
        "Env: BAE_AHA_ACCOUNT, BAE_AHA_TOKEN (or config.auth.token), BAE_MAX_CONCURRENCY (default 15)."
    ),
)
console = Console()

DEFAULT_CFG_PATH = "bae.config.yaml"


@app.command()
def init_config(path: str = typer.Option("bae.config.example.yaml", help="Where to write example config")):
    """Create an example config file you can tweak."""
    AppConfig.dump_example(path)
    console.print(f"Wrote example config to [bold]{path}[/]. Edit it and copy to [bold]{DEFAULT_CFG_PATH}[/] if desired ✍️")


@app.command()
def check(
    product_name: Optional[str] = typer.Option(None, help="Aha! Product/Workspace name (leaf). Default: config.product_name"),
    product_key: Optional[str] = typer.Option(None, help="Aha! product key from URL, e.g., DATALIN"),
    product_path: List[str] = typer.Option([], "--product-path", help="Path segments to the product (from root to leaf). Provide multiple --product-path flags in order."),
    releases: Optional[List[str]] = typer.Option(None, "--releases", help="Release names to include (multiple). If omitted, use config."),
    release_ids: Optional[List[str]] = typer.Option(None, "--release-ids", help="Release IDs to include (multiple). Overrides --releases mapping."),
    ignore_releases: bool = typer.Option(False, help="Ignore release filter entirely, even if present in config"),
    tags: Optional[List[str]] = typer.Option(None, "--tags", help="Required tags (all must be present). If omitted, use config."),
    tags_one_of: Optional[List[str]] = typer.Option(None, help="At least one of these tags must be present. If omitted, use config."),
    pm_owner: Optional[str] = typer.Option(None, help="Expected Product Management owner value (custom field)"),
    json_output: bool = typer.Option(False, "--json", help="Print machine JSON instead of fancy tables"),
    verify: bool = typer.Option(False, "--verify", "-v", help="Show a verification table with all fields we validate (no description text, only a checkmark)"),
    sort_by: Optional[str] = typer.Option(None, "--sort", "-s", help="Sort results by column. Examples: ref,name,release,status,pm_owner,dev_owner,priority"),
    debug: bool = typer.Option(False, "--debug", help="Verbose logs for troubleshooting"),
):
    """Validate epics and print a glorious, colorful report with emojis and ASCII art."""
    banner("BeautifulEpics")
    cfg = AppConfig.load()

    # CLI overrides config
    product_name = product_name or cfg.product_name
    product_key = product_key or cfg.product_key
    product_path = product_path or cfg.product_path
    pm_owner = pm_owner or cfg.filters.pm_owner
    if not product_name and not product_path and not product_key:
        typer.echo("Provide --product-name or --product-key or --product-path (or set them in bae.config.yaml)", err=True)
        raise typer.Exit(code=2)

    if ignore_releases:
        releases = []
        release_ids = []
    elif releases is None and release_ids is None:
        releases = cfg.filters.releases
        release_ids = cfg.filters.release_ids or None
    # If user passed only empty strings, disable release filtering
    if releases and all((r or "").strip() == "" for r in releases):
        releases = []
    required_tags = list({t for t in ((cfg.filters.tags_include if tags is None else tags) or []) if t})
    required_one_of = list({t for t in ((cfg.filters.tags_one_of if tags_one_of is None else tags_one_of) or []) if t})

    # Auth check – support env vars or token in config
    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account:
        typer.echo("Set account via config.account or BAE_AHA_ACCOUNT", err=True)
        raise typer.Exit(code=2)
    if not token:
        typer.echo("Provide token via config.auth.token or env var BAE_AHA_TOKEN", err=True)
        raise typer.Exit(code=2)

    client = AhaClient(account=account, token=token)

    # Resolve product via path > key > name
    product = None
    if product_path:
        product = client.find_product_by_path(product_path)
    if not product and product_key:
        product = client.find_product_by_key(product_key)
    if not product and (product_name or ""):
        product = client.find_product_by_name(product_name or "")

    if not product:
        hint = (
            " > ".join(product_path) if product_path else (product_key or product_name or "")
        )
        typer.echo(f"Product not found: {hint}", err=True)
        raise typer.Exit(code=2)

    resolved_release_ids = list(release_ids or []) if release_ids else []
    if releases and not resolved_release_ids:
        name_to_id = client.map_release_names_to_ids(product["id"], releases)
        missing = [n for n, rid in name_to_id.items() if not rid]
        if missing:
            console.print(f"[yellow]⚠️ Some releases not found:[/]: {', '.join(missing)} – they will be ignored")
        resolved_release_ids = [rid for rid in name_to_id.values() if rid]

    # Gather epic IDs from releases (or fallback to all epics filtered by tag)
    raw_epics = []
    selected_features: List[dict] = []
    if resolved_release_ids:
        if debug:
            console.log(f"Fetching epics for release IDs: {', '.join([str(x) for x in resolved_release_ids])}")
        # Collect ALL features from the releases (no server-side tag prefilter)
        feature_ids: List[str] = []
        per_release_counts = {}
        for rid in resolved_release_ids:
            count = 0
            for f in client.iter_release_features(rid, tag=None):
                feature_ids.append(str(f.get("id")))
                count += 1
            per_release_counts[str(rid)] = count
        if debug:
            console.log(f"Feature IDs collected (no server tag prefilter): {len(feature_ids)} | per release: " + ", ".join([f"{k}={v}" for k,v in per_release_counts.items()]))
        # fetch details in parallel
        concurrency = int(os.getenv("BAE_MAX_CONCURRENCY", "15"))
        details = client.fetch_features_many(feature_ids, concurrency=concurrency)
        if debug:
            console.log(f"Fetched feature details: {len(details)} (concurrency={concurrency})")
        # apply tag policy (one-of) locally; the 'all' scanners already filtered on server
        req_all = [t.lower() for t in (cfg.filters.tags_include if tags is None else (tags or []))]
        req_one = [t.lower() for t in (cfg.filters.tags_one_of if tags_one_of is None else (tags_one_of or []))]
        # Support CSV in pm_owner from CLI/config
        pm_raw = pm_owner or cfg.filters.pm_owner or ""
        expected_pms_raw: List[str] = [p.strip() for p in str(pm_raw).split(",") if p and p.strip()]
        expected_pms_l = [p.lower() for p in expected_pms_raw]
        kept_by_all = 0
        kept_by_one_of = 0
        kept_by_pm = 0
        for ff in details:
            tags_set = set([str(t).lower() for t in (ff.get("tags") or [])])
            all_ok = (all(t in tags_set for t in req_all) if req_all else True)
            one_ok = (any(t in tags_set for t in req_one) if req_one else True)
            # PM owner filter: include only if empty OR contains ANY of expected_pms
            pm_emails: List[str] = []
            cfs = ff.get("custom_fields") or []
            if isinstance(cfs, list):
                for it in cfs:
                    if isinstance(it, dict) and (it.get("key") or it.get("name")) in ("product_management_owner", "pm_owner", "product_management_owner_email"):
                        ev = it.get("email_value") or it.get("value") or []
                        def _split_emails(s: str) -> List[str]:
                            try:
                                import re as _re
                                return [x.strip() for x in _re.split(r"[;,]", s) if x and x.strip()]
                            except Exception:
                                return [s]
                        if isinstance(ev, str):
                            pm_emails = _split_emails(ev)
                        elif isinstance(ev, list):
                            tmp: List[str] = []
                            for x in ev:
                                if isinstance(x, str):
                                    tmp.extend(_split_emails(x))
                                elif isinstance(x, dict):
                                    # best effort: try common keys
                                    for kk in ("email", "value", "text_value"):
                                        if kk in x and isinstance(x[kk], str):
                                            tmp.extend(_split_emails(x[kk]))
                                            break
                                    else:
                                        tmp.append(str(x))
                                else:
                                    tmp.append(str(x))
                            pm_emails = tmp
                        break
            pm_emails_l = [e.strip().lower() for e in pm_emails if e]
            pm_ok = (len(pm_emails_l) == 0) or (expected_pms_l and any(ep in pm_emails_l for ep in expected_pms_l))
            if all_ok:
                kept_by_all += 1
            if one_ok:
                kept_by_one_of += 1
            if pm_ok:
                kept_by_pm += 1
            if all_ok and one_ok and pm_ok and ff:
                selected_features.append(ff)
        used_feature_tag_filter = True
        if debug:
            console.log(f"After filters: selected_features={len(selected_features)} (all_kept={kept_by_all}, one_of_kept={kept_by_one_of}, pm_kept={kept_by_pm})")
    else:
        # Fallback path (no release_ids): scan product epics (unchanged)
        for e in client.iter_product_epics(product["id"], tag=None):
            raw_epics.append(e)
        # Fallback if API doesn't support product epics
        if not raw_epics:
            for e in client.iter_all_epics(tag=None):
                # keep only epics that belong to the selected product (when field available)
                pid = (e.get("product") or {}).get("id") or (e.get("workspace") or {}).get("id")
                if pid and str(pid) != str(product["id"]):
                    continue
                raw_epics.append(e)

    if debug:
        console.log(f"Fetched {len(raw_epics)} epics (pre-filter)")

    # Fetch full details for each epic now for accurate checks.
    full_epics = []
    # If we already fetched selected_features fast path, skip epic details
    if not (resolved_release_ids and ('used_feature_tag_filter' in locals() and used_feature_tag_filter)):
        for e in track(raw_epics, description="Downloading epic details"):
            eid = str(e.get("id"))
            full = client.get_epic(eid)
            full_epics.append(full)

    # Apply release-name filter (epic might have been pulled without release context)
    if releases and not resolved_release_ids:
        wanted = set([r.lower() for r in releases])
        def rel_ok(ep):
            name = None
            if isinstance(ep.get("release"), dict):
                name = ep.get("release", {}).get("name")
            name = name or ep.get("release_name")
            return (name or "").strip().lower() in wanted
        before = len(full_epics)
        full_epics = [e for e in full_epics if rel_ok(e)]
        if debug:
            console.log(f"After release filter: {len(full_epics)} / {before}")

    # Tags filter on EPIC only if we did NOT already select by feature tags
    def has_tags(ep) -> bool:
        tags_set = set([str(t).lower() for t in (ep.get("tags") or [])])
        return all(t.lower() in tags_set for t in required_tags)

    before_tags = len(full_epics)
    if not (resolved_release_ids and ('used_feature_tag_filter' in locals() and used_feature_tag_filter)):
        full_epics = [e for e in full_epics if has_tags(e)]
    if debug:
        console.log(f"After tags filter: {len(full_epics)} / {before_tags} (need all: {required_tags or '[]'})")

    # Evaluate
    results = []
    if resolved_release_ids and ('used_feature_tag_filter' in locals() and used_feature_tag_filter):
        # We validated by feature tags; evaluate FEATURES instead of master epics
        eval_items = selected_features  # populated earlier
        for f in eval_items:
            res = evaluate_feature(
                f,
                cfg.fields,
                required_tag_all=required_tags,
                required_tag_one_of=required_one_of,
                pm_owner_expect=expected_pms_raw,
            )
            results.append({
                "id": res.epic_id,
                "reference_num": f.get("reference_num") or res.epic_id,
                "name": f.get("name") or "<no title>",
                "release_name": (f.get("release") or {}).get("name") if isinstance(f.get("release"), dict) else "",
                "ok": res.ok,
                "problems": res.problems,
            })
    else:
        for e in full_epics:
            res = evaluate_epic(
                e,
                cfg.fields,
                required_tag_all=required_tags,
                required_tag_one_of=required_one_of,
                pm_owner_expect=[p.strip() for p in str(pm_owner or cfg.filters.pm_owner or '').split(',') if p and p.strip()],
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

    # Optional sorting of base results (by simple columns available)
    if sort_by and not verify:
        keymap = {
            "ref": lambda r: (r.get("reference_num") or "").lower(),
            "name": lambda r: (r.get("name") or "").lower(),
            "release": lambda r: (r.get("release_name") or "").lower(),
            "problems": lambda r: len(r.get("problems") or []),
        }
        kf = keymap.get((sort_by or "").strip().lower())
        if kf:
            results = sorted(results, key=kf)

    beautiful = [r for r in results if r["ok"]]
    not_beautiful = [r for r in results if not r["ok"]]

    if json_output and not verify:
        typer.echo(json.dumps({"ok": beautiful, "not_ok": not_beautiful}, ensure_ascii=False, indent=2))
        raise typer.Exit(0)

    # Pretty print with emojis and colors
    console.rule("🦋 Results 🦋")

    # Optional verification view with all fields
    if verify:
        def _status_value(obj: dict) -> str:
            status = ((obj.get("workflow_status") or {}).get("name") or obj.get("status") or obj.get("workflow_status_name") or "").strip()
            wst = obj.get("workflow_status_times") or []
            for rec in wst or []:
                if rec and rec.get("ended_at") in (None, "") and rec.get("status_name"):
                    status = rec.get("status_name")
            return str(status or "")

        def _desc_ok(obj: dict, is_feature: bool) -> bool:
            if is_feature:
                desc = obj.get("description", {})
                body = (desc or {}).get("body") if isinstance(desc, dict) else desc
                return bool(_norm_text(body))
            else:
                return bool(_norm_text(obj.get("description")))

        def _github_present(obj: dict, custom: dict) -> bool:
            # integration_fields first
            for f in obj.get("integration_fields", []) or []:
                vals = [f.get("url"), f.get("name"), f.get("value"), f.get("service_name")]
                if any(((v or "").lower().find("github") >= 0) or ("git" in (v or '').lower() and "http" in (v or '').lower()) for v in vals):
                    return True
            # custom fields mapping
            for k in cfg.fields.github_link:
                v = str(custom.get(k) or "")
                if ("github" in v.lower()) or ("git" in v.lower() and "http" in v.lower()):
                    return True
            return False

        def _has_scanners(obj: dict) -> bool:
            tags = set([str(t).strip().lower() for t in (obj.get("tags") or [])])
            return "scanners" in tags

        def _release_name(obj: dict) -> str:
            rel = obj.get("release")
            if isinstance(rel, dict):
                return str(rel.get("name") or "")
            return str(obj.get("release_name") or "")

        def _release_dates_ok(obj: dict) -> bool:
            rel = obj.get("release")
            if isinstance(rel, dict):
                return bool(rel.get("start_date") and rel.get("release_date"))
            # If only name present, we don't know dates -> treat as False when verify expects both
            return False

        def _pm_owner(custom: dict) -> str:
            v = custom.get(cfg.fields.product_management_owner)
            if isinstance(v, list):
                return ", ".join([str(x) for x in v])
            return str(v or "")

        def _mk_row(obj: dict, is_feature: bool) -> dict:
            custom = _custom_to_dict(obj.get("custom_fields"))
            row = {
                "ref": str(obj.get("reference_num") or obj.get("reference_num_with_prefix") or obj.get("id") or ""),
                "name": str(obj.get("name") or obj.get("title") or ""),
                "release": _release_name(obj),
                "desc_ok": "✅" if _desc_ok(obj, is_feature) else "❌",
                "status": _status_value(obj),
                "solution_value": str(custom.get(cfg.fields.solution_value_statement) or (custom.get("client_value_statement") if is_feature else "") or ""),
                "risk_status": str(custom.get(cfg.fields.risk_status) or ""),
                "commitment": str(custom.get(cfg.fields.commitment) or (custom.get("committed") if is_feature else "") or ""),
                "master_epic": (
                    "yes" if (is_feature and (isinstance(obj.get("epic"), dict) or isinstance(obj.get("master_feature"), dict))) else str(custom.get(cfg.fields.master_epic) or "")
                ),
                "github": "✅" if _github_present(obj, custom) else "❌",
                "tag_scanners": "✅" if _has_scanners(obj) else "❌",
                "pm_owner": _pm_owner(custom),
                "dev_owner": str(custom.get(cfg.fields.development_owner) or ""),
                "gtm_themes": str(custom.get(cfg.fields.ibm_software_gtm_themes) or ""),
                "priority": str(custom.get(cfg.fields.priority_data_ai) or custom.get("priority") or ""),
                "rel_dates_ok": "✅" if _release_dates_ok(obj) else "❌",
            }
            return row

        # Build rows from the same evaluation pool as we used above
        verify_rows = []
        if resolved_release_ids and ('used_feature_tag_filter' in locals() and used_feature_tag_filter):
            for f in selected_features:
                verify_rows.append(_mk_row(f, is_feature=True))
        else:
            for e in full_epics:
                verify_rows.append(_mk_row(e, is_feature=False))

        # Sorting for verify view
        if sort_by:
            keymap_v = {
                "ref": lambda r: (r.get("ref") or "").lower(),
                "name": lambda r: (r.get("name") or "").lower(),
                "release": lambda r: (r.get("release") or "").lower(),
                "status": lambda r: (r.get("status") or "").lower(),
                "risk": lambda r: (r.get("risk_status") or "").lower(),
                "risk_status": lambda r: (r.get("risk_status") or "").lower(),
                "pm_owner": lambda r: (r.get("pm_owner") or "").lower(),
                "dev_owner": lambda r: (r.get("dev_owner") or "").lower(),
                "priority": lambda r: (r.get("priority") or "").lower(),
            }
            k = (sort_by or "").strip().lower()
            kf = keymap_v.get(k)
            if kf:
                verify_rows = sorted(verify_rows, key=kf)
            elif debug:
                console.log(f"[debug] Unknown --sort key '{sort_by}'. Known: {', '.join(keymap_v.keys())}")

        # Render table
        vt = Table(title="🔎 Verification (all items & field values)", expand=True, show_lines=False)
        vt.add_column("Ref", style="cyan", no_wrap=True)
        vt.add_column("Name", style="white")
        vt.add_column("Release", style="magenta")
        vt.add_column("RelDates", style="magenta", no_wrap=True)
        vt.add_column("Desc", style="white", no_wrap=True)
        vt.add_column("Status", style="white", no_wrap=True)
        vt.add_column("Solution Value", style="white")
        vt.add_column("Risk", style="white")
        vt.add_column("Commitment", style="white")
        vt.add_column("MasterEpic", style="white", no_wrap=True)
        vt.add_column("GitHub", style="white", no_wrap=True)
        vt.add_column("scanners", style="white", no_wrap=True)
        vt.add_column("PM Owner", style="white")
        vt.add_column("Dev Owner", style="white")
        vt.add_column("GTM Themes", style="white")
        vt.add_column("Priority", style="white", no_wrap=True)
        for r in verify_rows:
            vt.add_row(
                r.get("ref",""), r.get("name",""), r.get("release",""), r.get("rel_dates_ok",""), r.get("desc_ok",""),
                r.get("status",""), r.get("solution_value",""), r.get("risk_status",""), r.get("commitment",""),
                str(r.get("master_epic","")), r.get("github",""), r.get("tag_scanners",""), r.get("pm_owner",""),
                r.get("dev_owner",""), r.get("gtm_themes",""), r.get("priority",""),
            )
        if debug:
            console.log(f"Verify rows rendered: {len(verify_rows)} (features_mode={bool(resolved_release_ids and ('used_feature_tag_filter' in locals() and used_feature_tag_filter))})")
        console.print(vt)
        # In verify mode, print only the verification table and exit without OK/Issues summary
        raise typer.Exit(0)

    if not beautiful and not not_beautiful:
        console.print("[yellow]🤷 No epics matched your filters.[/] [dim](Try relaxing releases/tags or check product path.)[/]")
        raise typer.Exit(2)

    if beautiful:
        console.print(table_ok(beautiful))

    console.print()
    if not_beautiful:
        console.print(table_issues(not_beautiful))
        # Rule summary (overall and per release)
        from collections import Counter, defaultdict
        # Per release
        by_rel = defaultdict(list)
        for r in not_beautiful:
            rel = (r.get("release_name") or "<no release>")
            by_rel[rel].append(r)
        if by_rel:
            console.print("\n[bold]Top issues by release:[/]")
            for rel in sorted(by_rel.keys(), key=lambda x: (str(x) or "").lower()):
                cc = Counter()
                for rr in by_rel[rel]:
                    for p in rr.get("problems", []):
                        cc[p] += 1
                console.print(f"[underline]{rel}[/]")
                for k, v in cc.most_common():
                    console.print(f"  • {k}: [bold]{v}[/]")
        # Overall
        c = Counter()
        for r in not_beautiful:
            for p in r.get("problems", []):
                c[p] += 1
        if c:
            console.print("\n[bold]Top issues (overall):[/]")
            for k, v in c.most_common():
                console.print(f"  • {k}: [bold]{v}[/]")
        console.print("\n[bold red]Action needed:[/] bring these epics to beauty with love, colors and discipline 💪🌈")
        raise typer.Exit(1)
    else:
        console.print("[bold green]All checked epics are BEAUTIFUL! ✨🌟🎉[/]")
        raise typer.Exit(0)


@app.command("auth-set-token")
def auth_set_token(
    token: Optional[str] = typer.Option(None, help="Aha! API token. If omitted, will prompt (hidden)")
):
    """Store token into bae.config.yaml (useful for local dev)."""
    cfg_path = os.getenv("BAE_CONFIG", DEFAULT_CFG_PATH)
    if token is None:
        token = typer.prompt("Enter Aha! API token", hide_input=True)
    # Merge existing YAML
    data = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    data.setdefault("auth", {})
    data["auth"]["token"] = token
    # Ensure file exists with 0600 perms
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    try:
        os.chmod(cfg_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    console.print(f"Saved token into [bold]{cfg_path}[/] (file mode 600 where supported).")


@app.command("list-releases")
def list_releases_cmd(
    product_name: Optional[str] = typer.Option(None),
    product_key: Optional[str] = typer.Option(None),
    product_path: List[str] = typer.Option([], "--product-path"),
):
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    product_name = product_name or cfg.product_name
    product_key = product_key or cfg.product_key
    product_path = product_path or cfg.product_path

    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token. See README.", err=True)
        raise typer.Exit(2)

    client = AhaClient(account=account, token=token)

    product = None
    if product_path:
        product = client.find_product_by_path(product_path)
    if not product and product_key:
        product = client.find_product_by_key(product_key)
    if not product and product_name:
        product = client.find_product_by_name(product_name)
    if not product:
        typer.echo("Product not found.", err=True)
        raise typer.Exit(2)

    releases = client.list_releases_for_product(product["id"]) or []
    t = Table(title="📦 Releases", expand=True)
    t.add_column("ID", style="cyan")
    t.add_column("Name", style="white")
    t.add_column("Ref", style="green")
    for r in releases:
        ref = r.get("reference_num") or r.get("reference_num_with_prefix") or ""
        t.add_row(str(r.get("id")), r.get("name", ""), str(ref))
    console.print(t)


@app.command("add-release")
def add_release_cmd(
    release: str = typer.Argument(..., help="Release ID, reference (e.g., DATALIN-R-29), or exact name"),
    product_name: Optional[str] = typer.Option(None, help="Override product name (else from config)"),
    product_key: Optional[str] = typer.Option(None, help="Override product key (else from config)"),
    product_path: List[str] = typer.Option([], "--product-path", help="Override product path (else from config)"),
    cfg_path: str = typer.Option(DEFAULT_CFG_PATH, "--config", help="bae.config.yaml path"),
    debug: bool = typer.Option(False, "--debug", help="Verbose logs"),
):
    """Resolve a release and append its numeric ID to filters.release_ids in bae.config.yaml.

    Examples:
      • beauty add-release 7515164732697196802
      • beauty add-release DATALIN-R-29
      • beauty add-release "June 2026 - IKC 5.4 and DI 2.4"
    """
    banner("BeautifulEpics")
    cfg = AppConfig.load()

    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token. See README.", err=True)
        raise typer.Exit(2)

    client = AhaClient(account=account, token=token)

    # Resolve product via overrides > config
    product_name = product_name or cfg.product_name
    product_key = product_key or cfg.product_key
    product_path = product_path or cfg.product_path

    product = None
    if product_path:
        product = client.find_product_by_path(product_path)
    if not product and product_key:
        product = client.find_product_by_key(product_key)
    if not product and product_name:
        product = client.find_product_by_name(product_name)
    if not product:
        typer.echo("Product not found.", err=True)
        raise typer.Exit(2)

    rid: Optional[str] = None
    raw = (release or "").strip()
    if raw.isdigit():
        rid = raw
    else:
        # Scan releases and try to match by reference code, name, or URL fragment
        rels = client.list_releases_for_product(product["id"]) or []
        target_norm = (raw or "").strip().lower()
        def _norm(s: Optional[str]) -> str:
            import re as _re
            return _re.sub(r"\s+", " ", (s or "").strip()).lower()
        for r in rels:
            candidates = set()
            # Reference-like fields
            for k in ("reference_num", "reference_num_with_prefix", "reference", "ref", "key"):
                v = r.get(k)
                if isinstance(v, str) and v:
                    candidates.add(v.strip())
            # Name
            nm = r.get("name")
            if isinstance(nm, str) and nm:
                candidates.add(nm.strip())
            # URL fragments
            for k in ("url", "html_url", "path"):
                u = r.get(k)
                if isinstance(u, str) and u:
                    if raw in u or raw in u.split("/")[-1]:
                        rid = str(r.get("id")) if r.get("id") else None
                        break
            if rid:
                break
            # Compare normalized tokens
            cand_norm = {_norm(c) for c in candidates if c}
            if target_norm in cand_norm:
                rid = str(r.get("id")) if r.get("id") else None
                break
            # Also allow exact raw containment in any candidate (best-effort)
            for c in candidates:
                if raw and raw.lower() == c.strip().lower():
                    rid = str(r.get("id")) if r.get("id") else None
                    break
            if rid:
                break

    if not rid:
        console.print("[red]Release not found.[/]")
        console.print("Use 'beauty list-releases' to see IDs and names, then re-run with the numeric ID or exact name.")
        raise typer.Exit(2)

    # Load and update YAML
    import yaml as _yaml
    data = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
    data.setdefault("filters", {})
    ids = [str(x) for x in (data["filters"].get("release_ids") or [])]
    if str(rid) not in ids:
        ids.append(str(rid))
    data["filters"]["release_ids"] = ids

    with open(cfg_path, "w", encoding="utf-8") as f:
        _yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    console.print(f"Added release ID [bold]{rid}[/] to filters.release_ids in [bold]{cfg_path}[/] ✅")


@app.command("list-epics")
def list_epics_cmd(
    product_name: Optional[str] = typer.Option(None),
    product_key: Optional[str] = typer.Option(None),
    product_path: List[str] = typer.Option([], "--product-path"),
    limit: int = typer.Option(50, help="Max epics to show"),
):
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    product_name = product_name or cfg.product_name
    product_key = product_key or cfg.product_key
    product_path = product_path or cfg.product_path

    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token. See README.", err=True)
        raise typer.Exit(2)

    client = AhaClient(account=account, token=token)

    product = None
    if product_path:
        product = client.find_product_by_path(product_path)
    if not product and product_key:
        product = client.find_product_by_key(product_key)
    if not product and product_name:
        product = client.find_product_by_name(product_name)
    if not product:
        typer.echo("Product not found.", err=True)
        raise typer.Exit(2)

    rows = []
    for e in client.iter_product_epics(product["id"]):
        rows.append(e)
        if len(rows) >= limit:
            break

    t = Table(title="🦋 Epics (first N)", expand=True, show_lines=False)
    t.add_column("ID", style="cyan")
    t.add_column("Ref", style="green")
    t.add_column("Name", style="white")
    t.add_column("Release", style="magenta")
    for e in rows:
        ref = e.get("reference_num") or e.get("reference_num_with_prefix") or str(e.get("id"))
        rel = (e.get("release") or {}).get("name") if isinstance(e.get("release"), dict) else (e.get("release_name") or "")
        t.add_row(str(e.get("id")), str(ref), e.get("name", ""), rel)
    console.print(t)


@app.command("list-features")
def list_features_cmd(
    release_id: str = typer.Argument(..., help="Release ID"),
    limit: int = typer.Option(50),
):
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token.", err=True)
        raise typer.Exit(2)
    client = AhaClient(account=account, token=token)
    rows = []
    for f in client.iter_release_features(release_id):
        rows.append(f)
        if len(rows) >= limit:
            break
    t = Table(title=f"🧩 Features in release {release_id}", expand=True)
    t.add_column("ID", style="cyan")
    t.add_column("Ref", style="green")
    t.add_column("Name", style="white")
    t.add_column("EpicRef", style="magenta")
    for f in rows:
        ref = f.get("reference_num") or f.get("reference_num_with_prefix") or str(f.get("id"))
        name = f.get("name", "")
        epic = f.get("epic") or f.get("master_feature") or f.get("master_epic") or {}
        epic_ref = epic.get("reference_num") if isinstance(epic, dict) else ""
        if not epic_ref:
            # try fetching full feature to get epic ref
            try:
                ff = client.get_feature(str(f.get("id")))
                epic = ff.get("epic") or ff.get("master_feature") or ff.get("master_epic") or {}
                epic_ref = epic.get("reference_num") if isinstance(epic, dict) else ""
            except Exception:
                pass
        t.add_row(str(f.get("id")), str(ref), name, epic_ref)
    console.print(t)


@app.command("show-epic")
def show_epic_cmd(
    epic_id: str = typer.Argument(..., help="Epic numeric ID or reference"),
    raw: bool = typer.Option(False, "--raw", help="Print full JSON from API")
):
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token. See README.", err=True)
        raise typer.Exit(2)
    client = AhaClient(account=account, token=token)
    data = client.get_epic(epic_id)
    if raw:
        import json as _json
        typer.echo(_json.dumps(data, ensure_ascii=False, indent=2))
        raise typer.Exit(0)
    # Print key fields quickly
    rel = data.get("release")
    rel_name = (rel or {}).get("name") if isinstance(rel, dict) else data.get("release_name")
    cf = data.get("custom_fields")
    if isinstance(cf, dict):
        cf_keys = list(cf.keys())
    elif isinstance(cf, list):
        cf_keys = [str((x.get("key") or x.get("name") or "?")) for x in cf if isinstance(x, dict)]
    else:
        cf_keys = []
    console.print({
        "id": data.get("id"),
        "reference_num": data.get("reference_num"),
        "name": data.get("name"),
        "release": rel_name,
        "tags": data.get("tags"),
        "custom_fields_keys": cf_keys,
    })


@app.command("fix-tags")
def fix_tags(
    product_name: Optional[str] = typer.Option(None),
    product_key: Optional[str] = typer.Option(None),
    product_path: List[str] = typer.Option([], "--product-path"),
    releases: Optional[List[str]] = typer.Option(None, "--releases"),
    release_ids: Optional[List[str]] = typer.Option(None, "--release-ids"),
    ignore_releases: bool = typer.Option(False),
    require: Optional[List[str]] = typer.Option(None, help="Tags that must be present (added if missing)"),
    one_of: Optional[List[str]] = typer.Option(None, help="At least one of these must be present (adds first missing if none present)"),
    apply: bool = typer.Option(False, help="Perform updates (default is dry-run)"),
    limit: int = typer.Option(1000, help="Max epics to process"),
):
    """Ensure tags on epics match policy (uses PUT /epics/:id with {"epic":{"tags":[..]}})."""
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    product_name = product_name or cfg.product_name
    product_key = product_key or cfg.product_key
    product_path = product_path or cfg.product_path

    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token. See README.", err=True)
        raise typer.Exit(2)

    client = AhaClient(account=account, token=token)

    # Resolve product
    product = None
    if product_path:
        product = client.find_product_by_path(product_path)
    if not product and product_key:
        product = client.find_product_by_key(product_key)
    if not product and product_name:
        product = client.find_product_by_name(product_name)
    if not product:
        typer.echo("Product not found.", err=True)
        raise typer.Exit(2)

    # Release filters
    if ignore_releases:
        releases = []
        release_ids = []
    elif releases is None and release_ids is None:
        releases = cfg.filters.releases

    # Build epic list
    epics = []
    resolved_release_ids = list(release_ids or []) if release_ids else []
    if releases and not resolved_release_ids:
        name_to_id = client.map_release_names_to_ids(product["id"], releases)
        resolved_release_ids = [rid for rid in name_to_id.values() if rid]
    if resolved_release_ids:
        for rid in resolved_release_ids:
            for e in client.iter_release_epics(rid):
                epics.append(e)
    else:
        for e in client.iter_product_epics(product["id"]):
            epics.append(e)
    if not epics:
        console.print("[yellow]No epics to process.[/]")
        raise typer.Exit(0)

    # Prepare tag policy
    must: Set[str] = set((require if require is not None else cfg.filters.tags_include) or [])
    one: List[str] = (one_of if one_of is not None else cfg.filters.tags_one_of) or []

    changed = 0
    for e in epics[:limit]:
        full = client.get_epic(str(e.get("id")))
        existing = set([str(t).strip() for t in (full.get("tags") or [])])
        desired = set(existing)
        desired |= {t for t in must if t}
        if one:
            if not any(t in desired for t in one):
                desired.add(one[0])  # add first preferred if none present
        if desired != existing:
            changed += 1
            console.print(f"[cyan]{full.get('reference_num')}[/] {full.get('name','')} → tags: {sorted(existing)} -> [bold]{sorted(desired)}[/]")
            if apply:
                client.update_epic_tags(str(full.get("id")), sorted(desired))
                console.print("  ✅ updated")
            else:
                console.print("  🧪 dry-run (use --apply to update)")

    if changed == 0:
        console.print("[green]All epics already satisfy tag policy.[/]")
    elif not apply:
        console.print(f"[yellow]{changed} epics would be updated. Re-run with --apply to apply changes.[/]")


@app.command("show-feature")
def show_feature_cmd(
    identifier: str = typer.Argument(..., help="Feature ID or reference (e.g., DATALIN-457)"),
    release_ids: Optional[List[str]] = typer.Option(None, "--release-ids", help="Search these releases for ref resolution"),
    raw: bool = typer.Option(True, "--raw", help="Print full JSON")
):
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token.", err=True)
        raise typer.Exit(2)
    client = AhaClient(account=account, token=token)

    feat_id = None
    # numeric?
    if identifier.isdigit():
        feat_id = identifier
    else:
        # scan releases from args or config to resolve reference
        search_rids = release_ids or (cfg.filters.release_ids or [])
        for rid in search_rids:
            for f in client.iter_release_features(rid):
                ref = (f.get("reference_num") or f.get("reference_num_with_prefix") or "").strip()
                if ref.lower() == identifier.strip().lower():
                    feat_id = str(f.get("id"))
                    break
            if feat_id:
                break
    if not feat_id:
        typer.echo("Feature not found (provide numeric ID or valid reference within known releases).", err=True)
        raise typer.Exit(2)

    data = client.get_feature(feat_id)
    import json as _json
    typer.echo(_json.dumps(data, ensure_ascii=False, indent=2))
    raise typer.Exit(0)


@app.command("find-epic")
def find_epic(
    needle: str = typer.Argument(..., help="Text or reference to search (e.g., 'DATALIN-457' or part of a title)"),
    product_name: Optional[str] = typer.Option(None),
    product_key: Optional[str] = typer.Option(None),
    product_path: List[str] = typer.Option([], "--product-path"),
    release_ids: Optional[List[str]] = typer.Option(None, "--release-ids", help="Restrict search to these release IDs (features)"),
    limit: int = typer.Option(200, help="Max results for query search"),
):
    """Brute-force epic resolver.

    Strategy:
    1) If needle looks like a feature ref (e.g., DATALIN-457), scan features in given/all releases and follow parent epic.
    2) Also query epics with q=needle as fallback.
    """
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    product_name = product_name or cfg.product_name
    product_key = product_key or cfg.product_key
    product_path = product_path or cfg.product_path

    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token.", err=True)
        raise typer.Exit(2)

    client = AhaClient(account=account, token=token)

    # Resolve product
    product = None
    if product_path:
        product = client.find_product_by_path(product_path)
    if not product and product_key:
        product = client.find_product_by_key(product_key)
    if not product and product_name:
        product = client.find_product_by_name(product_name)
    if not product:
        typer.echo("Product not found.", err=True)
        raise typer.Exit(2)

    epic_ids = set()
    feature_hits = []

    # 1) Search via features in releases
    looks_like_ref = bool(re.match(r"^[A-Z]+-\d+$", needle.strip()))
    search_in_releases = release_ids or list(client.iter_release_ids_for_product(product["id"]))
    for rid in search_in_releases:
        for f in client.iter_release_features(rid):
            ref = (f.get("reference_num") or f.get("reference_num_with_prefix") or "").strip()
            name = (f.get("name") or "").strip()
            hit = False
            if looks_like_ref and ref.lower() == needle.strip().lower():
                hit = True
            elif needle.lower() in name.lower():
                hit = True
            if hit:
                # fetch full feature to get parent epic
                ff = client.get_feature(str(f.get("id")))
                epic = ff.get("epic") or ff.get("master_feature") or ff.get("master_epic") or {}
                eid = epic.get("id") if isinstance(epic, dict) else None
                if eid:
                    epic_ids.add(str(eid))
                feature_hits.append({
                    "release_id": rid,
                    "feature_ref": ref or str(f.get("id")),
                    "feature_name": name,
                    "epic_id": eid,
                })

    # 2) Fallback: query epics directly
    epic_hits = []
    for e in client.find_epics_by_query(needle, limit=limit):
        epic_hits.append({
            "id": e.get("id"),
            "ref": e.get("reference_num") or e.get("reference_num_with_prefix"),
            "name": e.get("name"),
        })
        if e.get("id"):
            epic_ids.add(str(e.get("id")))

    # Print summary
    if feature_hits:
        t = Table(title="🧩 Feature matches -> Epic", expand=True)
        t.add_column("ReleaseID", style="cyan")
        t.add_column("Feature", style="green")
        t.add_column("Name", style="white")
        t.add_column("EpicID", style="magenta")
        for h in feature_hits:
            t.add_row(str(h["release_id"]), str(h["feature_ref"]), h["feature_name"], str(h.get("epic_id") or ""))
        console.print(t)

    if epic_hits:
        t2 = Table(title="🦋 Epic matches (q=)", expand=True)
        t2.add_column("EpicID", style="cyan")
        t2.add_column("Ref", style="green")
        t2.add_column("Name", style="white")
        for h in epic_hits:
            t2.add_row(str(h.get("id")), str(h.get("ref")), h.get("name") or "")
        console.print(t2)

    if epic_ids:
        console.print(f"[bold]Resolved Epic IDs:[/] {', '.join(sorted(epic_ids))}")
        raise typer.Exit(0)
    else:
        console.print("[yellow]No matches found. Try providing --release-ids or a different needle.[/]")
        raise typer.Exit(2)


@app.command("learn-config")
def learn_config(
    epic_id: Optional[str] = typer.Option(None, help="Epic ID to learn from"),
    feature_ref: Optional[str] = typer.Option(None, help="Feature ref (e.g., DATALIN-457) to resolve epic and learn from"),
    write: bool = typer.Option(True, help="Write changes to bae.config.yaml (set false to preview)"),
):
    """Fetch an epic and infer field key mappings; update bae.config.yaml."""
    banner("BeautifulEpics")
    cfg = AppConfig.load()
    account = cfg.account or os.getenv("BAE_AHA_ACCOUNT")
    token = cfg.auth.token or os.getenv("BAE_AHA_TOKEN") or os.getenv("AHA_API_TOKEN")
    if not account or not token:
        typer.echo("Missing account/token.", err=True)
        raise typer.Exit(2)
    client = AhaClient(account=account, token=token)

    # Resolve epic via feature if needed
    if not epic_id and feature_ref:
        # scan all releases for the feature and get its epic
        # best-effort: use product from config
        product = None
        if cfg.product_key:
            product = client.find_product_by_key(cfg.product_key)
        elif cfg.product_name:
            product = client.find_product_by_name(cfg.product_name)
        rid_list = list(client.iter_release_ids_for_product(product["id"])) if product else []
        for rid in rid_list:
            for f in client.iter_release_features(rid):
                if (f.get("reference_num") or "").strip().lower() == feature_ref.strip().lower():
                    ff = client.get_feature(str(f.get("id")))
                    epic = ff.get("epic") or ff.get("master_feature") or ff.get("master_epic") or {}
                    if isinstance(epic, dict) and epic.get("id"):
                        epic_id = str(epic.get("id"))
                        break
            if epic_id:
                break
    if not epic_id:
        typer.echo("Provide --epic-id or --feature-ref", err=True)
        raise typer.Exit(2)

    data = client.get_epic(str(epic_id))
    cf = data.get("custom_fields")
    keys = []
    if isinstance(cf, dict):
        keys = list(cf.keys())
    elif isinstance(cf, list):
        keys = [str((x.get("key") or x.get("name") or "")).strip() for x in cf if isinstance(x, dict)]

    # Heuristics
    def pick(substrs: list[str], default: Optional[str] = None) -> Optional[str]:
        for k in keys:
            low = k.lower()
            if all(s in low for s in substrs):
                return k
        return default

    mapping = {
        "solution_value_statement": pick(["client", "value"]) or pick(["solution", "value"]) or "client_value_statement",
        "risk_status": pick(["risk"]) or "risk_status",
        "commitment": pick(["commit"]) or "commitment",
        "master_epic": pick(["master", "epic"]) or "ibm_software_only_managed_tags_master_epics",
        "github_link": ["github_link", "integrations_to"],
        "product_management_owner": pick(["product", "management", "owner"]) or "product_management_owner",
        "development_owner": pick(["development", "owner"]) or "development_owner",
        "ibm_software_gtm_themes": pick(["gtm", "themes"]) or "ibm_software_gtm_themes",
        "priority_data_ai": pick(["priority"]) or "priority",
    }

    console.print({"learned_fields": mapping, "custom_fields_keys": keys})

    # Write out
    if write:
        path = os.getenv("BAE_CONFIG", "bae.config.yaml")
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            y = yaml.safe_load(f) or {}
        y.setdefault("fields", {}).update(mapping)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(y, f, sort_keys=False, allow_unicode=True)
        console.print(f"Updated field mappings in {path} ✅")


if __name__ == "__main__":
    app()
