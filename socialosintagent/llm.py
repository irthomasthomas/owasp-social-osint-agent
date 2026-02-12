"""
Handles all interactions with Large Language Models (LLMs).

This module is responsible for:
- Loading analysis prompts from the filesystem.
- Formatting collected user data into a structured summary for the LLM.
- Calling the vision model for image analysis.
- Calling the text model for the final report synthesis.
- XML structural delimiting for all untrusted content
- Input sanitization and escape functions
- Prompt injection detection and reporting
- Defense-in-depth with multiple protective layers
"""

import base64
import collections
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from openai import APIError, OpenAI, RateLimitError

from socialosintagent.exceptions import RateLimitExceededError
from socialosintagent.utils import (SUPPORTED_IMAGE_EXTENSIONS, UserData, get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.llm")
_CURRENT_DIR = Path(__file__).parent

# Prompt injection detection patterns
INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?(previous|prior)\s+instructions',
    r'you\s+are\s+now\s+(a|an)',
    r'disregard\s+(your|the|all)',
    r'new\s+instructions?:',
    r'</(text_evidence|vision_evidence|network_evidence|user_query)>',  # Premature XML closing
    r'system\s+prompt',
    r'repeat\s+(your|the)\s+instructions',
    r'what\s+(are|is)\s+your\s+(instructions|guidelines|rules)',
    r'debug\s+mode',
    r'developer\s+mode',
    r'admin\s+(mode|override)',
    r'you\s+must\s+(now|immediately)',
    r'end\s+of\s+(instructions|prompt|guidelines)',
]

def xml_escape(text: str) -> str:
    """
    Escape XML special characters to prevent tag injection.
    
    This prevents attackers from closing XML tags prematurely or injecting
    their own structural elements.
    
    Args:
        text: Raw text that may contain XML special chars
        
    Returns:
        Escaped text safe for XML content
    """
    if not text:
        return ""
    
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))


def delimit_lines(text: str, prefix: str = "DATA") -> str:
    """
    Prefix each line with a delimiter to break injection syntax.
    
    This is an additional defense layer - even if XML escaping fails,
    line-by-line prefixing makes it harder for injected content to
    form valid LLM instructions.
    
    Args:
        text: Text to delimit
        prefix: Prefix to add (default: "DATA")
        
    Returns:
        Line-delimited text
    """
    if not text:
        return ""
    
    return '\n'.join(f"{prefix}: {line}" for line in text.split('\n'))


def detect_injection_attempt(text: str) -> List[str]:
    """
    Detect potential prompt injection attempts in text.
    
    Returns a list of matched attack patterns. Empty list means no
    obvious injection detected.
    
    Args:
        text: Text to scan for injection patterns
        
    Returns:
        List of matched pattern descriptions
    """
    if not text:
        return []
    
    detected = []
    text_lower = text.lower()
    
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            # Extract the actual matched text for logging
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                detected.append(f"Pattern '{pattern}' matched: '{match.group()}'")
    
    return detected


def sanitize_user_query(query: str) -> Tuple[str, List[str]]:
    """
    Sanitize and validate user query.
    
    Returns:
        Tuple of (sanitized_query, list_of_warnings)
    """
    warnings = []
    
    # Detect injection attempts
    if injections := detect_injection_attempt(query):
        warnings.append(f"Potential injection in query: {injections}")
    
    # Limit length to prevent token exhaustion attacks
    MAX_QUERY_LENGTH = 500
    if len(query) > MAX_QUERY_LENGTH:
        warnings.append(f"Query truncated from {len(query)} to {MAX_QUERY_LENGTH} chars")
        query = query[:MAX_QUERY_LENGTH] + "..."
    
    # XML escape the query
    sanitized = xml_escape(query)
    
    return sanitized, warnings


