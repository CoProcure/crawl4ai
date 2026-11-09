import re
from typing import Any

from crawl4ai.cache_client import DEFAULT_CACHE_TTL_SECONDS, CacheClient, NoCacheClient

from .cache_context import CacheMode
from .config import (
    IMAGE_DESCRIPTION_MIN_WORD_THRESHOLD,
    IMAGE_SCORE_THRESHOLD,
    MIN_WORD_THRESHOLD,
    PAGE_TIMEOUT,
    SOCIAL_MEDIA_DOMAINS,
)
from .content_scraping_strategy import ContentScrapingStrategy, LXMLWebScrapingStrategy
from .markdown_generation_strategy import (
    DefaultMarkdownGenerator,
    MarkdownGenerationStrategy,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/116.0.0.0 Safari/537.36"
)


def generate_client_hints(user_agent: str) -> str:
    patterns = {
        "chrome": r"Chrome/(\d+)",
        "edge": r"Edg/(\d+)",
        "safari": r"Version/(\d+)",
        "firefox": r"Firefox/(\d+)",
    }
    versions = {}
    for browser, pattern in patterns.items():
        match = re.search(pattern, user_agent)
        if match:
            versions[browser] = match.group(1)

    hints = []
    if "chrome" in versions:
        hints.append(f'"Chromium";v="{versions["chrome"]}"')
        hints.append('"Not_A Brand";v="8"')
        if "edge" in versions:
            hints.append(f'"Microsoft Edge";v="{versions["edge"]}"')
        else:
            hints.append(f'"Google Chrome";v="{versions["chrome"]}"')
    elif "firefox" in versions:
        return '""'
    elif "safari" in versions:
        hints.append(f'"Safari";v="{versions["safari"]}"')
        hints.append('"Not_A Brand";v="8"')

    return ", ".join(hints)


class ProxyConfig:
    def __init__(
        self,
        server: str,
        username: str | None = None,
        password: str | None = None,
    ):
        self.server = server
        self.username = username
        self.password = password

    @staticmethod
    def from_string(proxy_str: str) -> "ProxyConfig":
        parts = proxy_str.split(":")
        if len(parts) == 4:
            ip, port, username, password = parts
            return ProxyConfig(
                server=f"http://{ip}:{port}", username=username, password=password
            )
        if len(parts) == 2:
            ip, port = parts
            return ProxyConfig(server=f"http://{ip}:{port}")
        raise ValueError(f"Invalid proxy string format: {proxy_str}")

    @staticmethod
    def from_dict(proxy_dict: dict) -> "ProxyConfig":
        return ProxyConfig(
            server=proxy_dict.get("server"),
            username=proxy_dict.get("username"),
            password=proxy_dict.get("password"),
        )


def _coerce_proxy_config(proxy_config: "ProxyConfig | dict | str | None"):
    if isinstance(proxy_config, dict):
        return ProxyConfig.from_dict(proxy_config)
    if isinstance(proxy_config, str):
        return ProxyConfig.from_string(proxy_config)
    return proxy_config


