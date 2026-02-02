"""
Improved analyzer module with unified fetch/rate handling and resilient image processing.

Key improvements:
- Uses new ImageProcessor for resilient image handling
- Better error aggregation and reporting
- Graceful degradation when individual operations fail
- More detailed progress tracking
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .cache import CacheManager
from .client_manager import ClientManager
from .exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from .llm import LLMAnalyzer
from .platforms import FETCHERS
from .utils import (SUPPORTED_IMAGE_EXTENSIONS, UserData, handle_rate_limit, 
                   sanitize_username)
from .image_processor import ImageProcessor, ProcessingStatus

logger = logging.getLogger("SocialOSINTAgent")


class FetchResult:
    """Container for fetch operation results with detailed error tracking."""
    
    def __init__(self):
        self.successful: List[tuple] = []  # (platform, username, data)
        self.failed: List[tuple] = []  # (platform, username, error_type, message)
        self.rate_limited: List[tuple] = []  # (platform, username)

    def add_success(self, platform: str, username: str, data: UserData):
        """Record a successful fetch."""
        self.successful.append((platform, username, data))

    def add_failure(self, platform: str, username: str, error_type: str, message: str):
        """Record a failed fetch."""
        self.failed.append((platform, username, error_type, message))

    def add_rate_limit(self, platform: str, username: str):
        """Record a rate-limited fetch."""
        self.rate_limited.append((platform, username))

    @property
    def has_any_data(self) -> bool:
        """Check if any fetch succeeded."""
        return len(self.successful) > 0

    def get_summary(self) -> str:
        """Get a summary of fetch results."""
        parts = []
        if self.successful:
            parts.append(f"{len(self.successful)} successful")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        if self.rate_limited:
            parts.append(f"{len(self.rate_limited)} rate-limited")
        return ", ".join(parts) if parts else "no results"


class SocialOSINTAgent:
    """
    Improved OSINT agent with unified fetch/rate handling and resilient image processing.
    """
    
    def __init__(self, args, cache_manager: CacheManager, llm_analyzer: LLMAnalyzer, 
                 client_manager: ClientManager):
        """
        Initializes the SocialOSINTAgent.

        Args:
            args: Command-line arguments namespace.
            cache_manager: An instance of CacheManager for data caching.
            llm_analyzer: An instance of LLMAnalyzer for AI-powered analysis.
            client_manager: An instance of ClientManager for API client handling.
        """
        self.args = args
        self.base_dir = Path("data")
        self.cache = cache_manager
        self.llm = llm_analyzer
        self.client_manager = client_manager
        self.image_processor = ImageProcessor()
        self._setup_directories()
        self._verify_env_vars()
    
    def _verify_env_vars(self):
        """Verifies that all necessary environment variables are set."""
        required_llm = ["LLM_API_KEY", "LLM_API_BASE_URL", "IMAGE_ANALYSIS_MODEL", 
                       "ANALYSIS_MODEL"]
        if any(not os.getenv(k) for k in required_llm):
            raise RuntimeError("Missing one or more critical LLM environment variables.")
        if not self.client_manager.get_available_platforms(check_creds=True):
            logger.warning(
                "No platform API credentials found. Only HackerNews and GitHub may be available."
            )

    def _setup_directories(self):
        """Ensures that all required data directories exist."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        for dir_name in ["cache", "media", "outputs"]:
            (self.base_dir / dir_name).mkdir(parents=True, exist_ok=True)

    def analyze(
        self, 
        platforms: Dict[str, List[str]], 
        query: str, 
        force_refresh: bool = False, 
        fetch_options: Optional[Dict[str, Any]] = None, 
        console: Optional[Console] = None
    ) -> Dict[str, Any]:
        """
        Orchestrates the entire OSINT analysis process with improved error handling.

        Args:
            platforms: A dictionary mapping platform names to lists of usernames.
            query: The user's natural language query for the analysis.
            force_refresh: If True, bypasses the cache for API data.
            fetch_options: A dictionary to control fetch counts.
            console: A rich.console.Console object for progress display.

        Returns:
            A dictionary containing the analysis report and metadata.
        """
        progress_console = console or Console(stderr=True)
        
        # Phase 1: Fetch data from all platforms
        fetch_result = self._fetch_all_platform_data(
            platforms, force_refresh, fetch_options, progress_console
        )
        
        if not fetch_result.has_any_data:
            return {
                "metadata": {},
                "report": "[red]Data collection failed for all targets.[/red]",
                "error": True
            }
        
        # Phase 2: Process images (if not offline)
        vision_stats = {}
        if not self.args.offline:
            vision_stats = self._perform_vision_analysis(
                fetch_result.successful, progress_console
            )
        
        # Phase 3: Generate analysis report
        report = self._generate_analysis_report(
            fetch_result, query, vision_stats, progress_console
        )
        
        return report

    def _fetch_all_platform_data(
        self,
        platforms: Dict[str, List[str]],
        force_refresh: bool,
        fetch_options: Optional[Dict[str, Any]],
        console: Console
    ) -> FetchResult:
        """
        Fetch data from all platforms with unified error handling.
        
        Returns:
            FetchResult object with detailed success/failure tracking
        """
        result = FetchResult()
        total_targets = sum(len(v) for v in platforms.values())
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Collecting data...", total=total_targets)
            fetch_options = fetch_options or {}
            default_count = fetch_options.get("default_count", 50)

            for platform, usernames in platforms.items():
                # Check if fetcher exists
                if not (fetcher := FETCHERS.get(platform)):
                    for username in usernames:
                        result.add_failure(
                            platform, username, "NotImplemented", 
                            "Fetcher not implemented"
                        )
                    progress.advance(task, len(usernames))
                    continue
                
                # Fetch data for each username
                for username in usernames:
                    progress.update(
                        task, 
                        description=f"[cyan]Fetching {platform}/{username}..."
                    )
                    
                    try:
                        # Get platform client
                        client = self.client_manager.get_platform_client(platform)
                        
                        # Determine fetch limit
                        limit = fetch_options.get("targets", {}).get(
                            f"{platform}:{username}", {}
                        ).get("count", default_count)
                        
                        # Prepare kwargs for fetcher
                        kwargs = {
                            'username': username,
                            'cache': self.cache,
                            'force_refresh': force_refresh,
                            'fetch_limit': limit,
                            'allow_external_media': self.args.unsafe_allow_external_media
                        }

                        # Add client based on platform requirements
                        platforms_requiring_client = ["twitter", "reddit", "bluesky"]
                        if platform == "mastodon":
                            kwargs['clients'], kwargs['default_client'] = client
                        elif platform in platforms_requiring_client:
                            kwargs['client'] = client
                        
                        # Execute fetch
                        data = fetcher(**kwargs)
                        
                        if data:
                            result.add_success(platform, username, data)
                        else:
                            result.add_failure(
                                platform, username, "NoData", 
                                "Fetcher returned no data"
                            )
                    
                    except RateLimitExceededError as e:
                        result.add_rate_limit(platform, username)
                        handle_rate_limit(
                            console, f"{platform.capitalize()} Fetch", e, 
                            should_raise=False
                        )
                    
                    except UserNotFoundError as e:
                        result.add_failure(platform, username, "NotFound", str(e))
                    
                    except AccessForbiddenError as e:
                        result.add_failure(platform, username, "Forbidden", str(e))
                    
                    except Exception as e:
                        logger.error(
                            f"Fetch failed for {platform}/{username}: {e}", 
                            exc_info=True
                        )
                        result.add_failure(
                            platform, username, "Unexpected", 
                            f"Unexpected error: {type(e).__name__}"
                        )
                    
                    finally:
                        progress.advance(task)
        
        # Display summary
        self._display_fetch_summary(result, console)
        
        return result

    def _display_fetch_summary(self, result: FetchResult, console: Console):
        """Display a summary of fetch results."""
        if not result.failed and not result.rate_limited:
            console.print(f"[green]✓ All fetches successful ({len(result.successful)} targets)[/green]")
            return
        
        console.print(f"\n[yellow]Fetch Summary: {result.get_summary()}[/yellow]")
        
        if result.failed or result.rate_limited:
            table = Table(title="Collection Issues", show_header=True)
            table.add_column("Platform", style="cyan")
            table.add_column("Username", style="magenta")
            table.add_column("Issue", style="yellow")
            
            for platform, username, error_type, message in result.failed:
                table.add_row(platform, username, f"{error_type}: {message}")
            
            for platform, username in result.rate_limited:
                table.add_row(platform, username, "Rate Limited")
            
            console.print(table)

    def _perform_vision_analysis(
        self,
        successful_fetches: List[tuple],
        console: Console
    ) -> Dict[str, Any]:
        """
        Perform vision analysis on images with graceful error handling.
        
        Returns:
            Statistics about vision processing
        """
        # Collect all images to analyze
        images_to_analyze = []
        for platform, username, user_data in successful_fetches:
            for post in user_data.get("posts", []):
                for media_item in post.get("media", []):
                    if (path_str := media_item.get("local_path")) and not media_item.get("analysis"):
                        path = Path(path_str)
                        if path.exists() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                            images_to_analyze.append((
                                path,
                                {
                                    "url": media_item["url"],
                                    "context": f"{platform} user {username}",
                                    "media_item": media_item,
                                    "user_data": user_data,
                                    "platform": platform,
                                    "username": username
                                }
                            ))
        
        if not images_to_analyze:
            return {"total": 0, "analyzed": 0, "failed": 0, "skipped": 0}
        
        # Process images in batch with graceful error handling
        console.print(f"\n[cyan]Processing {len(images_to_analyze)} images...[/cyan]")
        
        analyzed_count = 0
        failed_count = 0
        skipped_count = 0
        modified_users = set()
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console
        ) as progress:
            task = progress.add_task(
                "[cyan]Analyzing images...", 
                total=len(images_to_analyze)
            )
            
            for file_path, metadata in images_to_analyze:
                progress.update(
                    task,
                    description=f"[cyan]Analyzing image from {metadata['platform']}/{metadata['username']}..."
                )
                
                # Process single image with error isolation
                result = self.image_processor.process_single_image(
                    file_path,
                    analyze_func=self.llm.analyze_image,
                    source_url=metadata["url"],
                    context=metadata["context"]
                )
                
                if result.status == ProcessingStatus.SUCCESS and result.analysis:
                    # Update media item with analysis
                    metadata["media_item"]["analysis"] = result.analysis
                    modified_users.add((metadata["platform"], metadata["username"]))
                    analyzed_count += 1
                
                elif result.status == ProcessingStatus.RATE_LIMITED:
                    console.print(
                        "[bold red]Vision model rate limit hit. Stopping image analysis.[/bold red]"
                    )
                    skipped_count = len(images_to_analyze) - analyzed_count - failed_count
                    break
                
                else:
                    # Log failure but continue processing
                    logger.warning(
                        f"Image analysis failed for {file_path}: {result.error_message}"
                    )
                    failed_count += 1
                
                progress.advance(task)
        
        # Save updated caches for modified users
        for platform, username in modified_users:
            # Find the user data
            for p, u, user_data in successful_fetches:
                if p == platform and u == username:
                    self.cache.save(platform, username, user_data)
                    break
        
        stats = {
            "total": len(images_to_analyze),
            "analyzed": analyzed_count,
            "failed": failed_count,
            "skipped": skipped_count
        }
        
        # Display summary
        if analyzed_count > 0:
            console.print(
                f"[green]✓ Analyzed {analyzed_count}/{len(images_to_analyze)} images[/green]"
            )
        if failed_count > 0:
            console.print(
                f"[yellow]⚠ {failed_count} images failed analysis (continued processing)[/yellow]"
            )
        if skipped_count > 0:
            console.print(
                f"[yellow]⚠ {skipped_count} images skipped due to rate limit[/yellow]"
            )
        
        return stats

    def _generate_analysis_report(
        self,
        fetch_result: FetchResult,
        query: str,
        vision_stats: Dict[str, Any],
        console: Console
    ) -> Dict[str, Any]:
        """Generate the final analysis report."""
        with console.status("[magenta]Synthesizing report with LLM..."):
            try:
                # Prepare data for LLM
                collected_data = {p: [] for p in set(p for p, _, _ in fetch_result.successful)}
                for platform, username, data in fetch_result.successful:
                    collected_data[platform].append({"username_key": username, "data": data})
                
                # Run analysis
                report = self.llm.run_analysis(collected_data, query)
                
            except RateLimitExceededError as e:
                handle_rate_limit(console, "LLM Analysis", e)
                return {
                    "metadata": {},
                    "report": "[red]Analysis aborted due to LLM rate limit.[/red]",
                    "error": True
                }
            except Exception as e:
                logger.error(f"LLM analysis failed: {e}", exc_info=True)
                return {
                    "metadata": {},
                    "report": f"[red]LLM analysis failed: {e}[/red]",
                    "error": True
                }
        
        # Build metadata
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text_model = os.getenv("ANALYSIS_MODEL")
        img_model = os.getenv("IMAGE_ANALYSIS_MODEL")
        
        platforms_used = {p: [u for p2, u, _ in fetch_result.successful if p2 == p]
                         for p in set(p for p, _, _ in fetch_result.successful)}
        
        metadata = {
            "query": query,
            "targets": platforms_used,
            "generated_utc": ts,
            "mode": 'Offline' if self.args.offline else 'Online',
            "models": {"text": text_model, "image": img_model},
            "fetch_stats": {
                "successful": len(fetch_result.successful),
                "failed": len(fetch_result.failed),
                "rate_limited": len(fetch_result.rate_limited)
            },
            "vision_stats": vision_stats
        }
        
        # Build header
        header = (
            f"# OSINT Analysis Report\n\n"
            f"**Query:** `{query}`\n"
            f"**Generated:** `{ts}`\n"
            f"**Mode:** `{metadata['mode']}`\n"
            f"**Models Used:**\n- Text: `{text_model}`\n- Image: `{img_model}`\n"
            f"**Data Sources:** {len(fetch_result.successful)} targets\n"
        )
        
        if vision_stats.get("analyzed", 0) > 0:
            header += f"**Images Analyzed:** {vision_stats['analyzed']}/{vision_stats['total']}\n"
        
        header += "\n---\n\n"
        
        return {"metadata": metadata, "report": header + report, "error": False}

    def process_stdin(self):
        """Processes an analysis request provided via stdin as a JSON object."""
        stderr_console = Console(stderr=True)
        stderr_console.print("[cyan]Processing analysis request from stdin...[/cyan]")
        
        # Parse JSON with detailed error handling
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            error_detail = {
                "error": "Invalid JSON",
                "message": str(e),
                "line": e.lineno,
                "column": e.colno,
                "help": "Ensure your JSON is properly formatted. Example: {\"platforms\": {\"twitter\": [\"user1\"]}, \"query\": \"What are their interests?\"}"
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        # Validate required fields
        required_fields = ["platforms", "query"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            error_detail = {
                "error": "Missing required fields",
                "missing_fields": missing,
                "provided_fields": list(data.keys()),
                "example": {
                    "platforms": {"twitter": ["example_user"], "reddit": ["example_user"]},
                    "query": "What are their primary interests and communication patterns?",
                    "fetch_options": {"default_count": 50}
                }
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        # Validate field types
        platforms = data.get("platforms")
        query = data.get("query")
        fetch_options = data.get("fetch_options")
        
        if not isinstance(platforms, dict):
            error_detail = {
                "error": "Invalid field type",
                "field": "platforms",
                "expected_type": "dict",
                "received_type": type(platforms).__name__,
                "example": {"twitter": ["user1", "user2"], "reddit": ["user3"]}
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        if not isinstance(query, str) or not query.strip():
            error_detail = {
                "error": "Invalid field type or empty value",
                "field": "query",
                "expected_type": "non-empty string",
                "received_type": type(query).__name__,
                "example": "What are the user's primary interests and recent activities?"
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        if not platforms:
            error_detail = {
                "error": "Empty platforms",
                "message": "The 'platforms' field must contain at least one platform with usernames",
                "example": {"twitter": ["example_user"]}
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        # Validate platform configuration
        try:
            available_platforms = self.client_manager.get_available_platforms(check_creds=True)
            
            if not available_platforms:
                error_detail = {
                    "error": "No platforms configured",
                    "message": "No platform API credentials are configured. Please check your .env file.",
                    "help": "At minimum, configure credentials for: TWITTER_BEARER_TOKEN, REDDIT_CLIENT_ID/SECRET, or BLUESKY_IDENTIFIER/SECRET"
                }
                sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
                sys.exit(1)
            
            # Filter to only valid platforms and sanitize usernames
            query_platforms = {}
            invalid_platforms = []
            
            for platform, usernames in platforms.items():
                if platform not in available_platforms:
                    invalid_platforms.append(platform)
                    continue
                
                if not isinstance(usernames, list):
                    error_detail = {
                        "error": "Invalid usernames format",
                        "platform": platform,
                        "expected_type": "list of strings",
                        "received_type": type(usernames).__name__,
                        "example": {"twitter": ["user1", "user2"]}
                    }
                    sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
                    sys.exit(1)
                
                sanitized = [sanitize_username(u.strip()) for u in usernames if u and u.strip()]
                if sanitized:
                    query_platforms[platform] = sanitized
            
            # Report invalid platforms
            if invalid_platforms:
                stderr_console.print(
                    f"[yellow]Warning: Skipping unconfigured platforms: {', '.join(invalid_platforms)}[/yellow]"
                )
                stderr_console.print(
                    f"[dim]Available platforms: {', '.join(available_platforms)}[/dim]"
                )
            
            if not query_platforms:
                error_detail = {
                    "error": "No valid platforms found",
                    "message": "None of the requested platforms are configured or contain valid usernames",
                    "requested_platforms": list(platforms.keys()),
                    "available_platforms": available_platforms,
                    "help": "Configure credentials for at least one requested platform in your .env file"
                }
                sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
                sys.exit(1)
            
        except RuntimeError as e:
            error_detail = {
                "error": "Platform initialization failed",
                "message": str(e),
                "help": "Check your .env file for correct API credentials"
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        # Run the analysis
        try:
            result = self.analyze(
                query_platforms, 
                query, 
                fetch_options=fetch_options, 
                console=stderr_console
            )
            
        except Exception as e:
            logger.error(f"Analysis failed during execution: {e}", exc_info=True)
            error_detail = {
                "error": "Analysis execution failed",
                "message": str(e),
                "type": type(e).__name__
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(1)
        
        # Handle results
        if result.get("error"):
            # Analysis completed but with errors
            error_detail = {
                "error": "Analysis completed with errors",
                "report": result.get("report", "No report available"),
                "metadata": result.get("metadata", {})
            }
            sys.stderr.write(json.dumps(error_detail, indent=2) + "\n")
            sys.exit(2)
        
        # Success - output the report
        if self.args.no_auto_save:
            # Print to stdout (machine-readable mode)
            if self.args.format == "json":
                output = {
                    "success": True,
                    "metadata": result.get("metadata", {}),
                    "report": result.get("report", "")
                }
                print(json.dumps(output, indent=2))
            else:
                # Print markdown directly
                print(result["report"])
        else:
            # Save to file and report the path
            output_path = self._save_output_headless(result, self.args.format)
            success_detail = {
                "success": True,
                "output_file": str(output_path),
                "metadata": result.get("metadata", {})
            }
            # Print success info to stdout (JSON format for easy parsing)
            print(json.dumps(success_detail, indent=2))
        
        sys.exit(0)

    def _save_output_headless(self, result: Dict[str, Any], file_format: str) -> Path:
        """
        Saves the analysis report to a file in non-interactive mode.
        
        Returns:
            Path: The path to the saved file
        """
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
        
        # Log to stderr so it doesnt interfere with stdout JSON
        sys.stderr.write(f"Analysis saved to: {path}\n")
        
        return path