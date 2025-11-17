"""
Handles all interactions with Large Language Models (LLMs).

This module is responsible for:
- Loading analysis prompts from the filesystem.
- Formatting collected user data into a structured summary for the LLM.
- Calling the vision model for image analysis.
- Calling the text model for the final report synthesis.
"""

import base64
import collections
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from openai import APIError, OpenAI, RateLimitError
from PIL import Image

from .exceptions import RateLimitExceededError
from .utils import (SUPPORTED_IMAGE_EXTENSIONS, UserData,
                    extract_and_resolve_urls, get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.llm")
_CURRENT_DIR = Path(__file__).parent

def _load_prompt(filename: str) -> str:
    """
    Loads a prompt template from the 'prompts' directory.

    Args:
        filename: The name of the prompt file (e.g., 'system_analysis.prompt').

    Returns:
        The content of the prompt file as a string.

    Raises:
        FileNotFoundError: If the prompt file cannot be found.
    """
    try:
        prompt_path = _CURRENT_DIR / "prompts" / filename
        return prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"CRITICAL: Prompt file not found at {prompt_path}")
        raise

class LLMAnalyzer:
    """A client for performing text and vision analysis using an OpenAI-compatible API."""
    def __init__(self, is_offline: bool):
        """
        Initializes the LLMAnalyzer.

        Args:
            is_offline: If True, all network-related LLM calls will be skipped.
        """
        self.is_offline = is_offline
        self._llm_client_instance: Optional[OpenAI] = None
        self.system_analysis_prompt_template = _load_prompt("system_analysis.prompt")
        self.image_analysis_prompt_template = _load_prompt("image_analysis.prompt")

    @property
    def client(self) -> OpenAI:
        """
        Lazily initializes and returns the OpenAI-compatible client.

        Reads configuration from environment variables.

        Returns:
            An initialized OpenAI client instance.

        Raises:
            RuntimeError: If necessary LLM environment variables are not set.
        """
        if self._llm_client_instance is None:
            try:
                api_key = os.environ["LLM_API_KEY"]
                base_url = os.environ["LLM_API_BASE_URL"]
                headers: Dict[str, str] = {}
                # Add specific headers for providers like OpenRouter for better tracking.
                if "openrouter.ai" in base_url.lower():
                    headers["HTTP-Referer"] = os.getenv("OPENROUTER_REFERER", "http://localhost:3000")
                    headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "SocialOSINTAgent")
                
                self._llm_client_instance = OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=httpx.Timeout(60.0),
                    default_headers=headers or None
                )
                logger.info(f"LLM client initialized for base URL: {base_url}")
            except KeyError as e:
                raise RuntimeError(f"LLM config missing: {e} not found in environment.")
        return self._llm_client_instance

    def analyze_image(self, file_path: Path, source_url: str, context: str = "") -> Optional[str]:
        """
        Analyzes a single image using a vision-capable LLM.

        The method handles image pre-processing (resizing, converting) before
        sending it to the LLM.

        Args:
            file_path: The local path to the image file.
            source_url: The original URL of the image, for context.
            context: Additional context about the image (e.g., who posted it).

        Returns:
            A string containing the AI's analysis of the image, or None on failure.
        
        Raises:
            RateLimitExceededError: If the vision model API rate limit is hit.
        """
        if self.is_offline or not file_path.exists() or file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            return None
        
        temp_path = None
        try:
            # Pre-process image to ensure compatibility and reasonable size
            with Image.open(file_path) as img:
                img_to_process = img
                if getattr(img, "is_animated", False):
                    img.seek(0)
                    img_to_process = img.copy()
                if img_to_process.mode != "RGB":
                    img_to_process = img_to_process.convert("RGB")
                
                max_dim = 1536
                if max(img_to_process.size) > max_dim:
                    img_to_process.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                
                # Save to a temporary JPEG for consistent encoding
                temp_path = file_path.with_suffix(".processed.jpg")
                img_to_process.save(temp_path, "JPEG", quality=85)

            base64_image = base64.b64encode(temp_path.read_bytes()).decode("utf-8")
            prompt_text = self.image_analysis_prompt_template.format(context=context)
            model = os.environ["IMAGE_ANALYSIS_MODEL"]
            
            completion = self.client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt_text}, 
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}}
                    ]
                }],
                max_tokens=1024,
            )
            return completion.choices[0].message.content.strip() if completion.choices and completion.choices[0].message.content else None
        except APIError as e:
            if isinstance(e, RateLimitError):
                raise RateLimitExceededError("LLM Image Analysis", original_exception=e)
            logger.error(f"LLM API error during image analysis for {file_path}: {e}")
            return None
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def _format_user_data_summary(self, user_data: UserData) -> str:
        """
        Formats a UserData object into a structured Markdown summary for the LLM.

        Args:
            user_data: The normalized data object for a single user on a platform.

        Returns:
            A formatted string summarizing the user's profile and recent activity.
        """
        if not (profile := user_data.get("profile")):
            return ""
        
        platform = profile.get("platform", "Unknown").capitalize()
        username = profile.get("username", "N/A")
        output = [f"### {platform} Data Summary for: {username}"]
        
        output.append("\n**User Profile:**")
        if profile.get("created_at"):
            created_dt = get_sort_key(profile, "created_at")
            # Ensure we don't format a min-date placeholder
            if created_dt > datetime(1970, 1, 2, tzinfo=timezone.utc):
                output.append(f"- Account Created: {created_dt.strftime('%Y-%m-%d')}")
        
        if bio := profile.get("bio"):
            output.append(f"- Bio: {bio.strip()}")
        if metrics := profile.get("metrics"):
            metrics_str = ', '.join(f"{k.replace('_', ' ').capitalize()}={v}" for k, v in metrics.items())
            output.append(f"- Stats: {metrics_str}")

        if posts := user_data.get("posts"):
            output.append(f"\n**Recent Activity (up to 25 items shown):**")
            for i, post in enumerate(posts[:25]):
                ts = get_sort_key(post, "created_at").strftime("%Y-%m-%d")
                info = [post.get('type', 'post')]
                if post.get('media'):
                    info.append(f"Media: {len(post['media'])}")
                if repo := post.get('context', {}).get('repo'):
                    info.append(f"Repo: {repo}")
                info_str = f" ({', '.join(info)})" if info else ""
                text_snippet = post.get('text', '')[:750].strip()
                output.append(f"- Item {i+1} ({ts}){info_str}:\n  Content: {text_snippet}")

        return "\n".join(output)

    def _analyze_shared_links(self, all_user_data: List[UserData]) -> str:
        """
        Extracts all external URLs from posts, counts domain frequency, and returns a summary.

        Args:
            all_user_data: A list of all UserData objects from all targets.

        Returns:
            A formatted Markdown string summarizing the top shared domains.
        """
        all_urls = [
            link for user_data in all_user_data 
            for post in user_data.get("posts", []) 
            for link in post.get("external_links", [])
        ]
        if not all_urls:
            return ""

        # Exclude links to the social platforms themselves to find external shares.
        platform_domains = {"twitter.com", "x.com", "t.co", "reddit.com", "redd.it", "bsky.app", "news.ycombinator.com"}
        domain_counts = collections.Counter(
            urlparse(url).netloc.replace("www.", "") for url in all_urls 
            if urlparse(url).netloc and urlparse(url).netloc.replace("www.", "") not in platform_domains
        )
        if not domain_counts:
            return ""

        output = ["## Top Shared Domains"]
        output.extend(f"- **{domain}:** {count} link(s)" for domain, count in domain_counts.most_common(10))
        return "\n".join(output)

    def run_analysis(self, platforms_data: Dict[str, List[Dict]], query: str) -> str:
        """
        Synthesizes the final report by sending all collected data to the text LLM.

        Args:
            platforms_data: The raw collected data from the analyzer.
            query: The user's analysis query.

        Returns:
            The final analysis report generated by the LLM.

        Raises:
            RuntimeError: If the LLM API request fails.
        """
        collected_summaries, all_media_analyses, all_user_data_flat = [], [], []
        
        for user_data_list in platforms_data.values():
            for user_data_dict in user_data_list:
                user_data: UserData = user_data_dict["data"]
                all_user_data_flat.append(user_data)
                if summary := self._format_user_data_summary(user_data):
                    collected_summaries.append(summary)
                for post in user_data.get("posts", []):
                    for media in post.get("media", []):
                        if analysis := media.get("analysis"):
                            # Format with a clickable link for the final report
                            all_media_analyses.append(f"- **Image Source:** [{media['url']}]({media['url']})\n- **Analysis:**\n{analysis}")

        if not collected_summaries and not all_media_analyses:
            return "[yellow]No data available for analysis.[/yellow]"

        # Assemble the different components of the final prompt
        components = []
        if all_media_analyses:
            components.append("## Consolidated Media Analysis:\n\n" + "\n\n".join(sorted(list(set(all_media_analyses)))))
        if shared_links_summary := self._analyze_shared_links(all_user_data_flat):
            components.append(shared_links_summary)
        if collected_summaries:
            components.append("## Collected Textual & Activity Data Summary:\n\n" + "\n\n---\n\n".join(collected_summaries))
        
        current_ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        system_prompt = self.system_analysis_prompt_template.format(current_timestamp=current_ts_str)
        user_prompt = f"**Analysis Query:** {query}\n\n**Provided Data:**\n\n" + "\n\n===\n\n".join(components)
        
        try:
            model = os.environ["ANALYSIS_MODEL"]
            completion = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=3500,
                temperature=0.5
            )
            return completion.choices[0].message.content or ""
        except APIError as e:
            logger.error(f"LLM API error during text analysis: {e}")
            raise RuntimeError(f"LLM API request failed: {e}") from e