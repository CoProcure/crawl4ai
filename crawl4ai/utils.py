import linecache
import os
import platform
import re
import subprocess
import textwrap
import traceback
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import aiohttp
import lxml.html
import psutil
from protego import Protego

from crawl4ai.cache_client import (
    DEFAULT_CACHE_TTL_SECONDS,
    ROBOTS_CACHE_KEY_PREFIX,
    CacheClient,
)


class RobotsParser:
    def __init__(self, cache_client: CacheClient, cache_ttl=None):
        self.cache_client = cache_client
        self.cache_ttl = cache_ttl or DEFAULT_CACHE_TTL_SECONDS

    def _get_cache_key(self, domain: str) -> str:
        return f"{ROBOTS_CACHE_KEY_PREFIX}{domain}"

    def _get_cached_rules(self, domain: str) -> str:
        return self.cache_client.get(key=self._get_cache_key(domain))

    def _cache_rules(self, domain: str, content: str):
        self.cache_client.set(
            key=self._get_cache_key(domain), value=content, ttl_seconds=self.cache_ttl
        )

    async def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        """
        Check if URL can be fetched according to robots.txt rules.

        Args:
            url: The URL to check
            user_agent: User agent string to check against (default: "*")

        Returns:
            bool: True if allowed, False if disallowed by robots.txt
        """
        # Handle empty/invalid URLs
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            if not domain:
                return True
        except Exception as _ex:
            return True

        # Fast path - check cache first
        rules = self._get_cached_rules(domain)

        # If rules not found or stale, fetch new ones
        if not rules:
            try:
                # Ensure we use the same scheme as the input URL
                scheme = parsed.scheme or "http"
                robots_url = f"{scheme}://{domain}/robots.txt"

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        robots_url, timeout=2, ssl=False
                    ) as response:
                        if response.status == 200:
                            rules = await response.text()
                            self._cache_rules(domain, rules)
                        else:
                            return True
            except Exception as _ex:
                # On any error (timeout, connection failed, etc), allow access
                return True

        # Create parser for this check
        parser = Protego.parse(rules)
        return parser.can_fetch(url, user_agent)


def create_box_message(
    message: str,
    type: str = "info",
    width: int = 120,
    add_newlines: bool = True,
    double_line: bool = False,
) -> str:
    """
    Create a styled message box with colored borders and formatted text.

    How it works:
    1. Determines box style and colors based on the message type (e.g., info, warning).
    2. Wraps text to fit within the specified width.
    3. Constructs a box using characters (single or double lines) with appropriate formatting.
    4. Adds optional newlines before and after the box.

    Args:
        message (str): The message to display inside the box.
        type (str): Type of the message (e.g., "info", "warning", "error", "success"). Defaults to "info".
        width (int): Width of the box. Defaults to 120.
        add_newlines (bool): Whether to add newlines before and after the box. Defaults to True.
        double_line (bool): Whether to use double lines for the box border. Defaults to False.

    Returns:
        str: A formatted string containing the styled message box.
    """

    # Define border and text colors for different types
    styles = {
        "warning": ("yellow", "bright_yellow", "⚠"),
        "info": ("blue", "bright_blue", "ℹ"),
        "debug": ("lightblack", "bright_black", "⋯"),
        "success": ("green", "bright_green", "✓"),
        "error": ("red", "bright_red", "×"),
    }

    border_color, text_color, prefix = styles.get(type.lower(), styles["info"])

    # Define box characters based on line style
    box_chars = {
        "single": ("─", "│", "┌", "┐", "└", "┘"),
        "double": ("═", "║", "╔", "╗", "╚", "╝"),
    }
    line_style = "double" if double_line else "single"
    h_line, v_line, tl, tr, bl, br = box_chars[line_style]

    # Process lines with lighter text color
    formatted_lines = []
    raw_lines = message.split("\n")

    if raw_lines:
        first_line = f"{prefix} {raw_lines[0].strip()}"
        wrapped_first = textwrap.fill(first_line, width=width - 4)
        formatted_lines.extend(wrapped_first.split("\n"))

        for line in raw_lines[1:]:
            if line.strip():
                wrapped = textwrap.fill(f"  {line.strip()}", width=width - 4)
                formatted_lines.extend(wrapped.split("\n"))
            else:
                formatted_lines.append("")

    # Create the box with colored borders and lighter text
    horizontal_line = h_line * (width - 1)
    box = [
        f"[{border_color}]{tl}{horizontal_line}{tr}[/{border_color}]",
        *[
            f"[{border_color}]{v_line}[{text_color}] {line:<{width - 2}}[/{text_color}][{border_color}]{v_line}[/{border_color}]"
            for line in formatted_lines
        ],
        f"[{border_color}]{bl}{horizontal_line}{br}[/{border_color}]",
    ]

    result = "\n".join(box)
    if add_newlines:
        result = f"\n{result}\n"

    return result


