# __init__.py
import warnings

# Adaptive Crawler
from .adaptive_crawler import (
    AdaptiveConfig,
    AdaptiveCrawler,
    CrawlState,
    CrawlStrategy,
    StatisticalStrategy,
)

# MODIFIED: Add SeedingConfig and VirtualScrollConfig here
from .async_configs import (
    BrowserConfig,
    CrawlerRunConfig,
    GeolocationConfig,
    HTTPCrawlerConfig,
    LinkPreviewConfig,
    LLMConfig,
    MatchMode,
    ProxyConfig,
    SeedingConfig,
    VirtualScrollConfig,
)
from .async_dispatcher import (
    BaseDispatcher,
    MemoryAdaptiveDispatcher,
    RateLimiter,
    SemaphoreDispatcher,
)
from .async_logger import (
    AsyncLogger,
    AsyncLoggerBase,
)

# NEW: Import AsyncUrlSeeder
from .async_url_seeder import AsyncUrlSeeder
from .async_webcrawler import AsyncWebCrawler, CacheMode

# Browser Adapters
from .browser_adapter import BrowserAdapter, PlaywrightAdapter, UndetectedAdapter
from .browser_profiler import BrowserProfiler
from .chunking_strategy import ChunkingStrategy, RegexChunking
from .components.crawler_monitor import CrawlerMonitor
from .content_filter_strategy import (
    BM25ContentFilter,
    PruningContentFilter,
    RelevantContentFilter,
)
from .content_scraping_strategy import (
    ContentScrapingStrategy,
    LXMLWebScrapingStrategy,
    WebScrapingStrategy,  # Backward compatibility alias
)
from .deep_crawling import (
    BestFirstCrawlingStrategy,
    BFSDeepCrawlStrategy,
    CompositeScorer,
    ContentTypeFilter,
    DeepCrawlDecorator,
    DeepCrawlStrategy,
    DFSDeepCrawlStrategy,
    DomainAuthorityScorer,
    DomainFilter,
    FilterChain,
    FilterStats,
    FreshnessScorer,
    KeywordRelevanceScorer,
    PathDepthScorer,
    SEOFilter,
    URLFilter,
    URLPatternFilter,
    URLScorer,
)
from .docker_client import Crawl4aiDockerClient
from .extraction_strategy import (
    CosineStrategy,
    ExtractionStrategy,
    JsonCssExtractionStrategy,
    JsonLxmlExtractionStrategy,
    JsonXPathExtractionStrategy,
    RegexExtractionStrategy,
)
from .link_preview import LinkPreview
from .markdown_generation_strategy import DefaultMarkdownGenerator
from .models import CrawlResult, DisplayMode, MarkdownGenerationResult
from .proxy_strategy import (
    ProxyRotationStrategy,
    RoundRobinProxyStrategy,
)
from .script import CompilationResult, ErrorDetail, ValidationResult

# C4A Script Language Support
from .script import compile as c4a_compile
from .script import compile_file as c4a_compile_file
from .script import validate as c4a_validate
from .table_extraction import (
    DefaultTableExtraction,
    NoTableExtraction,
    TableExtractionStrategy,
)
from .utils import setup_colab_environment, start_colab_display_server

__all__ = [
    "AsyncLoggerBase",
    "AsyncLogger",
    "AsyncWebCrawler",
    "BrowserProfiler",
    "LLMConfig",
    "GeolocationConfig",
    # NEW: Add SeedingConfig and VirtualScrollConfig
    "SeedingConfig",
    "VirtualScrollConfig",
    # NEW: Add AsyncUrlSeeder
    "AsyncUrlSeeder",
    # Adaptive Crawler
    "AdaptiveCrawler",
    "AdaptiveConfig", 
    "CrawlState",
    "CrawlStrategy",
    "StatisticalStrategy",
    "DeepCrawlStrategy",
    "BFSDeepCrawlStrategy",
    "BestFirstCrawlingStrategy",
    "DFSDeepCrawlStrategy",
    "FilterChain",
    "URLPatternFilter",
    "ContentTypeFilter",
    "DomainFilter",
    "FilterStats",
    "URLFilter",
    "SEOFilter",
    "KeywordRelevanceScorer",
    "URLScorer",
    "CompositeScorer",
    "DomainAuthorityScorer",
    "FreshnessScorer",
    "PathDepthScorer",
    "DeepCrawlDecorator",
    "CrawlResult",
    "CacheMode",
    "MatchMode",
    "ContentScrapingStrategy",
    "WebScrapingStrategy",
    "LXMLWebScrapingStrategy",
    "BrowserConfig",
    "CrawlerRunConfig",
    "HTTPCrawlerConfig",
    "ExtractionStrategy",
    "CosineStrategy",
    "JsonCssExtractionStrategy",
    "JsonXPathExtractionStrategy",
    "JsonLxmlExtractionStrategy",
    "RegexExtractionStrategy",
    "ChunkingStrategy",
    "RegexChunking",
    "DefaultMarkdownGenerator",
    "TableExtractionStrategy",
    "DefaultTableExtraction",
    "NoTableExtraction",
    "RelevantContentFilter",
    "PruningContentFilter",
    "BM25ContentFilter",
    "BaseDispatcher",
    "MemoryAdaptiveDispatcher",
    "SemaphoreDispatcher",
    "RateLimiter",
    "CrawlerMonitor",
    "LinkPreview",
    "DisplayMode",
    "MarkdownGenerationResult",
    "Crawl4aiDockerClient",
    "ProxyRotationStrategy",
    "RoundRobinProxyStrategy",
    "ProxyConfig",
    "start_colab_display_server",
    "setup_colab_environment",
    # C4A Script additions
    "c4a_compile",
    "c4a_validate", 
    "c4a_compile_file",
    "CompilationResult",
    "ValidationResult",
    "ErrorDetail",
    # Browser Adapters
    "BrowserAdapter",
    "PlaywrightAdapter", 
    "UndetectedAdapter",
    "LinkPreviewConfig"
]



# Disable all Pydantic warnings
warnings.filterwarnings("ignore", module="pydantic")