class BrowserConfig:
    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = True,
        chrome_channel: str = "chromium",
        proxy: str = None,
        proxy_config: ProxyConfig | dict | None = None,
        viewport_width: int = 1080,
        viewport_height: int = 600,
        viewport: dict = None,
        ignore_https_errors: bool = True,
        java_script_enabled: bool = True,
        sleep_on_close: bool = False,
        verbose: bool = True,
        cookies: list = None,
        headers: dict = None,
        user_agent: str = DEFAULT_USER_AGENT,
        extra_args: list = None,
    ):
        self.browser_type = browser_type
        self.headless = headless
        self.chrome_channel = chrome_channel or self.browser_type or "chromium"
        if self.browser_type in ["firefox", "webkit"]:
            self.chrome_channel = ""
        self.proxy = proxy
        self.proxy_config = _coerce_proxy_config(proxy_config)

        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.viewport = viewport
        if self.viewport is not None:
            self.viewport_width = self.viewport.get("width", 1080)
            self.viewport_height = self.viewport.get("height", 600)
        self.ignore_https_errors = ignore_https_errors
        self.java_script_enabled = java_script_enabled
        self.cookies = cookies if cookies is not None else []
        self.headers = headers if headers is not None else {}
        self.user_agent = user_agent
        self.extra_args = extra_args if extra_args is not None else []
        self.sleep_on_close = sleep_on_close
        self.verbose = verbose

        self.browser_hint = generate_client_hints(self.user_agent)
        self.headers.setdefault("sec-ch-ua", self.browser_hint)

    @staticmethod
    def from_kwargs(kwargs: dict) -> "BrowserConfig":
        return BrowserConfig(
            browser_type=kwargs.get("browser_type", "chromium"),
            headless=kwargs.get("headless", True),
            chrome_channel=kwargs.get("chrome_channel", "chromium"),
            proxy=kwargs.get("proxy"),
            proxy_config=kwargs.get("proxy_config", None),
            viewport_width=kwargs.get("viewport_width", 1080),
            viewport_height=kwargs.get("viewport_height", 600),
            ignore_https_errors=kwargs.get("ignore_https_errors", True),
            java_script_enabled=kwargs.get("java_script_enabled", True),
            cookies=kwargs.get("cookies", []),
            headers=kwargs.get("headers", {}),
            user_agent=kwargs.get(
                "user_agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            ),
            extra_args=kwargs.get("extra_args", []),
        )

    def to_dict(self):
        return {
            "browser_type": self.browser_type,
            "headless": self.headless,
            "chrome_channel": self.chrome_channel,
            "proxy": self.proxy,
            "proxy_config": self.proxy_config,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "ignore_https_errors": self.ignore_https_errors,
            "java_script_enabled": self.java_script_enabled,
            "cookies": self.cookies,
            "headers": self.headers,
            "user_agent": self.user_agent,
            "extra_args": self.extra_args,
            "sleep_on_close": self.sleep_on_close,
            "verbose": self.verbose,
        }

    def clone(self, **kwargs):
        config_dict = self.to_dict()
        config_dict.update(kwargs)
        return BrowserConfig.from_kwargs(config_dict)


class LinkPreviewConfig:
    def __init__(
        self,
        include_internal: bool = True,
        include_external: bool = False,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        concurrency: int = 10,
        timeout: int = 5,
        max_links: int = 100,
        score_threshold: float | None = None,
        verbose: bool = False,
    ):
        self.include_internal = include_internal
        self.include_external = include_external
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns
        self.concurrency = concurrency
        self.timeout = timeout
        self.max_links = max_links
        self.score_threshold = score_threshold
        self.verbose = verbose

        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_links <= 0:
            raise ValueError("max_links must be positive")
        if score_threshold is not None and not (0.0 <= score_threshold <= 1.0):
            raise ValueError("score_threshold must be between 0.0 and 1.0")
        if not include_internal and not include_external:
            raise ValueError("At least one of include_internal or include_external must be True")

    @staticmethod
    def from_dict(config_dict: dict[str, Any]) -> "LinkPreviewConfig":
        if not config_dict:
            return None

        return LinkPreviewConfig(
            include_internal=config_dict.get("include_internal", True),
            include_external=config_dict.get("include_external", False),
            include_patterns=config_dict.get("include_patterns"),
            exclude_patterns=config_dict.get("exclude_patterns"),
            concurrency=config_dict.get("concurrency", 10),
            timeout=config_dict.get("timeout", 5),
            max_links=config_dict.get("max_links", 100),
            score_threshold=config_dict.get("score_threshold"),
            verbose=config_dict.get("verbose", False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "include_internal": self.include_internal,
            "include_external": self.include_external,
            "include_patterns": self.include_patterns,
            "exclude_patterns": self.exclude_patterns,
            "concurrency": self.concurrency,
            "timeout": self.timeout,
            "max_links": self.max_links,
            "score_threshold": self.score_threshold,
            "verbose": self.verbose,
        }

    def clone(self, **kwargs) -> "LinkPreviewConfig":
        config_dict = self.to_dict()
        config_dict.update(kwargs)
        return LinkPreviewConfig.from_dict(config_dict)


class CrawlerRunConfig:
    def __init__(
        self,
        word_count_threshold: int = MIN_WORD_THRESHOLD,
        markdown_generator: MarkdownGenerationStrategy = DefaultMarkdownGenerator(),
        excluded_tags: list = None,
        keep_data_attributes: bool = False,
        remove_forms: bool = False,
        scraping_strategy: ContentScrapingStrategy = None,
        proxy_config: ProxyConfig | dict | None = None,
        locale: str | None = None,
        timezone_id: str | None = None,
        cache_mode: CacheMode = CacheMode.BYPASS,
        cache_client: CacheClient = NoCacheClient(),
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        wait_until: str = "domcontentloaded",
        page_timeout: int = PAGE_TIMEOUT,
        delay_before_return_html: float = 0.1,
        ignore_body_visibility: bool = True,
        image_description_min_word_threshold: int = IMAGE_DESCRIPTION_MIN_WORD_THRESHOLD,
        image_score_threshold: int = IMAGE_SCORE_THRESHOLD,
        exclude_external_images: bool = False,
        exclude_all_images: bool = False,
        exclude_social_media_domains: list = None,
        exclude_external_links: bool = False,
        exclude_social_media_links: bool = False,
        exclude_domains: list = None,
        exclude_internal_links: bool = False,
        score_links: bool = False,
        verbose: bool = True,
        url: str = None,
        check_robots_txt: bool = False,
        user_agent: str = None,
        link_preview_config: LinkPreviewConfig | dict[str, Any] = None,
    ):
        self.url = url

        self.word_count_threshold = word_count_threshold
        self.markdown_generator = markdown_generator
        self.excluded_tags = excluded_tags or []
        self.keep_data_attributes = keep_data_attributes
        self.remove_forms = remove_forms
        self.scraping_strategy = scraping_strategy or LXMLWebScrapingStrategy(cache_client=cache_client)
        self.proxy_config = _coerce_proxy_config(proxy_config)

        self.locale = locale
        self.timezone_id = timezone_id

        self.cache_mode = cache_mode
        self.cache_client = cache_client
        self.cache_ttl_seconds = cache_ttl_seconds

        self.wait_until = wait_until
        self.page_timeout = page_timeout
        self.delay_before_return_html = delay_before_return_html
        self.ignore_body_visibility = ignore_body_visibility

        self.image_description_min_word_threshold = image_description_min_word_threshold
        self.image_score_threshold = image_score_threshold
        self.exclude_external_images = exclude_external_images
        self.exclude_all_images = exclude_all_images

        self.exclude_social_media_domains = (
            exclude_social_media_domains or SOCIAL_MEDIA_DOMAINS
        )
        self.exclude_external_links = exclude_external_links
        self.exclude_social_media_links = exclude_social_media_links
        self.exclude_domains = exclude_domains or []
        self.exclude_internal_links = exclude_internal_links
        self.score_links = score_links

        self.verbose = verbose

        self.check_robots_txt = check_robots_txt
        self.user_agent = user_agent

        if link_preview_config is None:
            self.link_preview_config = None
        elif isinstance(link_preview_config, LinkPreviewConfig):
            self.link_preview_config = link_preview_config
        elif isinstance(link_preview_config, dict):
            self.link_preview_config = LinkPreviewConfig.from_dict(link_preview_config)
        else:
            raise ValueError("link_preview_config must be LinkPreviewConfig object or dict")

    def __getattr__(self, name):
        raise AttributeError(f"'{self.__class__.__name__}' has no attribute '{name}'")

    @staticmethod
    def from_kwargs(kwargs: dict) -> "CrawlerRunConfig":
        return CrawlerRunConfig(
            word_count_threshold=kwargs.get("word_count_threshold", 200),
            markdown_generator=kwargs.get("markdown_generator"),
            excluded_tags=kwargs.get("excluded_tags", []),
            keep_data_attributes=kwargs.get("keep_data_attributes", False),
            remove_forms=kwargs.get("remove_forms", False),
            scraping_strategy=kwargs.get("scraping_strategy"),
            proxy_config=kwargs.get("proxy_config"),
            locale=kwargs.get("locale", None),
            timezone_id=kwargs.get("timezone_id", None),
            cache_mode=kwargs.get("cache_mode", CacheMode.BYPASS),
            cache_client=kwargs.get("cache_client", None),
            cache_ttl_seconds=kwargs.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS),
            wait_until=kwargs.get("wait_until", "domcontentloaded"),
            page_timeout=kwargs.get("page_timeout", 60000),
            delay_before_return_html=kwargs.get("delay_before_return_html", 0.1),
            ignore_body_visibility=kwargs.get("ignore_body_visibility", True),
            image_description_min_word_threshold=kwargs.get(
                "image_description_min_word_threshold",
                IMAGE_DESCRIPTION_MIN_WORD_THRESHOLD,
            ),
            image_score_threshold=kwargs.get(
                "image_score_threshold", IMAGE_SCORE_THRESHOLD
            ),
            exclude_all_images=kwargs.get("exclude_all_images", False),
            exclude_external_images=kwargs.get("exclude_external_images", False),
            exclude_social_media_domains=kwargs.get(
                "exclude_social_media_domains", SOCIAL_MEDIA_DOMAINS
            ),
            exclude_external_links=kwargs.get("exclude_external_links", False),
            exclude_social_media_links=kwargs.get("exclude_social_media_links", False),
            exclude_domains=kwargs.get("exclude_domains", []),
            exclude_internal_links=kwargs.get("exclude_internal_links", False),
            score_links=kwargs.get("score_links", False),
            verbose=kwargs.get("verbose", True),
            check_robots_txt=kwargs.get("check_robots_txt", False),
            user_agent=kwargs.get("user_agent"),
            link_preview_config=kwargs.get("link_preview_config"),
            url=kwargs.get("url"),
        )

    def to_dict(self):
        return {
            "word_count_threshold": self.word_count_threshold,
            "markdown_generator": self.markdown_generator,
            "excluded_tags": self.excluded_tags,
            "keep_data_attributes": self.keep_data_attributes,
            "remove_forms": self.remove_forms,
            "scraping_strategy": self.scraping_strategy,
            "proxy_config": self.proxy_config,
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "cache_mode": self.cache_mode,
            "cache_client": self.cache_client,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "wait_until": self.wait_until,
            "page_timeout": self.page_timeout,
            "delay_before_return_html": self.delay_before_return_html,
            "ignore_body_visibility": self.ignore_body_visibility,
            "image_description_min_word_threshold": self.image_description_min_word_threshold,
            "image_score_threshold": self.image_score_threshold,
            "exclude_all_images": self.exclude_all_images,
            "exclude_external_images": self.exclude_external_images,
            "exclude_social_media_domains": self.exclude_social_media_domains,
            "exclude_external_links": self.exclude_external_links,
            "exclude_social_media_links": self.exclude_social_media_links,
            "exclude_domains": self.exclude_domains,
            "exclude_internal_links": self.exclude_internal_links,
            "score_links": self.score_links,
            "verbose": self.verbose,
            "check_robots_txt": self.check_robots_txt,
            "user_agent": self.user_agent,
            "link_preview_config": self.link_preview_config.to_dict() if self.link_preview_config else None,
            "url": self.url,
        }

    def clone(self, **kwargs):
        config_dict = self.to_dict()
        config_dict.update(kwargs)
        return CrawlerRunConfig.from_kwargs(config_dict)


class SeedingConfig:
    def __init__(
        self,
        source: str = "sitemap+cc",
        pattern: str | None = "*",
        live_check: bool = False,
        extract_head: bool = False,
        max_urls: int = -1,
        concurrency: int = 1000,
        hits_per_sec: int = 5,
        force: bool = False,
        base_directory: str | None = None,
        verbose: bool | None = None,
        query: str | None = None,
        score_threshold: float | None = None,
        scoring_method: str = "bm25",
        filter_nonsense_urls: bool = True,
    ):
        self.source = source
        self.pattern = pattern
        self.live_check = live_check
        self.extract_head = extract_head
        self.max_urls = max_urls
        self.concurrency = concurrency
        self.hits_per_sec = hits_per_sec
        self.force = force
        self.base_directory = base_directory
        self.verbose = verbose
        self.query = query
        self.score_threshold = score_threshold
        self.scoring_method = scoring_method
        self.filter_nonsense_urls = filter_nonsense_urls