def sanitize_input_encode(text: str | None) -> str:
    """Sanitize input to handle potential encoding issues."""
    try:
        try:
            if not text:
                return ""
            # Attempt to encode and decode as UTF-8 to handle potential encoding issues
            return text.encode("utf-8", errors="ignore").decode("utf-8")
        except UnicodeEncodeError as e:
            print(
                f"Warning: Encoding issue detected. Some characters may be lost. Error: {e}"
            )
            # Fall back to ASCII if UTF-8 fails
            return text.encode("ascii", errors="ignore").decode("ascii")
    except Exception as e:
        raise ValueError(f"Error sanitizing input: {str(e)}") from e


def extract_metadata_using_lxml(html, doc=None):
    """
    Extract metadata from HTML using lxml for better performance.
    """
    metadata = {}

    if not html and doc is None:
        return {}

    if doc is None:
        try:
            doc = lxml.html.document_fromstring(html)
        except Exception:
            return {}

    # Use XPath to find head element
    head = doc.xpath("//head")
    if not head:
        return metadata

    head = head[0]

    # Title - using XPath
    # title = head.xpath(".//title/text()")
    # metadata["title"] = title[0].strip() if title else None

    # === Title Extraction - New Approach ===
    # Attempt to extract <title> using XPath
    title = head.xpath(".//title/text()")
    title = title[0] if title else None

    # Fallback: Use .find() in case XPath fails due to malformed HTML
    if not title:
        title_el = doc.find(".//title")
        title = title_el.text if title_el is not None else None

    # Final fallback: Use OpenGraph or Twitter title if <title> is missing or empty
    if not title:
        title_candidates = doc.xpath(
            "//meta[@property='og:title']/@content"
        ) or doc.xpath("//meta[@name='twitter:title']/@content")
        title = title_candidates[0] if title_candidates else None

    # Strip and assign title
    metadata["title"] = title.strip() if title else None

    # Meta description - using XPath with multiple attribute conditions
    description = head.xpath('.//meta[@name="description"]/@content')
    metadata["description"] = description[0].strip() if description else None

    # Meta keywords
    keywords = head.xpath('.//meta[@name="keywords"]/@content')
    metadata["keywords"] = keywords[0].strip() if keywords else None

    # Meta author
    author = head.xpath('.//meta[@name="author"]/@content')
    metadata["author"] = author[0].strip() if author else None

    # Open Graph metadata - using starts-with() for performance
    og_tags = head.xpath('.//meta[starts-with(@property, "og:")]')
    for tag in og_tags:
        property_name = tag.get("property", "").strip()
        content = tag.get("content", "").strip()
        if property_name and content:
            metadata[property_name] = content

    # Twitter Card metadata
    twitter_tags = head.xpath('.//meta[starts-with(@name, "twitter:")]')
    for tag in twitter_tags:
        property_name = tag.get("name", "").strip()
        content = tag.get("content", "").strip()
        if property_name and content:
            metadata[property_name] = content

    # Article metadata
    article_tags = head.xpath('.//meta[starts-with(@property, "article:")]')
    for tag in article_tags:
        property_name = tag.get("property", "").strip()
        content = tag.get("content", "").strip()
        if property_name and content:
            metadata[property_name] = content

    return metadata