def sanitize_ugc_content(content: str, source_description: str) -> Tuple[str, List[str]]:
    """
    Sanitize untrusted user-generated content.
    
    Args:
        content: Raw UGC text
        source_description: Description for logging (e.g., "twitter post")
        
    Returns:
        Tuple of (sanitized_content, list_of_warnings)
    """
    warnings = []
    
    # Detect injection attempts
    if injections := detect_injection_attempt(content):
        warnings.append(f"Injection detected in {source_description}: {injections[:2]}")  # Limit output
        logger.warning(f"Injection attempt in {source_description}: {injections}")
    
    # XML escape
    sanitized = xml_escape(content)
    
    # Optional: Apply line delimiting for high-risk content
    # Uncomment if you want double defense
    # if injections:
    #     sanitized = delimit_lines(sanitized, prefix="UGC")
    
    return sanitized, warnings


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
        self.security_warnings_accumulated: List[str] = []

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
        Analyzes a single image using a vision-capable LLM with injection protection.

        Expects a preprocessed image file (JPEG, RGB) as produced by
        ImageProcessor.preprocess_image(). It encodes the file and sends
        it to the vision model.
        Args:
            file_path: The local path to the preprocessed image file.
            source_url: The original URL of the image, for context.
            context: Additional context about the image (e.g., who posted it).

        Returns:
            A string containing the AI's analysis of the image, or None on failure.
        
        Raises:
            RateLimitExceededError: If the vision model API rate limit is hit.
        """
        if self.is_offline or not file_path.exists() or file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            return None
        
        try:
            base64_image = base64.b64encode(file_path.read_bytes()).decode("utf-8")
            
            # Sanitize context string
            sanitized_context, warnings = sanitize_ugc_content(context, "image context")
            if warnings:
                self.security_warnings_accumulated.extend(warnings)
            
            # Wrap context in XML for structural protection
            prompt_text = self.image_analysis_prompt_template.format(
                context=f"<image_context>{sanitized_context}</image_context>"
            )
            
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
                temperature=0.1
            )
            
            result = completion.choices[0].message.content.strip() if completion.choices and completion.choices[0].message.content else None
            
            # Check if the result itself contains injection attempts (the model might have been compromised)
            if result:
                if injections := detect_injection_attempt(result):
                    logger.warning(f"Vision model output contains suspicious patterns: {injections}")
                    self.security_warnings_accumulated.append(f"Vision model output flagged: {injections[0]}")
            
            return result
            
        except APIError as e:
            if isinstance(e, RateLimitError):
                raise RateLimitExceededError("LLM Image Analysis", original_exception=e)
            logger.error(f"LLM API error during image analysis for {file_path}: {e}")
            return None

    def _format_user_data_summary(self, user_data: UserData) -> str:
        """
        Formats a UserData object into a structured summary with sanitization.

        Args:
            user_data: The normalized data object for a single user on a platform.

        Returns:
            A formatted string summarizing the user's profile and recent activity.
        """
        if not (profile := user_data.get("profile")):
            return ""
        
        platform = profile.get("platform", "Unknown").capitalize()
        username = profile.get("username", "N/A")
        
        # Sanitize profile data
        bio = profile.get("bio", "")
        bio_sanitized, warnings = sanitize_ugc_content(bio, f"{platform} bio")
        if warnings:
            self.security_warnings_accumulated.extend(warnings)
        
        output = [f"### {platform} Data Summary for: {xml_escape(username)}"]
        
        output.append("\n**User Profile:**")
        if profile.get("created_at"):
            created_dt = get_sort_key(profile, "created_at")
            # Ensure we don't format a min-date placeholder
            if created_dt > datetime(1970, 1, 2, tzinfo=timezone.utc):
                output.append(f"- Account Created: {created_dt.strftime('%Y-%m-%d')}")
        
        if bio_sanitized:
            output.append(f"- Bio: {bio_sanitized.strip()}")
        
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
                    info.append(f"Repo: {xml_escape(repo)}")
                
                info_str = f" ({', '.join(info)})" if info else ""
                
                # Sanitize post text
                text_snippet = post.get('text', '')[:750].strip()
                text_sanitized, warnings = sanitize_ugc_content(text_snippet, f"{platform} post {i+1}")
                if warnings:
                    self.security_warnings_accumulated.extend(warnings)
                
                output.append(f"- Item {i+1} ({ts}){info_str}:\n  Content: {text_sanitized}")

        return "\n".join(output)

    def _analyze_shared_links(self, all_user_data: List[UserData]) -> str:
        """
        Extracts all external URLs from posts, counts domain frequency, and returns a summary.

        Args:
            all_user_data: A list of all UserData objects from all targets.

        Returns:
            A formatted string summarizing the top shared domains.
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
        for domain, count in domain_counts.most_common(10):
            # XML escape domain names (defense against domains with special chars)
            output.append(f"- **{xml_escape(domain)}:** {count} link(s)")
        
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
        # Reset security warnings for this analysis
        self.security_warnings_accumulated = []
        
        # Sanitize the user query first
        sanitized_query, query_warnings = sanitize_user_query(query)
        if query_warnings:
            self.security_warnings_accumulated.extend(query_warnings)
            logger.warning(f"Query sanitization warnings: {query_warnings}")
        
        # Collect and sanitize data
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
                            # Sanitize image analysis results (they come from the vision model)
                            sanitized_analysis, warnings = sanitize_ugc_content(
                                analysis, 
                                f"image analysis for {media['url']}"
                            )
                            if warnings:
                                self.security_warnings_accumulated.extend(warnings)
                            
                            # Format with XML-escaped URL
                            media_url_escaped = xml_escape(media['url'])
                            all_media_analyses.append(
                                f"- **Image Source:** [{media_url_escaped}]({media_url_escaped})\n"
                                f"- **Analysis:**\n{sanitized_analysis}"
                            )

        if not collected_summaries and not all_media_analyses:
            return "[yellow]No data available for analysis.[/yellow]"

        # Assemble components with proper XML wrapping
        text_evidence = ""
        if collected_summaries:
            text_evidence = "\n\n---\n\n".join(collected_summaries)
        
        vision_evidence = ""
        if all_media_analyses:
            vision_evidence = "\n\n".join(sorted(list(set(all_media_analyses))))
        
        network_evidence = ""
        if shared_links_summary := self._analyze_shared_links(all_user_data_flat):
            network_evidence = shared_links_summary
        
        # Build the fully structured prompt with XML delimiting
        current_ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        system_prompt = self.system_analysis_prompt_template.format(current_timestamp=current_ts_str)
        
        # Construct user prompt with proper XML structure
        user_prompt_parts = [
            f"<user_query>{sanitized_query}</user_query>",
            ""
        ]
        
        if text_evidence:
            user_prompt_parts.append("<text_evidence>")
            user_prompt_parts.append(text_evidence)
            user_prompt_parts.append("</text_evidence>")
            user_prompt_parts.append("")
        
        if vision_evidence:
            user_prompt_parts.append("<vision_evidence>")
            user_prompt_parts.append(vision_evidence)
            user_prompt_parts.append("</vision_evidence>")
            user_prompt_parts.append("")
        
        if network_evidence:
            user_prompt_parts.append("<network_evidence>")
            user_prompt_parts.append(network_evidence)
            user_prompt_parts.append("</network_evidence>")
        
        user_prompt = "\n".join(user_prompt_parts)
        
        # Log security warnings if any were detected
        if self.security_warnings_accumulated:
            logger.warning(
                f"Security warnings during analysis: {len(self.security_warnings_accumulated)} total. "
                f"First 3: {self.security_warnings_accumulated[:3]}"
            )
        
        try:
            model = os.environ["ANALYSIS_MODEL"]
            completion = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=3500,
                temperature=0.1
            )
            
            result = completion.choices[0].message.content or ""
            
            # Check the output for injection patterns (paranoid mode)
            if injections := detect_injection_attempt(result):
                logger.warning(f"LLM output contains suspicious patterns: {injections}")
                # Don't block, but append a warning to the report
                result += f"\n\n---\n\n**Security Notice:** The analysis output contained patterns that may indicate prompt injection attempts: {injections[0]}"
            
            # Append accumulated security warnings to the report if any
            if self.security_warnings_accumulated:
                unique_warnings = list(set(self.security_warnings_accumulated))[:5]  # Top 5 unique
                result += (
                    f"\n\n---\n\n## Security Anomalies Detected\n\n"
                    f"During analysis, {len(self.security_warnings_accumulated)} potential prompt injection "
                    f"attempts were detected and neutralized. Examples:\n\n"
                )
                for warning in unique_warnings:
                    result += f"- {warning}\n"
            
            return result
            
        except APIError as e:
            logger.error(f"LLM API error during text analysis: {e}")
            raise RuntimeError(f"LLM API request failed: {e}") from e