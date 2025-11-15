import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .cache import CacheManager
from .client_manager import ClientManager
from .exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from .llm import LLMAnalyzer
from .platforms import FETCHERS
from .utils import (SUPPORTED_IMAGE_EXTENSIONS, UserData, handle_rate_limit, sanitize_username)

if not os.path.exists('/.dockerenv'):
    print(">>> Running in local mode: Loading .env file...")
    project_root = Path(__file__).resolve().parent.parent
    dotenv_path = project_root / '.env'
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=dotenv_path, override=True)

logger = logging.getLogger("SocialOSINTAgent")

class SocialOSINTAgent:
    def __init__(self, args, cache_manager: CacheManager, llm_analyzer: LLMAnalyzer, client_manager: ClientManager):
        self.args = args
        self.base_dir = Path("data")
        self.cache = cache_manager
        self.llm = llm_analyzer
        self.client_manager = client_manager
        self._setup_directories()
        self._verify_env_vars()
    
    def _verify_env_vars(self):
        """Verifies that necessary environment variables are set."""
        required_llm = ["LLM_API_KEY", "LLM_API_BASE_URL", "IMAGE_ANALYSIS_MODEL", "ANALYSIS_MODEL"]
        if any(not os.getenv(k) for k in required_llm):
            raise RuntimeError("Missing one or more critical LLM environment variables.")
        if not self.client_manager.get_available_platforms(check_creds=True):
             logger.warning("No platform API credentials found. Only HackerNews may be available.")

    def _setup_directories(self):
        """Ensures necessary directories exist."""
        # All generated data goes inside self.base_dir ('data/')
        self.base_dir.mkdir(parents=True, exist_ok=True)
        for dir_name in ["cache", "media", "outputs"]:
            (self.base_dir / dir_name).mkdir(parents=True, exist_ok=True)

    def analyze(self, platforms: Dict[str, List[str]], query: str, force_refresh: bool = False, fetch_options: Optional[Dict[str, Any]] = None, console: Optional[Console] = None) -> Dict[str, Any]:
        progress_console = console or Console(stderr=True)
        collected_data: Dict[str, List[Dict]] = {p: [] for p in platforms}
        
        self._fetch_all_platform_data(platforms, force_refresh, fetch_options, collected_data, progress_console)

        if not any(data_list for data_list in collected_data.values()):
            return {"metadata": {}, "report": "[red]Data collection failed for all targets.[/red]", "error": True}

        if not self.args.offline:
            self._perform_vision_analysis(collected_data, progress_console)

        with progress_console.status("[magenta]Synthesizing report with LLM..."):
            try: report = self.llm.run_analysis(collected_data, query)
            except RateLimitExceededError as e:
                handle_rate_limit(progress_console, "LLM Analysis", e)
                return {"metadata": {}, "report": "[red]Analysis aborted due to LLM rate limit.[/red]", "error": True}
            except Exception as e:
                 logger.error(f"LLM analysis failed: {e}", exc_info=True)
                 return {"metadata": {}, "report": f"[red]LLM analysis failed: {e}[/red]", "error": True}

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text_model, img_model = os.getenv("ANALYSIS_MODEL"), os.getenv("IMAGE_ANALYSIS_MODEL")
        metadata = {"query": query, "targets": platforms, "generated_utc": ts, "mode": 'Offline' if self.args.offline else 'Online', "models": {"text": text_model, "image": img_model}}
        header = f"# OSINT Analysis Report\n\n**Query:** `{query}`\n**Generated:** `{ts}`\n**Mode:** `{metadata['mode']}`\n**Models Used:**\n- Text: `{text_model}`\n- Image: `{img_model}`\n\n---\n\n"
        return {"metadata": metadata, "report": header + report, "error": False}

    def _fetch_all_platform_data(self, platforms, force_refresh, fetch_options, collected_data, console):
        failed_fetches = []
        total_targets = sum(len(v) for v in platforms.values())
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress:
            task = progress.add_task("[cyan]Collecting data...", total=total_targets)
            fetch_options = fetch_options or {}
            default_count = fetch_options.get("default_count", 50)

            for platform, usernames in platforms.items():
                if not (fetcher := FETCHERS.get(platform)):
                    failed_fetches.append((platform, "all", "Fetcher not implemented")); progress.advance(task, len(usernames)); continue
                
                for username in usernames:
                    progress.update(task, description=f"[cyan]Fetching {platform}/{username}...")
                    try:
                        client = self.client_manager.get_platform_client(platform)
                        limit = fetch_options.get("targets", {}).get(f"{platform}:{username}", {}).get("count", default_count)
                        
                        kwargs = {'username': username, 'cache': self.cache, 'force_refresh': force_refresh, 'fetch_limit': limit}
                        platforms_requiring_client = ["twitter", "reddit", "bluesky"]
                        if platform == "mastodon": kwargs['clients'], kwargs['default_client'] = client
                        elif platform in platforms_requiring_client: kwargs['client'] = client
                        
                        if data := fetcher(**kwargs):
                            collected_data[platform].append({"username_key": username, "data": data})
                        else: failed_fetches.append((platform, username, "No data returned"))
                    
                    except RateLimitExceededError as e:
                        handle_rate_limit(console, f"{platform.capitalize()} Fetch", e, should_raise=False)
                        failed_fetches.append((platform, username, "Rate Limited"))
                    except (UserNotFoundError, AccessForbiddenError) as e: failed_fetches.append((platform, username, str(e)))
                    except Exception as e:
                        logger.error(f"Fetch failed for {platform}/{username}: {e}", exc_info=True)
                        failed_fetches.append((platform, username, "Unexpected Error"))
                    finally: progress.advance(task)
        
        if failed_fetches:
            console.print("[yellow]Data collection issues:[/yellow]")
            for p, u, r in failed_fetches: console.print(f"- {p}/{u}: {r}")

    def _perform_vision_analysis(self, collected_data: Dict[str, List[Dict[str, UserData]]], console):
        media_to_analyze = []
        for user_data_list in collected_data.values():
            for user_data_dict in user_data_list:
                user_data = user_data_dict["data"]
                for post in user_data.get("posts", []):
                    for media_item in post.get("media", []):
                        if (path_str := media_item.get("local_path")) and not media_item.get("analysis"):
                            path = Path(path_str)
                            if path.exists() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                                media_to_analyze.append((media_item, user_data["profile"]))
        if not media_to_analyze: return

        modified_users = set()

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console) as progress:
            task = progress.add_task("[cyan]Analyzing images...", total=len(media_to_analyze))
            for media_item, profile in media_to_analyze:
                progress.update(task, description=f"[cyan]Analyzing image from {profile['platform']}/{profile['username']}...")
                try:
                    analysis = self.llm.analyze_image(
                        Path(media_item['local_path']), source_url=media_item['url'],
                        context=f"{profile['platform']} user {profile['username']}"
                    )
                    if analysis: 
                        media_item['analysis'] = analysis
                        modified_users.add((profile['platform'], profile['username']))
                except RateLimitExceededError:
                    console.print("[bold red]Vision model rate limit hit. Aborting further image analysis.[/bold red]"); break
                except Exception as e: logger.error(f"Image analysis failed for {media_item.get('local_path')}: {e}")
                finally: progress.advance(task)
        
        for platform, user_data_list in collected_data.items():
            for user_data_dict in user_data_list:
                username = user_data_dict['username_key']
                if (platform, username) in modified_users:
                    self.cache.save(platform, username, user_data_dict['data'])

    def process_stdin(self):
        stderr_console = Console(stderr=True)
        stderr_console.print("[cyan]Processing analysis request from stdin...[/cyan]")
        try:
            data = json.load(sys.stdin)
            platforms, query, fetch_options = data.get("platforms"), data.get("query"), data.get("fetch_options")
            if not isinstance(platforms, dict) or not platforms or not isinstance(query, str) or not query.strip():
                raise ValueError("Invalid JSON: 'platforms' (dict) and 'query' (str) required.")
            
            available_platforms = self.client_manager.get_available_platforms(check_creds=True)
            query_platforms = {p: [sanitize_username(u.strip()) for u in us if u.strip()] for p, us in platforms.items() if p in available_platforms}
            if not query_platforms: raise ValueError("No valid/configured platforms found in input.")
            
            result = self.analyze(query_platforms, query, fetch_options=fetch_options, console=stderr_console)
            
            if not result.get("error"):
                if self.args.no_auto_save: print(result["report"])
                else: self._save_output_headless(result, self.args.format)
                sys.exit(0)
            else:
                sys.stderr.write(f"Analysis Error:\n{result['report']}\n"); sys.exit(2)
        except (ValueError, RuntimeError) as e:
            sys.stderr.write(f"Error: {e}\n"); sys.exit(1)

    def _save_output_headless(self, result: Dict[str, Any], format: str):
        metadata = result["metadata"]
        query = metadata.get("query", "query")
        platforms = list(metadata.get("targets", {}).keys())
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_q = "".join(c for c in query[:30] if c.isalnum() or c in " _-").strip() or "query"
        safe_p = "_".join(sorted(platforms)) or "platforms"
        base = f"analysis_{ts}_{safe_p}_{safe_q}"
        path = self.base_dir / "outputs" / f"{base}.{format}"

        if format == "json":
            data_to_save = {"analysis_metadata": metadata, "analysis_report_markdown": result["report"]}
            path.write_text(json.dumps(data_to_save, indent=2), encoding="utf-8")
        else:
            path.write_text(result["report"], encoding="utf-8")
        
        print(f"Analysis saved to: {path}")