def normalize_url(
    href: str,
    base_url: str,
):
    """
    Extended URL normalizer

    Parameters
    ----------
    href : str
        The raw link extracted from a page.
    base_url : str
        The page’s canonical URL (used to resolve relative links).

    Returns
    -------
    str | None
        A clean, canonical URL or None if href is empty/None.
    """
    if not href:
        return None

    # Resolve relative paths first
    full_url = urljoin(base_url, href.strip())

    # Parse once, edit parts, then rebuild
    parsed = urlparse(full_url)

    # ── netloc ──
    netloc = parsed.netloc.lower()

    # ── path ──
    # Strip duplicate slashes and trailing “/” (except root)
    path = quote(unquote(parsed.path))
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")

    # ── query ──
    query = parsed.query
    if query:
        params = [(k.lower(), v) for k, v in parse_qsl(query, keep_blank_values=True)]
        query = urlencode(params, doseq=True) if params else ""

    # Re-assemble
    return urlunparse((parsed.scheme, netloc, path, parsed.params, query, ""))


def get_base_domain(url: str) -> str:
    """
    Extract the base domain from a given URL, handling common edge cases.

    How it works:
    1. Parses the URL to extract the domain.
    2. Removes the port number and 'www' prefix.
    3. Handles special domains (e.g., 'co.uk') to extract the correct base.

    Args:
        url (str): The URL to extract the base domain from.

    Returns:
        str: The extracted base domain or an empty string if parsing fails.
    """
    try:
        # Get domain from URL
        domain = urlparse(url).netloc.lower()
        if not domain:
            return ""

        # Remove port if present
        domain = domain.split(":")[0]

        # Remove www
        domain = re.sub(r"^www\.", "", domain)

        # Extract last two parts of domain (handles co.uk etc)
        parts = domain.split(".")
        if len(parts) > 2 and parts[-2] in {
            "co",
            "com",
            "org",
            "gov",
            "edu",
            "net",
            "mil",
            "int",
            "ac",
            "ad",
            "ae",
            "af",
            "ag",
        }:
            return ".".join(parts[-3:])

        return ".".join(parts[-2:])
    except Exception:
        return ""


def is_external_url(url: str, base_domain: str) -> bool:
    """
    Extract the base domain from a given URL, handling common edge cases.

    How it works:
    1. Parses the URL to extract the domain.
    2. Removes the port number and 'www' prefix.
    3. Handles special domains (e.g., 'co.uk') to extract the correct base.

    Args:
        url (str): The URL to extract the base domain from.

    Returns:
        str: The extracted base domain or an empty string if parsing fails.
    """
    special = {"mailto:", "tel:", "ftp:", "file:", "data:", "javascript:"}
    if any(url.lower().startswith(p) for p in special):
        return True

    try:
        parsed = urlparse(url)
        if not parsed.netloc:  # Relative URL
            return False

        # Strip 'www.' from both domains for comparison
        url_domain = parsed.netloc.lower().replace("www.", "")
        base = base_domain.lower().replace("www.", "")

        # Check if URL domain ends with base domain
        return not url_domain.endswith(base)
    except Exception:
        return False


def get_error_context(exc_info, context_lines: int = 5):
    """
    Extract error context with more reliable line number tracking.

    Args:
        exc_info: The exception info from sys.exc_info()
        context_lines: Number of lines to show before and after the error

    Returns:
        dict: Error context information
    """
    # Get the full traceback
    tb = traceback.extract_tb(exc_info[2])

    # Get the last frame (where the error occurred)
    last_frame = tb[-1]
    filename = last_frame.filename
    line_no = last_frame.lineno
    func_name = last_frame.name

    # Get the source code context using linecache
    # This is more reliable than inspect.getsourcelines
    context_start = max(1, line_no - context_lines)
    context_end = line_no + context_lines + 1

    # Build the context lines with line numbers
    context_lines = []
    for i in range(context_start, context_end):
        line = linecache.getline(filename, i)
        if line:
            # Remove any trailing whitespace/newlines and add the pointer for error line
            line = line.rstrip()
            pointer = "→" if i == line_no else " "
            context_lines.append(f"{i:4d} {pointer} {line}")

    # Join the lines with newlines
    code_context = "\n".join(context_lines)

    # Get relative path for cleaner output
    try:
        rel_path = os.path.relpath(filename)
    except ValueError:
        # Fallback if relpath fails (can happen on Windows with different drives)
        rel_path = filename

    return {
        "filename": rel_path,
        "line_no": line_no,
        "function": func_name,
        "code_context": code_context,
    }


