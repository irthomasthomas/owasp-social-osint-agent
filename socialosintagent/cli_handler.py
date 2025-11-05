import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import humanize
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .analyzer import SocialOSINTAgent

logger = logging.getLogger("SocialOSINTAgent.CLI")

class CliHandler:
    """Handles all Command-Line Interface interactions for the agent."""
    def __init__(self, agent: SocialOSINTAgent, args):
        self.agent = agent
        self.args = args
        self.console = Console()
        self.base_dir = Path("data")

    def run(self):
        self.console.print(Panel("[bold blue]SocialOSINTAgent[/bold blue]\nCollects and analyzes user activity across multiple platforms using vision and LLMs.\nEnsure API keys and identifiers are set in your `.env` file or Mastodon JSON config.", title="Welcome", border_style="blue"))
        if self.args.offline: self.console.print(Panel("[bold yellow]OFFLINE MODE ENABLED[/bold yellow]\nData will be sourced only from local cache. No new data will be fetched or analyzed.", title_align="center", border_style="yellow"))
        
        while True:
            try:
                self.console.print("\n[bold cyan]Select Platform(s) for Analysis:[/bold cyan]")
                available = self.agent.client_manager.get_available_platforms(check_creds=True)
                if not available: self.console.print("[red]No platforms are configured correctly.[/red]"); break
                
                opts = {str(i+1): p for i,p in enumerate(available)}
                n = len(available)+1
                if len(available)>1: opts[str(n)]="cross-platform"; n+=1
                opts.update({str(n):"purge data", str(n+1):"cache status", str(n+2):"exit"})
                for k,v in opts.items(): self.console.print(f" {k}. {v.replace('-',' ').capitalize()}")
                
                choice = Prompt.ask("Enter number(s)", default=str(n+2))
                
                if opts.get(choice)=="exit": break
                if opts.get(choice)=="purge data": self._handle_purge(); continue
                if opts.get(choice)=="cache status": self._handle_cache_status(); continue
                
                selected = available if opts.get(choice)=="cross-platform" else [opts[k] for k in choice.split(',') if k.strip() in opts and opts[k.strip()] in available]
                if not selected: self.console.print("[yellow]Invalid selection.[/yellow]"); continue
                self.console.print(f"Selected: {', '.join(p.capitalize() for p in selected)}")
                
                query_platforms = {}
                for p in selected:
                    prompt_msg = f"Enter {p.capitalize()} username(s) (comma-separated"
                    if p == "twitter": prompt_msg += ", no '@')"
                    elif p == "reddit": prompt_msg += ", no 'u/')"
                    elif p == "bluesky": prompt_msg += ", e.g., 'handle.bsky.social')"
                    elif p == "mastodon": prompt_msg += ", format: 'user@instance.domain')"
                    else: prompt_msg += ")"
                    if self.args.offline: prompt_msg += " - OFFLINE, cache only)"
                    
                    users_input = Prompt.ask(prompt_msg)
                    if not users_input: continue
                    from .utils import sanitize_username
                    users = [sanitize_username(u.strip()) for u in users_input.split(',') if u.strip()]
                    if users:
                        self.console.print(Text("Cache check: ", style="dim") + Text.from_markup(", ".join([f"{u} {self._get_cache_info_string(p,u)}" for u in users])))
                        query_platforms[p] = users
                if not query_platforms: self.console.print("[yellow]No users entered.[/yellow]"); continue
                
                default_count_str = Prompt.ask("Enter default number of items to fetch per target", default="50")
                try: default_count = int(default_count_str)
                except ValueError: default_count = 50; self.console.print("[yellow]Invalid number, using 50.[/yellow]")
                
                fetch_options = {"default_count": default_count, "targets": {}}
                self._run_analysis_loop(query_platforms, fetch_options)
            
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]Operation cancelled.[/yellow]")
                if Confirm.ask("Exit program?", default=False): break
                else: continue

    def _run_analysis_loop(self, platforms: Dict[str, List[str]], fetch_options: Dict[str, Any]):
        platform_info = " | ".join([f"{p.capitalize()}: {', '.join(u)}" for p, u in platforms.items()])
        self.console.print(Panel(f"Targets: {platform_info}\nCommands: `exit`, `refresh`, `help`, `loadmore [<platform/user>] <count>`", title="🔎 Analysis Session", border_style="cyan", expand=False))
        last_query = ""
        while True:
            try:
                user_input = Prompt.ask("\n[bold green]Analysis Query>[/bold green]", default=last_query).strip()
                if not user_input: continue
                
                # Use startswith for more robust command parsing
                force_refresh, should_run_analysis, query_to_run = False, False, ""
                
                if user_input.lower() == "exit": break
                elif user_input.lower() == "help":
                    self.console.print(Panel("`exit`: Return to menu.\n`refresh`: Force full data fetch.\n`loadmore <count>`: Add items for the sole target, or choose from a list.\n`loadmore <platform/user> <count>`: Explicitly add items for a target (e.g., `loadmore twitter/user001 100`).\n`help`: Show this message.", title="Help"))
                    continue
                elif user_input.lower() == "refresh":
                    if self.args.offline: self.console.print("[yellow]'refresh' is unavailable in offline mode.[/yellow]"); continue
                    if Confirm.ask("Force refresh data for all targets? This uses more API calls.", default=False):
                        force_refresh = True
                        query_to_run = Prompt.ask("Enter analysis query", default=last_query if last_query != "refresh" else "").strip()
                        if query_to_run: should_run_analysis = True
                        else: self.console.print("[yellow]Refresh cancelled, no query entered.[/yellow]")
                    continue
                elif user_input.lower().startswith("loadmore"):
                    parts = user_input.split()
                    should_run_analysis, query_to_run, force_refresh = self._handle_loadmore_command(parts, platforms, fetch_options, last_query)
                else:
                    query_to_run, should_run_analysis = user_input, True

                if not should_run_analysis: continue
                
                last_query = query_to_run
                result_dict = self.agent.analyze(platforms, query_to_run, force_refresh, fetch_options, console=self.console)
                
                report_content = result_dict.get("report", "[red]No report generated.[/red]")
                is_error = result_dict.get("error", True)

                border_color = "red" if is_error else "green"
                content_to_render = Text.from_markup(report_content) if is_error else Markdown(report_content)
                self.console.print(Panel(content_to_render, title="Analysis Report", border_style=border_color, expand=True))
                
                if not is_error:
                    if not self.args.no_auto_save:
                        self._save_output(result_dict, self.args.format)
                    elif Confirm.ask("Save report?"):
                        save_format = Prompt.ask("Format?", choices=["markdown", "json"], default=self.args.format)
                        self._save_output(result_dict, save_format)

            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]Analysis query cancelled.[/yellow]")
                if Confirm.ask("\nExit this analysis session?", default=False): break
                else: last_query = ""; continue
            except Exception as e:
                logger.error(f"Error in analysis loop: {e}", exc_info=True)
                self.console.print(f"[bold red]An error occurred: {e}[/bold red]")

    def _handle_purge(self):
        self.console.print("\n[bold yellow]Select Data to Purge:[/bold yellow]")
        options = {"1": ("All", ["cache", "media", "outputs"]), "2": ("Cache (Text/Metadata)", ["cache"]), "3": ("Media Files", ["media"]), "4": ("Output Reports", ["outputs"]), "5": ("Cancel", [])}
        for k, (n, _) in options.items(): self.console.print(f" {k}. {n}")
        choice = Prompt.ask("Enter number", default="5").strip()
        if choice not in options: self.console.print("[red]Invalid selection.[/red]"); return
        name, dirs = options[choice]
        if not dirs: self.console.print("[cyan]Purge operation cancelled.[/cyan]"); return
        if Confirm.ask(f"[bold red]This will PERMANENTLY delete all '{name}' data. Are you sure?[/bold red]", default=False):
            for d in dirs:
                path = self.base_dir / d
                if path.exists(): shutil.rmtree(path); self.console.print(f"[green]Successfully purged '{path.name}'.[/green]")
                path.mkdir(parents=True, exist_ok=True)
        else: self.console.print("[cyan]Purge operation cancelled.[/cyan]")

    def _handle_cache_status(self):
        self.console.print("\n[bold cyan]Cache Status Overview:[/bold cyan]")
        cache_dir = self.base_dir / "cache"
        if not cache_dir.is_dir(): self.console.print("[yellow]Cache directory not found.[/yellow]\n"); return
        table = Table(title="Cached Data Summary", show_lines=True)
        table.add_column("Platform", style="cyan"); table.add_column("Username", style="magenta"); table.add_column("Last Fetched (UTC)", style="green", min_width=19, max_width=19); table.add_column("Age", style="yellow"); table.add_column("Items", style="blue", justify="right"); table.add_column("Media (A/F)", style="dim", justify="right")
        for file in sorted(cache_dir.glob("*.json")):
            try:
                platform, username = file.stem.split("_", 1)
                data = self.agent.cache.load(platform, username)
                if not data: continue
                ts_str, age = data.get("timestamp", "N/A"), "N/A"
                if ts_str != "N/A": age = self._format_cache_age(ts_str)
                counts_parts = []
                if 'tweets' in data: counts_parts.append(f"{len(data['tweets'])}t")
                if 'submissions' in data: counts_parts.append(f"{len(data['submissions'])}s")
                if 'comments' in data: counts_parts.append(f"{len(data['comments'])}c")
                if 'posts' in data: counts_parts.append(f"{len(data['posts'])}p")
                if 'items' in data: counts_parts.append(f"{len(data['items'])}i")
                counts_str = ", ".join(counts_parts) or "N/A"
                media_analyzed = len([m for m in data.get('media_analysis', []) if m and m.strip()]); media_found = len(data.get('media_paths', [])); media_str = f"{media_analyzed}/{media_found}"
                table.add_row(platform.capitalize(), username, ts_str[:19], age, counts_str, media_str)
            except Exception as e: logger.error(f"Error processing {file.name} for status: {e}")
        if table.row_count > 0: self.console.print(table)
        else: self.console.print("[yellow]No valid cache files found.[/yellow]\n")
        Prompt.ask("\n[dim]Press Enter to return[/dim]", default="")

    def _handle_loadmore_command(self, parts: List[str], platforms: Dict[str, List[str]], fetch_options: Dict[str, Any], last_query: str) -> Tuple[bool, str, bool]:
        if len(parts) not in [2, 3]: self.console.print("[red]Invalid format. Use: `loadmore <count>` or `loadmore <platform/user> <count>`[/red]"); return False, "", False
        target_str, count_str = (parts[1], parts[2]) if len(parts) == 3 else (None, parts[1])
        try: count_to_add = int(count_str)
        except ValueError: self.console.print(f"[red]Invalid count: '{count_str}'.[/red]"); return False, "", False
        if not target_str:
            all_targets = [f"{p}/{u}" for p, users in platforms.items() for u in users]
            if len(all_targets) == 1: target_str = all_targets[0]
            elif len(all_targets) > 1:
                self.console.print("[cyan]Choose a target:[/cyan]")
                prompt_choices = {str(i): t for i, t in enumerate(all_targets, 1)}
                for i_str, t in prompt_choices.items(): self.console.print(f" {i_str}. {t}")
                choice = Prompt.ask("Enter number", choices=list(prompt_choices.keys()), show_choices=False)
                target_str = prompt_choices.get(choice)
            else: self.console.print("[red]No active targets.[/red]"); return False, "", False
        if not target_str: return False, "", False
        try: platform, username = target_str.split('/', 1)
        except ValueError: self.console.print(f"[red]Invalid target format: '{target_str}'.[/red]"); return False, "", False
        if platform not in platforms or username not in platforms.get(platform, []): self.console.print(f"[red]Target '{target_str}' not in session.[/red]"); return False, "", False
        target_key = f"{platform}:{username}"
        current_count = fetch_options.get("targets", {}).get(target_key, {}).get("count", fetch_options.get("default_count", 50))
        new_count = current_count + count_to_add
        if "targets" not in fetch_options: fetch_options["targets"] = {}
        if target_key not in fetch_options["targets"]: fetch_options["targets"][target_key] = {}
        fetch_options["targets"][target_key]["count"] = new_count
        if last_query: self.console.print(f"[cyan]Updated {target_str} to {new_count} items. Re-running last query...[/cyan]"); return True, last_query, True
        else: self.console.print(f"[cyan]Updated {target_str} to {new_count} items. Enter a query.[/cyan]"); return False, "", False
    
    def _save_output(self, result: Dict[str, Any], format: str):
        metadata = result["metadata"]
        query = metadata.get("query", "query")
        platforms = list(metadata.get("targets", {}).keys())

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_q = "".join(c for c in query[:30] if c.isalnum() or c in " _-").strip() or "query"
        safe_p = "_".join(sorted(platforms)) or "platforms"
        base = f"analysis_{ts}_{safe_p}_{safe_q}"
        path = self.base_dir / "outputs" / f"{base}.{format}"

        if format == "json":
            # The report already contains the header, so we can save it directly.
            # A more advanced approach might separate body and header, but this is robust.
            data_to_save = {
                "analysis_metadata": metadata,
                "analysis_report_markdown": result["report"]
            }
            path.write_text(json.dumps(data_to_save, indent=2), encoding="utf-8")
        else:
            path.write_text(result["report"], encoding="utf-8")
        
        self.console.print(f"[green]Analysis saved to: {path}[/green]")
    
    def _format_cache_age(self, timestamp_str: str) -> str:
        try:
            dt_obj = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
            return humanize.naturaltime(datetime.now(timezone.utc) - dt_obj)
        except (ValueError, TypeError):
            return "Invalid date"

    def _get_cache_info_string(self, platform: str, username: str) -> str:
        data = self.agent.cache.load(platform, username)
        if not data: return "[dim](no cache)[/dim]"
        ts = data.get("timestamp")
        fresh = "[red]date err[/red]"
        if ts:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            fresh = "[green]fresh[/green]" if age.total_seconds() < 24*3600 else f"[yellow]stale ({self._format_cache_age(ts)})[/yellow]"
        counts = {"twitter": len(data.get('tweets',[])), "reddit": len(data.get('submissions',[]))+len(data.get('comments',[])), "bluesky": len(data.get('posts',[])), "mastodon": len(data.get('posts',[])), "hackernews": len(data.get('items',[]))}
        return f"(cached: {counts.get(platform, 0)} items, {fresh})"