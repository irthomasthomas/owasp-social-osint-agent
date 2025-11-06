import base64
import collections
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from openai import (APIError, OpenAI,
                    RateLimitError)
from PIL import Image

from .exceptions import RateLimitExceededError
from .utils import (SUPPORTED_IMAGE_EXTENSIONS, extract_and_resolve_urls,
                    get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.llm")

# Helper to load prompts safely
_CURRENT_DIR = Path(__file__).parent

def _load_prompt(filename: str) -> str:
    """Loads a prompt file from the 'prompts' directory within this module."""
    try:
        # Construct a path relative to the current file's location.
        prompt_path = _CURRENT_DIR / "prompts" / filename
        return prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(f"CRITICAL: Prompt file not found at {prompt_path}")
        raise
    except Exception as e:
        logger.error(f"CRITICAL: Failed to load prompt file {prompt_path}: {e}")
        raise

class LLMAnalyzer:
    def __init__(self, is_offline: bool):
        self.is_offline = is_offline
        self._llm_client_instance: Optional[OpenAI] = None
        # Load prompts on initialization
        self.system_analysis_prompt_template = _load_prompt("system_analysis.prompt")
        self.image_analysis_prompt_template = _load_prompt("image_analysis.prompt")

    @property
    def client(self) -> OpenAI:
        """Initializes and returns the OpenAI client for LLM calls."""
        if self._llm_client_instance is None:
            try:
                api_key = os.environ["LLM_API_KEY"]
                base_url = os.environ["LLM_API_BASE_URL"]
                
                headers: Dict[str, str] = {}
                if "openrouter.ai" in base_url.lower():
                    headers["HTTP-Referer"] = os.getenv("OPENROUTER_REFERER", "http://localhost:3000")
                    headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "SocialOSINTAgent")

                self._llm_client_instance = OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    default_headers=headers or None,
                )
                logger.info(f"LLM client initialized for base URL: {base_url}")
            except KeyError as e:
                raise RuntimeError(f"LLM config missing: {e} not found in environment.")
            except Exception as e:
                raise RuntimeError(f"Failed to initialize LLM client: {e}")
        return self._llm_client_instance

    def analyze_image(self, file_path: Path, source_url: str, context: str = "") -> Optional[str]:
        if self.is_offline:
            logger.info(f"Offline mode: Skipping LLM image analysis for {file_path}.")
            return None
        if not file_path.exists() or file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            return None

        temp_path = None
        try:
            with Image.open(file_path) as img:
                img_to_process = img
                if getattr(img, "is_animated", False):
                    img.seek(0)
                    img_to_process = img.copy()
                if img_to_process.mode != "RGB":
                    if img_to_process.mode == "P" and "transparency" in img_to_process.info:
                        img_to_process = img_to_process.convert("RGBA")
                    if img_to_process.mode == "RGBA":
                        bg = Image.new("RGB", img_to_process.size, (255, 255, 255))
                        bg.paste(img_to_process, mask=img_to_process.split()[3])
                        img_to_process = bg
                    else:
                        img_to_process = img_to_process.convert("RGB")
                
                max_dim = 1536
                if max(img_to_process.size) > max_dim:
                    img_to_process.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                
                temp_path = file_path.with_suffix(".processed.jpg")
                img_to_process.save(temp_path, "JPEG", quality=85)
                analysis_file_path = temp_path

            base64_image = base64.b64encode(analysis_file_path.read_bytes()).decode("utf-8")
            image_data_url = f"data:image/jpeg;base64,{base64_image}"

            prompt_text = self.image_analysis_prompt_template.format(context=context)
            model = os.environ["IMAGE_ANALYSIS_MODEL"]
            completion = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}}]}],
                max_tokens=1024,
            )
            analysis_text = completion.choices[0].message.content.strip() if completion.choices[0].message.content else None
            if not analysis_text: return None
            
            return f"- **Image Source:** [{source_url}]({source_url})\n- **Analysis:**\n{analysis_text}"

        except APIError as e:
            if isinstance(e, RateLimitError): raise RateLimitExceededError("LLM Image Analysis")
            logger.error(f"LLM API error during image analysis: {e}")
            return None
        except Exception as e:
            logger.error(f"Error during image analysis for {file_path}: {e}", exc_info=True)
            return None
        finally:
            if temp_path and temp_path.exists(): temp_path.unlink()

    def _format_text_data(self, platform: str, username: str, data: dict) -> str:
        # This method's logic remains the same as before.
        MAX_ITEMS_PER_TYPE = 25
        TEXT_SNIPPET_LENGTH = 750
        if not data: return ""
        
        output = []
        user_info = data.get("user_info") or data.get("profile_info") or data.get("user_profile")
        prefix = {"twitter": "@", "reddit": "u/"}.get(platform, "")
        handle = user_info.get("username") or user_info.get("name") or user_info.get("handle") or user_info.get("acct") or username if user_info else username
        output.append(f"### {platform.capitalize()} Data Summary for: {prefix}{handle}")

        if user_info:
            output.append("\n**User Profile:**")
            created = get_sort_key(user_info, "created_at") or get_sort_key(user_info, "created_utc")
            output.append(f"- Account Created: {created.strftime('%Y-%m-%d') if created > datetime.min.replace(tzinfo=timezone.utc) else 'N/A'}")
            if platform == "twitter":
                pm = user_info.get("public_metrics", {})
                output.append(f"- Description: {user_info.get('description', '')}")
                output.append(f"- Stats: Followers={pm.get('followers_count','N/A')}, Following={pm.get('following_count','N/A')}, Tweets={pm.get('tweet_count','N/A')}")
            elif platform == "reddit":
                output.append(f"- Karma: Link={user_info.get('link_karma','N/A')}, Comment={user_info.get('comment_karma','N/A')}")
            elif platform == "mastodon":
                output.append(f"- Bio: {user_info.get('note_text', '')}")
                output.append(f"- Stats: Followers={user_info.get('followers_count','N/A')}, Following={user_info.get('following_count','N/A')}, Posts={user_info.get('statuses_count','N/A')}")

        if data.get("stats"):
            output.append("\n**Cached Activity Overview:**")
            output.append(f"- {json.dumps(data['stats'])}")
        
        # Detailed Item Formatting with null checks
        if platform == "twitter" and data.get("tweets"):
            output.append(f"\n**Recent Tweets (up to {MAX_ITEMS_PER_TYPE}):**")
            for i, t in enumerate(data["tweets"][:MAX_ITEMS_PER_TYPE]):
                if t is not None:
                    ts = get_sort_key(t, "created_at").strftime("%Y-%m-%d")
                    info = []
                    if t.get("replied_to_user_info"): info.append(f"Reply to @{t['replied_to_user_info']['username']}")
                    if any(r['type'] == 'quoted' for r in t.get("referenced_tweets",[])): info.append("Quotes a tweet")
                    if t.get("media"): info.append(f"Media: {len(t['media'])}")
                    info_str = f" ({', '.join(info)})" if info else ""
                    text = t.get("text", "")[:TEXT_SNIPPET_LENGTH]
                    output.append(f"- Tweet {i+1} ({ts}){info_str}:\n  Content: {text}\n  Metrics: {t.get('metrics')}")
        elif platform == "reddit":
            if data.get("submissions"):
                output.append(f"\n**Recent Submissions (up to {MAX_ITEMS_PER_TYPE}):**")
                for i, s in enumerate(data["submissions"][:MAX_ITEMS_PER_TYPE]):
                    if s is not None:
                        ts = get_sort_key(s, "created_utc").strftime("%Y-%m-%d")
                        output.append(f"- Submission {i+1} in r/{s.get('subreddit','?')} ({ts}):\n  Title: {s.get('title')}\n  Score: {s.get('score',0)}")
            if data.get("comments"):
                output.append(f"\n**Recent Comments (up to {MAX_ITEMS_PER_TYPE}):**")
                for i, c in enumerate(data["comments"][:MAX_ITEMS_PER_TYPE]):
                    if c is not None:
                        ts = get_sort_key(c, "created_utc").strftime("%Y-%m-%d")
                        text = c.get("text","")[:TEXT_SNIPPET_LENGTH]
                        output.append(f"- Comment {i+1} in r/{c.get('subreddit','?')} ({ts}):\n  Content: {text}\n  Score: {c.get('score',0)}")
        elif platform == "mastodon" and data.get("posts"):
            output.append(f"\n**Recent Posts (up to {MAX_ITEMS_PER_TYPE}):**")
            for i, p in enumerate(data["posts"][:MAX_ITEMS_PER_TYPE]):
                if p is not None:
                    ts = get_sort_key(p, "created_at").strftime("%Y-%m-%d")
                    info = ["Boost"] if p.get("is_reblog") else []
                    if p.get("media"): info.append(f"Media: {len(p['media'])}")
                    info_str = f" ({', '.join(info)})" if info else ""
                    text = p.get("text_cleaned", "")[:TEXT_SNIPPET_LENGTH]
                    output.append(f"- Post {i+1} ({ts}){info_str}:\n  Content: {text}\n  Stats: Favs={p.get('favourites_count',0)}, Boosts={p.get('reblogs_count',0)}")

        return "\n".join(output)

    def _analyze_shared_links(self, platforms_data: Dict[str, List[Dict]]) -> str:
        # This method's logic remains the same as before.
        all_urls = []
        platform_domains = {"twitter.com", "x.com", "t.co", "reddit.com", "redd.it", "bsky.app", "news.ycombinator.com", "youtube.com", "youtu.be"}

        try:
            for platform, user_data_list in platforms_data.items():
                if not isinstance(user_data_list, list): continue
                for user_data in user_data_list:
                    if not isinstance(user_data, dict): continue
                    data = user_data.get("data", {})
                    if not isinstance(data, dict): continue
                    if platform == "twitter":
                        tweets = data.get("tweets", [])
                        if isinstance(tweets, list):
                            for t in tweets:
                                if t and isinstance(t, dict) and t.get("entities_raw") and isinstance(t["entities_raw"], dict) and t["entities_raw"].get("urls") and isinstance(t["entities_raw"]["urls"], list):
                                    for url_entity in t["entities_raw"]["urls"]:
                                        if isinstance(url_entity, dict) and "expanded_url" in url_entity: all_urls.append(url_entity["expanded_url"])
                    elif platform == "reddit":
                        submissions = data.get("submissions", [])
                        if isinstance(submissions, list):
                            for s in submissions:
                                if s and isinstance(s, dict):
                                    if s.get("link_url"): all_urls.append(s["link_url"])
                                    if s.get("text"): all_urls.extend(extract_and_resolve_urls(s["text"]))
                        comments = data.get("comments", [])
                        if isinstance(comments, list):
                            for c in comments:
                                if c and isinstance(c, dict):
                                    if c.get("text"): all_urls.extend(extract_and_resolve_urls(c["text"]))
                    elif platform == "hackernews":
                        items = data.get("items", [])
                        if isinstance(items, list):
                            for i in items:
                                if i and isinstance(i, dict):
                                    if i.get("url"): all_urls.append(i["url"])
                                    if i.get("text"): all_urls.extend(extract_and_resolve_urls(i["text"]))
                    else:
                        posts = data.get("posts", [])
                        if isinstance(posts, list):
                            for p in posts:
                                if p and isinstance(p, dict):
                                    if p.get("text"): all_urls.extend(extract_and_resolve_urls(p["text"]))
                                    if p.get("text_cleaned"): all_urls.extend(extract_and_resolve_urls(p["text_cleaned"]))
        except Exception as e:
            logger.error(f"Error in _analyze_shared_links: {e}", exc_info=True)
            return ""

        if not all_urls: return ""

        domain_counts = collections.Counter(urlparse(url).netloc.replace("www.", "") for url in all_urls if urlparse(url).netloc and urlparse(url).netloc.replace("www.", "") not in platform_domains)
        if not domain_counts: return ""

        output = ["## Top Shared Domains"]
        for domain, count in domain_counts.most_common(10):
            output.append(f"- **{domain}:** {count} link(s)")
        
        return "\n".join(output)

    def run_analysis(self, platforms_data: Dict[str, List[Dict]], query: str) -> str:
        """Collects data summaries and uses LLM to analyze it."""
        collected_summaries, all_media_analyses = [], []
        
        current_ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for platform, user_data_list in platforms_data.items():
            for user_data in user_data_list:
                username = user_data.get("username_key", "unknown")
                summary = self._format_text_data(platform, username, user_data["data"])
                if summary: collected_summaries.append(summary)
                
                # The media analyses are now populated by the analyzer, so this works
                media_analyses = [ma for ma in user_data["data"].get("media_analysis", []) if ma]
                if media_analyses: all_media_analyses.extend(media_analyses)
        
        if not collected_summaries and not all_media_analyses:
            return "[yellow]No data available for analysis.[/yellow]"

        components = []
        if all_media_analyses:
            components.append("## Consolidated Media Analysis:\n\n" + "\n\n".join(sorted(list(set(all_media_analyses)))))
        
        shared_links_summary = self._analyze_shared_links(platforms_data)
        if shared_links_summary:
            components.append(shared_links_summary)

        if collected_summaries:
            components.append("## Collected Textual & Activity Data Summary:\n\n" + "\n\n---\n\n".join(collected_summaries))
        
        system_prompt = self.system_analysis_prompt_template.format(current_timestamp=current_ts_str)
        user_prompt = f"**Analysis Query:** {query}\n\n**Provided Data:**\n\n" + "\n\n===\n\n".join(components)
        
        text_model = os.environ["ANALYSIS_MODEL"]
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        
        # --- REFACTOR: REMOVED THREADING, DIRECT CALL ---
        try:
            completion = self.client.chat.completions.create(
                model=text_model, messages=messages, max_tokens=3500, temperature=0.5
            )
            if not completion or not completion.choices:
                raise RuntimeError("LLM API call returned no completion.")
            
            return completion.choices[0].message.content or ""
        except APIError as e:
            logger.error(f"LLM API error during text analysis: {e}")
            raise RuntimeError(f"LLM API request failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during text analysis: {e}", exc_info=True)
            raise RuntimeError(f"An unexpected error occurred during LLM analysis: {e}") from e