def extract_page_context(
    page_title: str, headlines_text: str, meta_description: str, base_url: str
) -> dict:
    """
    Extract page context for link scoring - called ONCE per page for performance.
    Parser-agnostic function that takes pre-extracted data.

    Args:
        page_title: Title of the page
        headlines_text: Combined text from h1, h2, h3 elements
        meta_description: Meta description content
        base_url: Base URL of the page

    Returns:
        Dictionary containing page context data for fast link scoring
    """
    context = {
        "terms": set(),
        "headlines": headlines_text or "",
        "meta_description": meta_description or "",
        "domain": "",
        "is_docs_site": False,
    }

    try:
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        context["domain"] = parsed.netloc.lower()

        # Check if this is a documentation/reference site
        context["is_docs_site"] = any(
            indicator in context["domain"]
            for indicator in ["docs.", "api.", "developer.", "reference."]
        )

        # Create term set for fast intersection (performance optimization)
        all_text = (
            (page_title or "")
            + " "
            + context["headlines"]
            + " "
            + context["meta_description"]
        ).lower()
        # Simple tokenization - fast and sufficient for scoring
        context["terms"] = set(
            word.strip('.,!?;:"()[]{}')
            for word in all_text.split()
            if len(word.strip('.,!?;:"()[]{}')) > 2
        )

    except Exception:
        # Fail gracefully - return empty context
        pass

    return context


def calculate_link_intrinsic_score(
    link_text: str,
    url: str,
    title_attr: str,
    class_attr: str,
    rel_attr: str,
    page_context: dict,
) -> float:
    """
    Ultra-fast link quality scoring using only provided data (no DOM access needed).
    Parser-agnostic function.

    Args:
        link_text: Text content of the link
        url: Link URL
        title_attr: Title attribute of the link
        class_attr: Class attribute of the link
        rel_attr: Rel attribute of the link
        page_context: Pre-computed page context from extract_page_context()

    Returns:
        Quality score (0.0 - 10.0), higher is better
    """
    score = 0.0

    try:
        # 1. ATTRIBUTE QUALITY (string analysis - very fast)
        if title_attr and len(title_attr.strip()) > 3:
            score += 1.0

        class_str = (class_attr or "").lower()
        # Navigation/important classes boost score
        if any(
            nav_class in class_str
            for nav_class in ["nav", "menu", "primary", "main", "important"]
        ):
            score += 1.5
        # Marketing/ad classes reduce score
        if any(
            bad_class in class_str
            for bad_class in ["ad", "sponsor", "track", "promo", "banner"]
        ):
            score -= 1.0

        rel_str = (rel_attr or "").lower()
        # Semantic rel values
        if any(
            good_rel in rel_str for good_rel in ["canonical", "next", "prev", "chapter"]
        ):
            score += 1.0
        if any(bad_rel in rel_str for bad_rel in ["nofollow", "sponsored", "ugc"]):
            score -= 0.5

        # 2. URL STRUCTURE QUALITY (string operations - very fast)
        url_lower = url.lower()

        # High-value path patterns
        if any(
            good_path in url_lower
            for good_path in [
                "/docs/",
                "/api/",
                "/guide/",
                "/tutorial/",
                "/reference/",
                "/manual/",
            ]
        ):
            score += 2.0
        elif any(
            medium_path in url_lower
            for medium_path in ["/blog/", "/article/", "/post/", "/news/"]
        ):
            score += 1.0

        # Penalize certain patterns
        if any(
            bad_path in url_lower
            for bad_path in [
                "/admin/",
                "/login/",
                "/cart/",
                "/checkout/",
                "/track/",
                "/click/",
            ]
        ):
            score -= 1.5

        # URL depth (shallow URLs often more important)
        url_depth = url.count("/") - 2  # Subtract protocol and domain
        if url_depth <= 2:
            score += 1.0
        elif url_depth > 5:
            score -= 0.5

        # HTTPS bonus
        if url.startswith("https://"):
            score += 0.5

        # 3. TEXT QUALITY (string analysis - very fast)
        if link_text:
            text_clean = link_text.strip()
            if len(text_clean) > 3:
                score += 1.0

            # Multi-word links are usually more descriptive
            word_count = len(text_clean.split())
            if word_count >= 2:
                score += 0.5
            if word_count >= 4:
                score += 0.5

            # Avoid generic link text
            generic_texts = ["click here", "read more", "more info", "link", "here"]
            if text_clean.lower() in generic_texts:
                score -= 1.0

        # 4. CONTEXTUAL RELEVANCE (pre-computed page terms - very fast)
        if page_context.get("terms") and link_text:
            link_words = set(
                word.strip('.,!?;:"()[]{}').lower()
                for word in link_text.split()
                if len(word.strip('.,!?;:"()[]{}')) > 2
            )

            if link_words:
                # Calculate word overlap ratio
                overlap = len(link_words & page_context["terms"])
                if overlap > 0:
                    relevance_ratio = overlap / min(
                        len(link_words), 10
                    )  # Cap to avoid over-weighting
                    score += relevance_ratio * 2.0  # Up to 2 points for relevance

        # 5. DOMAIN CONTEXT BONUSES (very fast string checks)
        if page_context.get("is_docs_site", False):
            # Documentation sites: prioritize internal navigation
            if link_text and any(
                doc_keyword in link_text.lower()
                for doc_keyword in ["api", "reference", "guide", "tutorial", "example"]
            ):
                score += 1.0

    except Exception:
        # Fail gracefully - return minimal score
        score = 0.5

    # Ensure score is within reasonable bounds
    return max(0.0, min(score, 10.0))


