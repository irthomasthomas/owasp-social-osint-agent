"""
Handles all Command-Line Interface (CLI) interactions for the agent.

This module uses the 'rich' library to create a user-friendly, interactive
experience for selecting platforms, entering targets, running analysis queries,
and managing the application's data.
"""

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
from .cache import CACHE_EXPIRY_HOURS
from .utils import get_sort_key

logger = logging.getLogger("SocialOSINTAgent.CLI")

class CliHandler:
    """Manages the interactive command-line session for the SocialOSINTAgent."""
    def __init__(self, agent: SocialOSINTAgent, args):
        """
        Initializes the CliHandler.

        Args:
            agent: An instance of the SocialOSINTAgent.
            args: Command-line arguments namespace.
        """
        self.agent = agent
        self.args = args
        self.console = Console()
        self.base_dir = Path("data")

    def run(self):
        """Starts the main interactive loop of the CLI."""
        self.console.print(Panel("[bold blue]SocialOSINTAgent[/bold blue]\nCollects and analyzes user activity across multiple platforms using vision and LLMs.\nEnsure API keys are set in your `.env` file.", title="Welcome", border_style="blue"))
        if self.args.offline:
            self.console.print(Panel("[bold yellow]OFFLINE MODE ENABLED[/bold yellow]\nData will be sourced only from local cache. No new data will be fetched.", title_align="center", border_style="yellow"))
        
        while True:
            try:
                self._show_main_menu()
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]Operation cancelled.[/yellow]")
                if Confirm.ask("Exit program?", default=True):
                    break
                else:
                    continue

    def _show_main_menu(self):
        """Displays the main menu and handles user platform/command selection."""
        self.console.print("\n[bold cyan]Select Platform(s) for Analysis:[/bold cyan]")
        available = self.agent.client_manager.get_available_platforms(check_creds=True)
        if not available:
            self.console.print("[red]No platforms are configured correctly. Please check your .env file.[/red]")
            return

        opts = {str(i + 1): p for i, p in enumerate(available)}
        n = len(available) + 1
        if len(available) > 1:
            opts[str(n)] = "cross-platform"
            n += 1
        opts.update({str(n): "purge data", str(n + 1): "cache status", str(n + 2): "exit"})
        
        for k, v in opts.items():
            self.console.print(f" {k}. {v.replace('-', ' ').capitalize()}")
        
        choice = Prompt.ask("Enter number(s)", default=str(n + 2))
        
        if opts.get(choice) == "exit":
            raise EOFError  # Use an exception to break the outer loop cleanly
        if opts.get(choice) == "purge data":
            self._handle_purge()
            return
        if opts.get(choice) == "cache status":
            self._handle_cache_status()
            return
        
        selected_platforms = available if opts.get(choice) == "cross-platform" else [
            opts[k] for k in choice.split(',') if k.strip() in opts and opts[k.strip()] in available
        ]
        if not selected_platforms:
            self.console.print("[yellow]Invalid selection.[/yellow]")
            return

        self._collect_targets_and_start_session(selected_platforms)

    def _collect_targets_and_start_session(self, selected_platforms: List[str]):
        """
        Prompts the user for usernames for the selected platforms and starts an analysis session.

        Args:
            selected_platforms: A list of platform names to query.
        """
        self.console.print(f"Selected: {', '.join(p.capitalize() for p in selected_platforms)}")
        
        query_platforms = {}
        from .utils import sanitize_username
        for p in selected_platforms:
            prompt_msg = self._get_platform_prompt(p)
            users_input = Prompt.ask(prompt_msg)
            if not users_input:
                continue
            
            users = [sanitize_username(u.strip()) for u in users_input.split(',') if u.strip()]
            if users:
                self.console.print(Text("Cache check: ", style="dim") + Text.from_markup(", ".join([f"{u} {self._get_cache_info_string(p,u)}" for u in users])))
                query_platforms[p] = users

        if not query_platforms:
            self.console.print("[yellow]No users entered.[/yellow]")
            return
        
        MIN_FETCH_ALLOWED = 5
        default_count_str = Prompt.ask(f"Enter default number of items to fetch per target (min {MIN_FETCH_ALLOWED})", default="50")
        try:
            default_count = int(default_count_str)
            if default_count < MIN_FETCH_ALLOWED:
                self.console.print(f"[yellow]Adjusting to minimum fetch count of {MIN_FETCH_ALLOWED}.[/yellow]")
                default_count = MIN_FETCH_ALLOWED
        except ValueError:
            default_count = 50
            self.console.print("[yellow]Invalid number, using 50.[/yellow]")
        
        fetch_options = {"default_count": default_count, "targets": {}}
        self._run_analysis_loop(query_platforms, fetch_options)

    def _get_platform_prompt(self, platform: str) -> str:
        """Generates a user-friendly prompt message for a given platform."""
        prompts = {
            "twitter": "no '@'",
            "reddit": "no 'u/'",
            "bluesky": "e.g., 'handle.bsky.social'",
            "mastodon": "format: 'user@instance.domain'",
        }
        details = prompts.get(platform, "")
        prompt_msg = f"Enter {platform.capitalize()} username(s) (comma-separated"
        if details:
            prompt_msg += f", {details})"
        else:
            prompt_msg += ")"
        if self.args.offline:
            prompt_msg += " - OFFLINE, cache only"
        return prompt_msg

    def _run_analysis_loop(self, platforms: Dict[str, List[str]], fetch_options: Dict[str, Any]):
        """
        The main loop for an active analysis session.
        Uses slash commands and persistent targeting info for clarity.
        """
        # Persistent Header Panel
        platform_info = " | ".join([f"{p.capitalize()}: {', '.join(u)}" for p, u in platforms.items()])
        self.console.print("\n")
        self.console.print(Panel(
            f"Targeting: [bold]{platform_info}[/bold]\n"
            f"Mode: [bold]{'OFFLINE (Cache Only)' if self.args.offline else 'ONLINE (Live API)'}[/bold]",
            title="🔎 Active OSINT Session",
            border_style="cyan",
            expand=False
        ))

        last_query = ""

        while True:
            try:
                # Persistent Command Footer (shown before every prompt)
                self.console.print("[dim italic]Commands: /exit, /help, /refresh, /loadmore <count>[/dim italic]")
                
                user_input = Prompt.ask(
                    "\n[bold green]Agent Query[/bold green]", 
                    default=last_query
                ).strip()

                if not user_input:
                    continue

                force_refresh = False
                should_run_analysis = False
                query_to_run = ""

                # Handle Slash Commands
                input_lower = user_input.lower()

                if input_lower == "/exit":
                    break

                elif input_lower in ["/help", "/?", "?"]:
                    self._show_help_table()
                    continue

                elif input_lower == "/refresh":
                    if self.args.offline:
                        self.console.print("[yellow]'/refresh' is unavailable in offline mode.[/yellow]")
                        continue
                    
                    if Confirm.ask("[yellow]Force refresh data for all targets? (Uses API calls)[/yellow]", default=False):
                        force_refresh = True
                        query_to_run = Prompt.ask("Enter query to run with refreshed data", default=last_query).strip()
                        if query_to_run:
                            should_run_analysis = True
                        else:
                            self.console.print("[cyan]Refresh cancelled.[/cyan]")
                    continue

                elif input_lower.startswith("/loadmore"):
                    # Use existing parser but strip the slash first
                    parts = user_input.replace("/", "").split()
                    should_run_analysis, query_to_run, force_refresh = self._handle_loadmore_command(
                        parts, platforms, fetch_options, last_query
                    )

                # Standard Query (No Slash)
                else:
                    if user_input.startswith("/"):
                        self.console.print(f"[red]Unknown command: {user_input.split()[0]}. Type /help for list.[/red]")
                        continue
                    
                    query_to_run = user_input
                    should_run_analysis = True

                # Execute Analysis
                if should_run_analysis:
                    last_query = query_to_run
                    result_dict = self.agent.analyze(
                        platforms, 
                        query_to_run, 
                        force_refresh, 
                        fetch_options, 
                        console=self.console
                    )
                    self._display_and_save_report(result_dict)

            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]Query cancelled.[/yellow]")
                if Confirm.ask("Exit this analysis session?", default=False):
                    break
                continue
            except Exception as e:
                logger.error(f"Error in analysis loop: {e}", exc_info=True)
                self.console.print(f"[bold red]An error occurred: {e}[/bold red]")

    def _show_help_table(self):
        """Helper to display a clean command reference."""
        table = Table(title="Command Reference", show_header=True, header_style="bold magenta", box=None)
        table.add_column("Command", style="cyan")
        table.add_column("Description", style="white")
        
        table.add_row("/loadmore <n>", "Fetch <n> additional items for current targets.")
        table.add_row("/loadmore <p/u> <n>", "Fetch more for a specific platform/user (e.g. /loadmore twitter/elon 20).")
        table.add_row("/refresh", "Ignore cache and force a fresh download from all APIs.")
        table.add_row("/help", "Show this command reference.")
        table.add_row("/exit", "Exit the session and return to the main menu.")
        
        self.console.print(Panel(table, border_style="magenta"))

    def _display_and_save_report(self, result_dict: Dict[str, Any]):
        """Renders the analysis report to the console and handles saving."""
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

    def _handle_purge(self):
        """Handles the interactive data purging process."""
        self.console.print("\n[bold yellow]Select Data to Purge:[/bold yellow]")
        options = {"1": ("All", ["cache", "media", "outputs"]), "2": ("Cache (Text/Metadata)", ["cache"]), "3": ("Media Files", ["media"]), "4": ("Output Reports", ["outputs"]), "5": ("Cancel", [])}
        for k, (n, _) in options.items():
            self.console.print(f" {k}. {n}")
        choice = Prompt.ask("Enter number", default="5").strip()
        
        name, dirs = options.get(choice, ("Invalid", []))
        if not dirs or name == "Invalid":
            self.console.print("[cyan]Purge operation cancelled.[/cyan]")
            return

        if Confirm.ask(f"[bold red]This will PERMANENTLY delete all '{name}' data. Are you sure?[/bold red]", default=False):
            for d in dirs:
                path = self.base_dir / d
                if path.exists():
                    shutil.rmtree(path)
                    self.console.print(f"[green]Successfully purged '{path.name}'.[/green]")
                path.mkdir(parents=True, exist_ok=True)
        else:
            self.console.print("[cyan]Purge operation cancelled.[/cyan]")

    def _handle_cache_status(self):
        """Displays a summary of all cached user data."""
        self.console.print("\n[bold cyan]Cache Status Overview:[/bold cyan]")
        cache_dir = self.base_dir / "cache"
        if not cache_dir.is_dir():
            self.console.print("[yellow]Cache directory not found.[/yellow]\n")
            return

        table = Table(title="Cached Data Summary", show_lines=True)
        table.add_column("Platform", style="cyan")
        table.add_column("Username", style="magenta")
        table.add_column("Last Fetched (UTC)", style="green", min_width=19, max_width=19)
        table.add_column("Age", style="yellow")
        table.add_column("Items", style="blue", justify="right")
        table.add_column("Media (Analyzed/Found)", style="dim", justify="right")
        
        files = sorted(cache_dir.glob("*.json"))
        if not files:
            self.console.print("[yellow]No cache files found.[/yellow]\n")
            return

        for file in files:
            try:
                platform, username = file.stem.split("_", 1)
                data = self.agent.cache.load(platform, username)
                if not data:
                    # This can happen if the cache file is invalid/expired
                    continue
                    
                profile = data.get("profile", {})
                ts_str = str(data.get("timestamp", "N/A"))
                age = self._format_cache_age(ts_str) if ts_str != "N/A" else "N/A"

                item_count = len(data.get("posts", []))
                
                media_found = 0
                media_analyzed = 0
                for post in data.get("posts", []):
                    for media_item in post.get("media", []):
                        media_found += 1
                        if media_item.get("analysis"):
                            media_analyzed += 1
                
                media_str = f"{media_analyzed}/{media_found}"

                table.add_row(
                    platform.capitalize(), 
                    profile.get("username", username),
                    ts_str[:19], 
                    age, 
                    str(item_count), 
                    media_str
                )
            except Exception as e:
                logger.error(f"Error processing {file.name} for cache status: {e}")
        
        self.console.print(table)
        Prompt.ask("\n[dim]Press Enter to return[/dim]", default="")

    def _handle_loadmore_command(self, parts: List[str], platforms: Dict[str, List[str]], fetch_options: Dict[str, Any], last_query: str) -> Tuple[bool, str, bool]:
        """
        Parses and applies the 'loadmore' command from user input.

        Returns:
            A tuple of (should_run_analysis, query_to_run, force_refresh).
        """
        if len(parts) not in [2, 3]:
            self.console.print("[red]Invalid format. Use: `loadmore <count>` or `loadmore <platform/user> <count>`[/red]")
            return False, "", False
        
        target_str, count_str = (parts[1], parts[2]) if len(parts) == 3 else (None, parts[1])
        try:
            count_to_add = int(count_str)
        except ValueError:
            self.console.print(f"[red]Invalid count: '{count_str}'.[/red]")
            return False, "", False

        all_targets = [f"{p}/{u}" for p, users in platforms.items() for u in users]
        if not target_str:
            if len(all_targets) == 1:
                target_str = all_targets[0]
            elif len(all_targets) > 1:
                self.console.print("[cyan]Choose a target to load more for:[/cyan]")
                prompt_choices = {str(i): t for i, t in enumerate(all_targets, 1)}
                for i_str, t in prompt_choices.items():
                    self.console.print(f" {i_str}. {t}")
                choice = Prompt.ask("Enter number", choices=list(prompt_choices.keys()), show_choices=False)
                target_str = prompt_choices.get(choice)
            else:
                self.console.print("[red]No active targets.[/red]")
                return False, "", False

        if not target_str:
            return False, "", False
        
        try:
            platform, username = target_str.split('/', 1)
        except ValueError:
            self.console.print(f"[red]Invalid target format: '{target_str}'. Use platform/user.[/red]")
            return False, "", False
        
        if platform not in platforms or username not in platforms.get(platform, []):
            self.console.print(f"[red]Target '{target_str}' is not part of the current analysis session.[/red]")
            return False, "", False

        target_key = f"{platform}:{username}"
                # Ensure the path exists
        if "targets" not in fetch_options:
            fetch_options["targets"] = {}

        # Get current count safely
        target_options = fetch_options["targets"].get(target_key, {})
        current_count = target_options.get("count", fetch_options.get("default_count", 50))

        # Set new count
        new_count = current_count + count_to_add
        if target_key not in fetch_options["targets"]:
            fetch_options["targets"][target_key] = {}
        fetch_options["targets"][target_key]["count"] = new_count
        
        if last_query:
            self.console.print(f"[cyan]Updated {target_str} fetch limit to {new_count} items. Re-running last query...[/cyan]")
            return True, last_query, True # Force refresh to get new data
        else:
            self.console.print(f"[cyan]Updated {target_str} fetch limit to {new_count} items. Enter a query to run an analysis.[/cyan]")
            return False, "", False
    
    def _save_output(self, result: Dict[str, Any], file_format: str):
        """Saves the analysis report to a file in interactive mode."""
        metadata = result["metadata"]
        query = metadata.get("query", "query")
        platforms = list(metadata.get("targets", {}).keys())

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_q = "".join(c for c in query[:30] if c.isalnum() or c in " _-").strip() or "query"
        safe_p = "_".join(sorted(platforms)) or "platforms"
        base_filename = f"analysis_{ts}_{safe_p}_{safe_q}"
        path = self.base_dir / "outputs" / f"{base_filename}.{file_format}"

        if file_format == "json":
            data_to_save = {
                "analysis_metadata": metadata,
                "analysis_report_markdown": result["report"]
            }
            path.write_text(json.dumps(data_to_save, indent=2), encoding="utf-8")
        else:
            path.write_text(result["report"], encoding="utf-8")
        
        self.console.print(f"[green]Analysis saved to: {path}[/green]")
    
    def _format_cache_age(self, timestamp_str: str) -> str:
        """Formats a timestamp string into a human-readable relative time."""
        try:
            dt_obj = datetime.fromisoformat(timestamp_str).replace(tzinfo=timezone.utc)
            return humanize.naturaltime(datetime.now(timezone.utc) - dt_obj)
        except (ValueError, TypeError):
            return "Invalid date"

    def _get_cache_info_string(self, platform: str, username: str) -> str:
        """Generates a brief, colorful string indicating cache status for a user."""
        data = self.agent.cache.load(platform, username)
        if not data:
            return "[dim](no cache)[/dim]"
        
        age_str = "date err"
        if data.get("timestamp"):
            cached_at = get_sort_key(data, "timestamp")
            age_delta = datetime.now(timezone.utc) - cached_at
            is_fresh = age_delta.total_seconds() < CACHE_EXPIRY_HOURS * 3600
            age_str = "[green]fresh[/green]" if is_fresh else f"[yellow]stale ({self._format_cache_age(cached_at.isoformat())})[/yellow]"
        
        item_count = len(data.get("posts", []))
        return f"(cached: {item_count} items, {age_str})"