def calculate_total_score(
    intrinsic_score: float | None = None,
    score_links_enabled: bool = False,
) -> float:
    if not score_links_enabled:
        return 5.0

    intrinsic = intrinsic_score if intrinsic_score is not None else 0.0
    return max(0.0, min(intrinsic, 10.0))


def get_true_available_memory_gb() -> float:
    """Get truly available memory including inactive pages (cross-platform)"""
    vm = psutil.virtual_memory()

    if platform.system() == "Darwin":  # macOS
        # On macOS, we need to include inactive memory too
        try:
            # Use vm_stat to get accurate values
            result = subprocess.run(["vm_stat"], capture_output=True, text=True)
            lines = result.stdout.split("\n")

            page_size = 16384  # macOS page size
            pages = {}

            for line in lines:
                if "Pages free:" in line:
                    pages["free"] = int(line.split()[-1].rstrip("."))
                elif "Pages inactive:" in line:
                    pages["inactive"] = int(line.split()[-1].rstrip("."))
                elif "Pages speculative:" in line:
                    pages["speculative"] = int(line.split()[-1].rstrip("."))
                elif "Pages purgeable:" in line:
                    pages["purgeable"] = int(line.split()[-1].rstrip("."))

            # Calculate total available (free + inactive + speculative + purgeable)
            total_available_pages = (
                pages.get("free", 0)
                + pages.get("inactive", 0)
                + pages.get("speculative", 0)
                + pages.get("purgeable", 0)
            )
            available_gb = (total_available_pages * page_size) / (1024**3)

            return available_gb
        except:
            # Fallback to psutil
            return vm.available / (1024**3)
    else:
        # For Windows and Linux, psutil.available is accurate
        return vm.available / (1024**3)


def get_true_memory_usage_percent() -> float:
    """
    Get memory usage percentage that accounts for platform differences.

    Returns:
        float: Memory usage percentage (0-100)
    """
    vm = psutil.virtual_memory()
    total_gb = vm.total / (1024**3)
    available_gb = get_true_available_memory_gb()

    # Calculate used percentage based on truly available memory
    used_percent = 100.0 * (total_gb - available_gb) / total_gb

    # Ensure it's within valid range
    return max(0.0, min(100.0, used_percent))
