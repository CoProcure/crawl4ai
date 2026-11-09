---
title: Crawl4ai prune
status: final
tags: [crawl4ai, dependencies]
created: 2026-11-09
updated: 2026-11-09
---
# crawl4ai fork pruning map (for gazebo/Pavilion)

Method: read the code, grepped gazebo (`git grep`), and ran a real crawl with gazebo's exact config against a local HTTP server under `sys.setprofile`
(`scratchpad/trace_run.py`, results in `scratchpad/called.json`). The trace used `check_robots_txt=True, cache_mode=ENABLED, score_links=True,
LinkPreviewConfig, DefaultMarkdownGenerator(ignore_mailto_links=False)`, `arun` (miss then cache hit), `arun_many` and `robots_parser.can_fetch`. It succeeded
(status 200, markdown, links, metadata). No repo file was edited. (`uv run` re-synced the untracked `.venv`; that is the only side effect.)

Totals: 59 tracked `.py` files, 35,232 lines. Whole-file deletions below remove about 18,900 lines. Trims remove roughly another 8,000 to 9,000.

---------------------------------------------------------------------------------------------------

## 0. Verification of gazebo's usage (confirmed, plus extras)

Confirmed exactly as stated. Extra findings from `git grep` in gazebo:

- CrawlResult fields read: `url, html, markdown (str / .raw_markdown / .markdown_with_citations), links.get("internal"), media.get("images"), metadata.get("title"),
  status_code, success, error_message`. Nothing reads `redirected_url, cleaned_html, fit_html, fit_markdown, response_headers, screenshot, pdf, mhtml, tables,
  ssl_certificate, extracted_content, network_requests, console_messages, session_id, dispatch_result`.
- Gazebo's tests build `AsyncCrawlResponse(html=, response_headers={}, status_code=200)` (`test_web_crawler_service.py:38`) and patch
  `crawl4ai.async_webcrawler.AsyncPlaywrightCrawlerStrategy` (name must remain a module-level import in `async_webcrawler.py`) plus `AsyncWebCrawler.arun/arun_many`.
  They assert `arun_mock.call_args.kwargs["config"]` and `cache_mode == WRITE_ONLY`, so keep `arun(url, config=...)` and the `config.clone(user_agent=, cache_mode=)` semantics.
- Gazebo only passes `CrawlerRunConfig` kwargs: `check_robots_txt, cache_mode, word_count_threshold, remove_forms, exclude_social_media_links, exclude_all_images,
  exclude_external_links, excluded_tags, score_links, link_preview_config, markdown_generator`. `LinkPreviewConfig()` is used default or with `(concurrency=3, score_threshold=0.2)`;
  `query` is never set, so BM25 never runs.
- Gazebo never calls: `aseed_urls`/`AsyncUrlSeeder`/`SeedingConfig`, deep crawl, extraction strategies, `js_code`, `css_selector`, `wait_for`, `screenshot`, `pdf`, `stream`,
  proxies, sessions, `check_ssl`, c4a scripts, CLI scripts (`crwl`, `crawl4ai-setup`, etc.), Dockerfiles do not reference crawl4ai entry points.
- Gazebo's own `pyproject.toml` pins `crawl4ai = { git = ".../CoProcure/crawl4ai", rev = ... }` and also declares `lxml, beautifulsoup4, numpy, requests, playwright==1.64.1`.
  Gazebo imports `pydantic` at runtime but declares it only in the dev group (provided transitively today by crawl4ai, google-genai, mcp...), and `import yaml` appears only in
  `.claude/skills/.../vendor_list.py`. Dropping `pydantic`-adjacent or `PyYAML` from crawl4ai is fine, but note pydantic stays anyway (models.py).
- Gazebo's uv.lock lists these as crawl4ai deps today: aiofiles, aiohttp, aiosqlite, anyio, beautifulsoup4, brotli, chardet, click, fake-useragent, httpx[http2], humanize, lark, lxml, nltk,
  numpy, patchright, pillow, playwright, protego, psutil, pydantic, pyopenssl, python-dotenv, pyyaml, rank-bm25, requests, rich, shapely, snowballstemmer, tf-playwright-stealth, tiktoken, xxhash.

---------------------------------------------------------------------------------------------------

## 1. Runtime code paths actually exercised

Entry: `AsyncWebCrawler.__init__` (`async_webcrawler.py:107`).

| Step | What runs (file:line) | Third-party needed |
|---|---|---|
| Construction | `BrowserConfig(user_agent=...)` (`async_configs.py:394`) always builds `ValidUAGenerator()` (line 479) which calls `fake_useragent.UserAgent()` even though the result is only used when `user_agent_mode == "random"`. Then `UAGen.generate_client_hints(user_agent)` (487) sets the `sec-ch-ua` header: this part is used. | fake-useragent (avoidable), none for hints |
| Logger | gazebo passes `AsyncLogger(log_level=WARNING)`; `AsyncLogger` uses `rich.Console` (`async_logger.py:7`) and `utils.create_box_message` | rich |
| Strategy | `AsyncPlaywrightCrawlerStrategy(browser_config, logger)` (`async_crawler_strategy.py:81`): `adapter = PlaywrightAdapter()`, `BrowserManager(use_undetected=False)`; hooks dict | playwright |
| Dirs | `os.makedirs(~/.crawl4ai, ~/.crawl4ai/cache)` (`async_webcrawler.py:150-152`); logger file `crawler.log` only if no logger passed | |
| Robots | `RobotsParser(cache_client)` (`utils.py:234`): cache key `c4ai:robots:<domain>`, fetch with `aiohttp.ClientSession` (utils.py:280), parse with `Protego` | aiohttp, protego |
| Deep crawl hook | `self.arun = DeepCrawlDecorator(self)(self.arun)` (line 160). Per call it only checks `config.deep_crawl_strategy`; always falls through to the original `arun` for gazebo | none |
| `start()` | `BrowserManager.start()` (`browser_manager.py:632`): `playwright.async_api.async_playwright().start()`; `enable_stealth=False` so `playwright_stealth` is NOT imported; `use_managed_browser=False`, `cdp_url=None` so `ManagedBrowser`/psutil path is NOT used; `chromium.launch(**_build_browser_args())` (687) with `channel="chromium"`. Hook `on_browser_created`. | playwright |
| `arun` | defaults `cache_mode` to BYPASS if None; `CacheContext` (cache_context.py) decides read/write; cache read `cache_client.get(key=f"c4ai:html:{url}")` then `aprocess_html` on the cached HTML (no browser); robots check; `crawler_strategy.crawl()`; writes `crawl_result.html` back with `config.cache_ttl_seconds` | |
| Fetch | `crawl()` (432) goes to `_crawl_web` (506). Default-path features that run: user agent override (`config.user_agent`), `get_page` -> `create_browser_context` + `setup_context` (adds `cookiesEnabled` cookie, `sec-ch-ua` and custom headers), `on_page_context_created` hook (gazebo's media-blocking), `page.goto`, `wait_for_selector("body")`, `csp_compliant_wait` visibility check, `update_image_dimensions.js` (non-text-mode default), `delay_before_return_html=0.1`, `page.content()`, `before_return_html` hook. NOT run by default: ssl cert fetch, downloads, scan_full_page, virtual scroll, js_code, wait_for, iframes, overlay removal, navigator override (`navigator_overrider.js`), screenshot/pdf/mhtml, network/console capture, adapter console setup | playwright |
| Process HTML | `aprocess_html` (397): `LXMLWebScrapingStrategy.scrap` -> `_scrap` (595): lxml parse, tag/forms/social removal, media (images), links incl. `calculate_link_intrinsic_score` (`utils.py:2278`) and `extract_page_context` (2220) because `score_links=True`, metadata via `extract_metadata_using_lxml` (923), then **`table_extraction.extract_tables` runs on every page by default** (line 752; `CrawlerRunConfig.__init__` instantiates `DefaultTableExtraction` at `async_configs.py:1196`). `preprocess_html_for_schema` (`utils.py:1969`) always runs to produce `fit_html` (webcrawler line 473) even though nothing reads it | lxml, xxhash (fit_html only) |
| Link preview | still inside `_scrap` (content_scraping_strategy.py:802-880): `LinkPreview` -> `AsyncUrlSeeder.extract_head_for_urls` (`link_preview.py:15,56`; `async_url_seeder.py:465`, `_fetch_head` 1027, `_validate` 954, `_cache_get/_cache_set` 232/238 under `c4ai:url_seeder:`). Runs a nested `asyncio.run` in a thread executor. Uses `httpx.AsyncClient(http2=True)` (async_url_seeder.py:208), optional `brotli` decode (1081), `lxml` head parse. BM25 (`rank_bm25`) and sitemap/Common Crawl are NOT exercised (no `query`). Note: `LXMLWebScrapingStrategy` gets `cache_client` from `CrawlerRunConfig.__init__`'s `cache_client` arg, which gazebo does not set, so head-peek caching is effectively off through this path (see risks). | httpx[http2], h2, lxml, brotli (optional) |
| Markdown | `DefaultMarkdownGenerator.generate_markdown` (`markdown_generation_strategy.py:148`) uses `CustomHTML2Text` (`html2text/__init__.py`) and `convert_links_to_citations` (82). `content_filter` is None so fit_markdown is skipped. No nltk, no BM25. | none (vendored html2text) |
| Result | `CrawlResultContainer(CrawlResult)` (`models.py`), pydantic | pydantic |
| `arun_many` | `MemoryAdaptiveDispatcher(rate_limiter=RateLimiter(base_delay=(1,3), max_delay=60, max_retries=3))` (`async_webcrawler.py:650`). Exercised: `run_urls`, `crawl_url`, `_memory_monitor_task`, `select_config`, `wait_if_needed`, `update_delay`. Uses `psutil` via `utils.get_true_memory_usage_percent` (utils.py:2594) and `get_true_available_memory_gb` (2552, spawns `vm_stat` on macOS). `CrawlerMonitor` (rich) is imported at module load (async_dispatcher.py:12) but only used if `monitor=` is passed. `DispatchResult` attached to each result. | psutil |
| `close()` | `BrowserManager.close()` | |

Not on the default path (verified by sys.modules + profile): `async_database`/`aiosqlite`, `migrations`, `browser_profiler`, `ManagedBrowser`, `patchright` module is imported at import time only because `browser_adapter.py:19` does a `try: from patchright...` at module level (and `UndetectedAdapter` is defined there), `playwright_stealth`, `ssl_certificate`/`OpenSSL` (imported at module load via `models.py:7`, never called), `nltk`, `torch`, `PIL` (module-level import in async_crawler_strategy, only used for screenshots), `chardet`/`aiofiles` (only in `AsyncHTTPCrawlerStrategy`), `tiktoken` (imported at top of `table_extraction.py:15` but never used in that file), `bs4` (imported by `utils.py` and `content_filter_strategy`/`extraction_strategy`, only used in deleted-or-dead functions), `rank_bm25`/`snowballstemmer` (content_filter_strategy; rank_bm25 also optional in seeder), `yaml`, `humanize`, `click`, `shapely` (not imported anywhere in the repo), `lark` (script/).

Third-party packages that appear in `sys.modules` after the real run: OpenSSL, PIL, aiofiles, aiohttp, bs4, brotli, chardet, dotenv, fake_useragent, h2, httpx, lark, lxml, numpy, patchright, playwright, protego, psutil, pydantic, rank_bm25, requests, rich, snowballstemmer, tiktoken, xxhash. All of those except the kept ones (section 3) are only there because of module-level imports in modules we plan to delete or trim.

Import closure: `__init__.py` pulls 48/59 modules (confirmed: 49 `crawl4ai.*` entries in sys.modules). Once `__init__` exports only gazebo's names and TRIM files drop module-level imports of deleted modules, the surviving import graph is:
`__init__ -> async_webcrawler -> {async_configs, async_crawler_strategy, async_dispatcher, async_logger, async_url_seeder, cache_client, cache_context, markdown_generation_strategy, models, utils, __version__}`;
`async_configs -> {cache_client, cache_context, config, content_scraping_strategy, markdown_generation_strategy}`;
`async_crawler_strategy -> {async_configs, async_logger, browser_manager, config, js_snippet, models}` (+ `browser_adapter` if kept);
`content_scraping_strategy -> {config, models, utils, link_preview (lazy)}`; `link_preview -> {async_configs, async_logger, async_url_seeder, models, utils}`;
`markdown_generation_strategy -> {html2text, models}`; `browser_manager -> {async_configs, config, js_snippet}`; `async_logger -> utils`.
That is about 20 modules (versus 59).

---------------------------------------------------------------------------------------------------

## 2. Module verdict table

Legend: DELETE = remove file. KEEP = unchanged (modulo import fixes). TRIM = keep file, remove listed pieces.

### Top level

| File | Verdict | Reason / specifics |
|---|---|---|
| `crawl4ai/__init__.py` | TRIM | Export only: `AsyncWebCrawler, AsyncLogger (and AsyncLoggerBase), BrowserConfig, CrawlerRunConfig, CrawlResult, CacheMode, LinkPreviewConfig, DefaultMarkdownGenerator` (+ keep `ProxyConfig`, `MatchMode`, `LinkPreview`, `MarkdownGenerationResult`, `MemoryAdaptiveDispatcher`, `RateLimiter`, `BaseDispatcher`, `SeedingConfig` if you want a conservative public surface; the in-repo tests import `MemoryAdaptiveDispatcher`, `RateLimiter`). Drop the other ~40 exports, `start_colab_display_server`, `setup_colab_environment`, c4a_*. Keep the `warnings.filterwarnings("ignore", module="pydantic")` line (line 193). `from_serializable_dict` does `getattr(crawl4ai, type_name)` so only classes exported here can round-trip through `dump/load` (see risks). |
| `__version__.py` | KEEP | read by `async_webcrawler.py:14` and (after trim) nothing else. |
| `adaptive_crawler.py` (1805 lines) | DELETE | `AdaptiveCrawler` only; needs numpy, embeddings. Imported only by `__init__`. |
| `async_configs.py` (1808) | TRIM | see detail below |
| `async_crawler_strategy.back.py` (2449) | DELETE | dead backup copy, imported nowhere. |
| `async_crawler_strategy.py` (2328) | TRIM | see detail below |
| `async_database.py` (566) | DELETE | no longer on any path since `CacheClient` replaced it; only `install.py`/`migrations.py` use it. Removes aiosqlite, aiofiles, `VersionManager`. |
| `async_dispatcher.py` (767) | TRIM | keep `RateLimiter` (23), `BaseDispatcher` (84, drop `monitor` param), `MemoryAdaptiveDispatcher` (143; drop `monitor` references at 12, 88, 94, 128, 138, 154, 180, 199, and `run_urls_stream` + `_update_queue_priorities` streaming/`DisplayMode` bits if you also drop `stream`). Delete `SemaphoreDispatcher` (619). Replace `from .types import AsyncWebCrawler` (19) with a `TYPE_CHECKING` import of `async_webcrawler`. Keep psutil. |
| `async_logger.py` (374) | KEEP | `LogLevel, LogColor, AsyncLoggerBase, AsyncLogger`. Needs `rich`. (`create_box_message` import from utils must stay in utils.) |
| `async_url_seeder.py` (1465) | TRIM | Needed only for `extract_head_for_urls` (465), `_fetch_head` (1027), `_validate` (954), `_head_ok` (1008) if referenced, `_parse_head` (89), `_match` (81), `_is_nonsense_url` (1277), `_cache_get/_cache_set/_get_cache_key` (228-245), `__init__/__aenter__/__aexit__/close/_log`. Delete: `urls` (247), `many_urls` (430), `_from_cc` (689), `_from_sitemaps` (750), `_iter_sitemap` (825), `_resolve_head` (659), `_apply_bm25_scoring` (625), `_calculate_bm25_score` (1373), `_calculate_url_relevance_score` (1190), `_extract_text_context` (1137) unless `link_preview._merge_head_data` calls it (check), `get_latest_index`/CC index code (1437-1450, `cc_index_cache_key`), `HAS_BM25`/`rank_bm25` import (49-53). Keep `HAS_BROTLI` branch. Then `URL_SEEDER_CACHE_KEY_PREFIX` is still used by `_get_cache_key`. A follow-up option: fold the remaining ~350 lines into `link_preview.py` and delete the seeder module and `SeedingConfig` (needs `LinkPreview` to stop building a `SeedingConfig`, link_preview.py:203). |
| `async_webcrawler.py` (781) | TRIM | see detail below |
| `browser_adapter.py` (292) | DELETE (or TRIM) | Default path only calls `adapter.evaluate` (a one-line wrapper over `page.evaluate`). Preferred: delete file and inline `page.evaluate(expr, arg)` at the 14 call sites in `async_crawler_strategy.py` (336, 381, 873, 902, 1237, 1321, 1724, 1846, 1930, 1971, 2028, 2048) and drop `adapter=` (lines 76, 91, 107, 110-115). Fallback: keep `BrowserAdapter` + `PlaywrightAdapter` (58-150), delete `UndetectedAdapter` (151-292) and the patchright imports (13-21). Either way removes `patchright`. |
| `browser_manager.py` (1151) | TRIM | see detail below |
| `browser_profiler.py` (1235) | DELETE | profile management UI. Referenced lazily from `ManagedBrowser.create_profile/list_profiles/delete_profile` (browser_manager.py:435-557, deleted) and `types.py`/`__init__`. |
| `cache_client.py` (32) | KEEP | gazebo imports all four prefixes + `CacheClient`. Keep `DEFAULT_CACHE_TTL_SECONDS` (comment says "2 hours" but value is 120 s; pre-existing). |
| `cache_context.py` (93) | KEEP | gazebo imports `CacheMode` from here. Could trim `DISABLED/READ_ONLY` modes but they are cheap; leave. |
| `chunking_strategy.py` (256) | DELETE | `RegexChunking` default in `CrawlerRunConfig.__init__` (line 1043 default arg) and `IdentityChunking` in `aprocess_html` only serve the extraction path. Pulls `model_loader` -> nltk. Remove `chunking_strategy` param and both uses. |
| `cli.py` (1326) | DELETE | `crwl` script; click, rich, humanize, yaml, anyio, litellm usage. |
| `components/crawler_monitor.py` (836) | DELETE | rich dashboard; imported by `async_dispatcher.py:12` and `__init__`. |
| `config.py` (146) | TRIM | keep: `MIN_WORD_THRESHOLD, IMAGE_DESCRIPTION_MIN_WORD_THRESHOLD, IMAGE_SCORE_THRESHOLD, IMPORTANT_ATTRS, ONLY_TEXT_ELIGIBLE_TAGS, SOCIAL_MEDIA_DOMAINS, SCREENSHOT_HEIGHT_TRESHOLD (only if screenshot kept; else delete), PAGE_TIMEOUT, DOWNLOAD_PAGE_TIMEOUT (only if downloads kept)`. Delete: `DEFAULT_PROVIDER*`, `PROVIDER_MODELS*`, `MODEL_REPO_BRANCH`, `CHUNK_TOKEN_THRESHOLD`, `OVERLAP_RATE`, `WORD_TOKEN_RATE`, `MAX_METRICS_HISTORY`, `NEED_MIGRATION`, `URL_LOG_SHORTEN_LENGTH`, `SHOW_DEPRECATION_WARNINGS`, `USER_SETTINGS`. Drop `from dotenv import load_dotenv; load_dotenv()` (lines 2-4): this removes python-dotenv but also removes a behaviour (see risks). |
| `content_filter_strategy.py` (778) | DELETE | `PruningContentFilter/BM25ContentFilter/LLMContentFilter`; imports rank_bm25, snowballstemmer, bs4, prompts, LLMConfig. Only referenced as a type in `markdown_generation_strategy.py:6`; `content_filter` is None for gazebo. |
| `content_scraping_strategy.py` (949) | TRIM | keep `LXMLWebScrapingStrategy` (98) incl. `_scrap` (595) and link preview block (802-880), `ContentScrapingStrategy` (88), `parse_srcset`, `parse_dimension`, `WebScrapingStrategy` alias (949). Delete: `fetch_image_file_size` (74, dead; also removes `requests` + `InvalidSchema` imports, lines 9, 12), table extraction block (750-759) and the `table_extraction` kwarg, the `css_selector`/`target_elements`/`only_text` branches if you decide to drop them (judgement, section 5). |
| `deep_crawling/*` (8 files, ~2000 lines) | DELETE | `base_strategy.DeepCrawlDecorator` (used by `async_webcrawler.py:26,160`), BFS/DFS/BestFirst, filters, scorers, `crazy.py` (numpy). Remove `deep_crawl_strategy` param from `CrawlerRunConfig` (async_configs.py:23). `utils.normalize_url_for_deep_crawl`, `efficient_normalize_url_for_deep_crawl`, `HeadPeekr` go with it. |
| `extraction_strategy.py` (1488) | DELETE | CSS/XPath/Regex/LLM/Cosine strategies, `_safe_eval_expression`. Referenced by `async_configs.py:24` (default `extraction_strategy=None`, validation at 1180) and `async_webcrawler.py:27,540-584`. |
| `html2text/__init__.py, _typing.py, config.py, elements.py, utils.py` | KEEP | `CustomHTML2Text` is the markdown engine. |
| `html2text/cli.py`, `html2text/__main__.py` | DELETE | CLI of vendored library. |
| `install.py` (212) | DELETE | `crawl4ai-setup`/`crawl4ai-doctor`: runs `playwright install` and initializes `async_database`. |
| `js_snippet/__init__.py` + `update_image_dimensions.js` | KEEP | `update_image_dimensions.js` runs on every default fetch (`async_crawler_strategy.py:867`). |
| `js_snippet/navigator_overrider.js` | DELETE (optional) | only for `override_navigator/simulate_user/magic` (`async_crawler_strategy.py:553`, `browser_manager.py:841`). |
| `js_snippet/remove_overlay_elements.js` | DELETE (optional) | only for `remove_overlay_elements=True` (`async_crawler_strategy.py:1318`). If deleted, keep `pyproject.toml` `package-data` as `js_snippet/*.js` and `MANIFEST.in` (still has one file). |
| `link_preview.py` (397) | KEEP | on the default path. Only edit: remove `Links/Link` import changes if models shrink. |
| `markdown_generation_strategy.py` (260) | TRIM | keep `DefaultMarkdownGenerator`, `MarkdownGenerationStrategy`, `fast_urljoin`, `convert_links_to_citations`. Delete the `RelevantContentFilter` import (6), the `content_filter` ctor arg and the fit-markdown block (232-250 region), `content_source` handling if you drop `fit_html`. Keep the `MarkdownGenerationResult` fields (public result shape), just leave `fit_*` None. |
| `migrations.py` (197) | DELETE | `crawl4ai-migrate`; aiosqlite/aiofiles/xxhash. |
| `model_loader.py` (296) | DELETE | `crawl4ai-download-models`; torch/transformers/nltk/scipy. |
| `models.py` (367) | TRIM | keep `CrawlResult, CrawlResultContainer, MarkdownGenerationResult, StringCompatibleMarkdown (if used by CrawlResult.markdown), AsyncCrawlResponse, Link/Links, Media/MediaItem, ScrapingResult, DispatchResult, DomainState, CrawlerTaskResult, CrawlStatus, RunManyReturn`. Delete `CrawlStats` (46), `DisplayMode` (78), `TokenUsage` (87, only content_filter), `UrlModel` (94), `TraversalStats` (101, deep crawl), `CrawlResultT` (261) if unused after deep-crawl removal. Replace `ssl_certificate: Optional[SSLCertificate]` (lines 149, 310) with `Optional[Any]` or delete the field, and drop `from .ssl_certificate import SSLCertificate` (line 7): this is what lets `pyOpenSSL` go. Keep `AsyncCrawlResponse` fields that gazebo's tests don't set optional. |
| `processors/pdf/*` | DELETE | PDF processing, PyPDF2/PIL/requests; imports models only. Dead (nothing imports it). |
| `prompts.py` (1578) | DELETE | LLM prompts; imported by content_filter_strategy and script/. |
| `proxy_strategy.py` (158) | DELETE | `ProxyRotationStrategy`, `RoundRobinProxyStrategy`; remove `proxy_rotation_strategy` from `CrawlerRunConfig` and `async_webcrawler.py:238-246`. Keep `ProxyConfig` (lives in async_configs.py:217, small) unless you want proxies gone entirely. |
| `script/*` (4 files) | DELETE | C4A script language (lark); only reached from `CrawlerRunConfig._compile_c4a_script` (async_configs.py:1308-1355) and `__init__`. |
| `ssl_certificate.py` (203) | DELETE | only if `fetch_ssl_certificate` and the `models.py` field are removed (otherwise KEEP; pyOpenSSL stays). Recommended: delete. |
| `table_extraction.py` (319) | DELETE | default instance runs on every page and populates `result.tables`/`media["tables"]`; gazebo never reads tables. `tiktoken` import there (line 15) is unused anyway. |
| `types.py` (251) | DELETE | pure `Union['...Type']` aliases + `TYPE_CHECKING`; only runtime consumer after pruning is `async_dispatcher.py:19`, which moves to a direct `TYPE_CHECKING` import. |
| `user_agent_generator.py` (428) | TRIM | keep only `UAGen.generate_client_hints` (staticmethod, line ~20-65; sets `sec-ch-ua`). Delete `ValidUAGenerator` (68), `OnlineUAGenerator` (89), `UserAgentGenerator` (143, legacy). Removes fake-useragent, requests, lxml usage here. `BrowserConfig.__init__` (479-485) and `_crawl_web` (537-541) then lose the `user_agent_mode="random"` / `magic` UA generation. Alternatively move `generate_client_hints` into `async_configs.py` as a function and delete the file. |
| `utils.py` (2624) | TRIM | see detail below |

### `async_configs.py` trim detail

Keep: `MatchMode` (36) and `UrlMatcher` + `CrawlerRunConfig.is_match` (1356-1400) only if you keep `url_matcher` (arun_many with a list of configs uses it via `BaseDispatcher.select_config`; gazebo passes a single config) -> optional; `to_serializable_dict/from_serializable_dict` (42, 116) only if `dump/load` kept (they are only used by `dump/load`; `clone` does not use them); `ProxyConfig` (217), `BrowserConfig` (325), `LinkPreviewConfig` (664), `CrawlerRunConfig` (829), `SeedingConfig` (1722, only if seeder module stays), `is_empty_value` (161).
Delete: `GeolocationConfig` (169) + `geolocation` params and use in `browser_manager.create_browser_context`; `VirtualScrollConfig` (619) + `_handle_virtual_scroll` (async_crawler_strategy 1091-1262); `HTTPCrawlerConfig` (759); `LLMConfig` (1638) and `create_llm_config` (1808); imports at lines 10, 11-21 partially, 22-31 (chunking, deep_crawling, extraction, proxy_strategy, table_extraction, user_agent_generator). `CrawlerRunConfig.__init__` params to remove: `extraction_strategy, chunking_strategy, table_extraction, table_score_threshold, deep_crawl_strategy, proxy_rotation_strategy, virtual_scroll_config, c4a_script (+_compile_c4a_script 1308), scan_full_page, scroll_delay, max_scroll_steps, screenshot*, pdf, capture_mhtml, capture_network_requests, capture_console_messages, process_iframes, remove_overlay_elements, simulate_user, override_navigator, magic, adjust_viewport_to_content, user_agent_mode, user_agent_generator_config, fetch_ssl_certificate, geolocation, locale/timezone_id (keep if cheap), experimental, session_id, shared_data, semaphore_count, mean_delay/max_range (used by `RateLimiter`? check), stream` and the matching lines in `to_dict` (1528-1610) and `from_kwargs` (1407-1514). `CrawlerRunConfig.from_kwargs` and `to_dict` must be edited in lock-step because `clone()` (1612) round-trips through them; this is how gazebo's `.clone(user_agent=, cache_mode=)` works. `BrowserConfig` params to remove: `browser_mode (builtin/docker, 480-495), use_managed_browser, cdp_url, use_persistent_context, user_data_dir, chrome_channel/channel (keep one), storage_state, downloads_path/accept_downloads, enable_stealth, debugging_port, host, text_mode/light_mode (cheap, keep if wanted), user_agent_mode, user_agent_generator_config`. Keep `user_agent, headers, viewport_*, proxy/proxy_config, ignore_https_errors, java_script_enabled, cookies, extra_args, headless, browser_type, verbose, sleep_on_close`. `BrowserConfig.__init__` must stop instantiating `ValidUAGenerator()` (line 479).

### `async_crawler_strategy.py` trim detail

Keep: `AsyncCrawlerStrategy` ABC (42, or drop the ABC and type `crawler_strategy` as the Playwright class; gazebo only uses `AsyncPlaywrightCrawlerStrategy`), `AsyncPlaywrightCrawlerStrategy` (52): `__init__` (81), `__aenter__/__aexit__/start/close` (125-149), `set_hook/execute_hook` (168/194), `update_user_agent` (213), `set_custom_headers` (225), `csp_compliant_wait` (298), `crawl` (432, trimmed to http/https only, or keep file:// and raw: since tests use `raw:`), `_crawl_web` (506, trimmed), `check_visibility` (1920).
Delete: `kill_session` (151), `smart_wait` (237, only for `wait_for`), `process_iframes` (344), `create_session` (408), `_handle_full_page_scan` (1013), `_handle_virtual_scroll` (1091), `_handle_download` (1262), `remove_overlay_elements` (1311), `export_pdf` (1345), `capture_mhtml` (1358), `_capture_console_messages` (1422), `take_screenshot*` (1458-1645), `export_storage_state` (1645), `robust_execute_user_script` (1669), `execute_user_script` (1819), `safe_scroll`/`csp_scroll_to`/`get_page_dimensions`/`page_need_scroll` (1944-2068), `_generate_screenshot_from_html`, the whole HTTP strategy block `HTTPCrawlerError, ConnectionTimeoutError, HTTPStatusError, AsyncHTTPCrawlerStrategy` (2071-2328). Remove `_crawl_web` branches: network capture (560-640), console capture (643-651, 959, 996-1007), ssl (655-657), downloads (659-665), js_only (668/726), body-visibility error raising can stay, `wait_for_images/adjust_viewport_to_content` (767-815), scan_full_page (819), virtual scroll (824), `js_code` (827-841), simulate_user (843), wait_for (855-865), iframes (882), overlay (891), `css_selector` (894-915; judgement), pdf/mhtml/screenshot (928-957), `get_delayed_content` closure, `user_agent_mode/magic` (537-541, 553-555), session handling in `finally` (990). Module imports to drop: `aiofiles, aiohttp, chardet, ClientTimeout, PIL (Image, ImageDraw, ImageFont), BytesIO, base64, hashlib?, uuid?, AsyncGenerator, MappingProxyType, HTTPCrawlerConfig, VirtualScrollConfig, BrowserAdapter/PlaywrightAdapter/UndetectedAdapter, SSLCertificate, ValidUAGenerator, SCREENSHOT_HEIGHT_TRESHOLD`. Re-verify unused imports with `ruff --select F401`.
Expected size after trim: roughly 500 lines (from 2328).

### `browser_manager.py` trim detail

Delete: `ManagedBrowser` (43-557) including `build_browser_flags` (69), `BrowserProfiler` shims (435-557), and `import psutil, signal, subprocess, shlex, tempfile, shutil, get_chromium_path` (lines 5-10, 17). Delete from `BrowserManager` (558): managed/CDP branch in `__init__` (601-611, 653-672), stealth (617-618, 653-659, 1137-1149), patchright (`use_undetected` at 576-583, 584, 650-651, `async_crawler_strategy.py:94`), `kill_session` (1077), session TTL logic (`sessions`, `_cleanup_expired_sessions` 1091; default path calls it harmlessly), persistent-context branches in `create_browser_context` (843-973) and `get_page` (1010-1076) other than the plain "launch + new context per config signature" path, `firefox/webkit` branches (optional), `accept_downloads`/`downloads_path` (700-708), `BROWSER_DISABLE_OPTIONS`/`light_mode`/`text_mode` args (optional), `navigator_overrider` init script (841). Keep: `__init__`, `start` (chromium launch), `_build_browser_args` (687), `setup_context` (760), `create_browser_context`, `_make_config_signature` (974), `get_page`, `close` (1102), `BrowserManager._playwright_instance` (async_crawler_strategy.py:147 resets it). Expected size: ~350 lines (from 1151).

### `async_webcrawler.py` trim detail

Delete: imports of `AsyncCrawlerStrategy` ABC if dropped, `BaseDispatcher` (keep if `arun_many(dispatcher=)` stays), `AsyncUrlSeeder`, `chunking`, `deep_crawling`, `extraction_strategy`, `SeedingConfig`, `fast_format_html`, `preprocess_html_for_schema`, `InvalidCSSSelectorError` (if css_selector goes); `__init__` params `crawler_strategy`? (keep: gazebo constructs via default; tests patch the class), `thread_safe`/`_lock`/`nullcontext` (163, 194-198, 233) optional; `self._deep_handler`/`self.arun = ...` (159-161); `self.url_seeder` (163); proxy rotation block (237-246); `aseed_urls` (692-781); `aprocess_html` blocks: `fit_html`/`html_source_selector`/`content_source` (473-515, replace with `markdown_input_html = cleaned_html`), structured extraction (540-584), `prettiify` (587), `tables` (463, 469, 598), `screenshot_data/pdf_data` passes (317-318, 601-603), `crawl_result.downloaded_files, js_execution_result, mhtml, ssl_certificate, network_requests, console_messages` assignments (347-353) (keep fields on `CrawlResult` as optional to avoid pydantic errors in anyone deserializing). In `arun_many`: stream branch (680-688), list-of-configs typing (optional). `RobotsParser` import stays. Keep: `start/close/__aenter__/__aexit__`, `arun` core (cache read/write, robots, crawl, aprocess_html), `_get_cache_key`, `arun_many`, `robots_parser`, `crawler_strategy`, `browser_config`, module-level name `AsyncPlaywrightCrawlerStrategy` (gazebo tests patch it).

### `utils.py` trim detail (keep list is short)

Keep: `RobotsParser` (234), `InvalidCSSSelectorError` (298, only if css_selector kept), `create_box_message` (557), `sanitize_input_encode` (875), `extract_metadata_using_lxml` (923), `normalize_url` (1298), `get_base_domain` (1424), `is_external_url` (1475), `get_error_context` (1831), `extract_page_context` (2220), `calculate_link_intrinsic_score` (2278), `calculate_total_score` (2430), `get_true_available_memory_gb` (2552), `get_true_memory_usage_percent` (2594). Conditional: `fast_format_html` (1252) only if `prettiify` kept; `preprocess_html_for_schema` (1969) only if `fit_html` kept; `get_home_folder` (712) is used only by deleted modules and `tests/test_utils.py`.
Delete everything else: `chunk_documents, merge_chunks, VersionManager, advanced_split, calculate_semaphore_count, get_system_memory, get_chromium_path, beautify_html, split_and_parse_json_objects, sanitize_html (used by table_extraction/content_filter), escape_json_string, extract_metadata (bs4), extract_xml_*, merge_chunks_based_on_token_threshold, wrap_text, format_html (bs4), normalize_url_for_deep_crawl, efficient_normalize_url_for_deep_crawl, clean_tokens, profile_and_time, generate_content_hash, ensure_content_dirs, configure_windows_event_loop (also imported lazily by nothing), truncate, optimize_html, HeadPeekr (httpx), start_colab_display_server, setup_colab_environment, get_text_embeddings(+_sync), cosine_similarity, cosine_distance, get_memory_stats, SPLITS, HTML_CODE_CHARS`. Module imports to drop: `bs4, numpy, xxhash (if preprocess_html_for_schema goes), httpx, packaging.version, cProfile, pstats, linecache? (get_error_context uses linecache, keep), playwright`. Keep: `aiohttp, protego, lxml, psutil`. `utils.py` goes from 2624 to roughly 600 lines.

---------------------------------------------------------------------------------------------------

## 3. Dependency verdicts

`pyproject.toml` `dependencies` (lines 15-48):

| Dependency | Verdict | Who still needs it after trimming (or why it goes) |
|---|---|---|
| aiofiles | REMOVE | only `async_database`, `migrations`, `AsyncHTTPCrawlerStrategy._stream_file` (all deleted). |
| aiohttp | KEEP | `utils.RobotsParser.can_fetch` (utils.py:280). (Could be swapped for httpx later to drop aiohttp; but `tests/test_robots_parser.py` uses `aiohttp.web`, so it would need to move to the dev group.) |
| aiosqlite | REMOVE | `async_database`, `migrations` only. Never imported at runtime now. |
| anyio | REMOVE | `cli.py` only. (httpx brings it transitively.) |
| lxml | KEEP | `content_scraping_strategy`, `utils`, `async_url_seeder`. |
| numpy | REMOVE | `extraction_strategy`, `crazy.py`, `adaptive_crawler`, `utils` embedding helpers (all deleted). Gazebo pins numpy itself. |
| pillow | REMOVE | only `async_crawler_strategy` screenshot code. Removes the pillow security-alert surface (`chore(deps): update pillow to v13 [security]`). |
| playwright | KEEP | `browser_manager`, `async_crawler_strategy`. Gazebo pins `==1.64.1`; keep crawl4ai's range loose (`>=1.49`). |
| patchright | REMOVE | `UndetectedAdapter` + `use_undetected` branches. |
| python-dotenv | REMOVE (judgement) | only `config.py:2-4`. See risk 4 for the behaviour change. |
| requests | REMOVE | `content_scraping_strategy.fetch_image_file_size` (dead), `user_agent_generator` (trimmed), `processors/pdf`. Gazebo declares requests itself. |
| beautifulsoup4 | REMOVE | `utils` (beautify_html, extract_metadata, format_html), `content_filter_strategy`, `extraction_strategy` (all deleted). Gazebo pins beautifulsoup4 itself. |
| tf-playwright-stealth | REMOVE | lazy import in `browser_manager.py:655` behind `enable_stealth` (default False). |
| xxhash | REMOVE if `preprocess_html_for_schema`/`fit_html` is dropped, else KEEP | `utils.preprocess_html_for_schema` (1969), `generate_content_hash`, `migrations`. |
| rank-bm25 | REMOVE | `content_filter_strategy`; optional in seeder (guarded by `HAS_BM25`). Never reached without `LinkPreviewConfig.query`. |
| snowballstemmer | REMOVE | `content_filter_strategy` only. |
| pydantic | KEEP | `models.py`. |
| pyOpenSSL | REMOVE | `ssl_certificate.py` only (after dropping the `models.py` field and fetch branch). Not needed by playwright. |
| psutil | KEEP | `async_dispatcher` via `utils.get_true_memory_usage_percent/available_memory` (default `arun_many`). Dropping it means replacing `MemoryAdaptiveDispatcher` with a plain semaphore dispatcher. |
| PyYAML | REMOVE | `cli.py` only. |
| nltk | REMOVE | `model_loader`, `chunking_strategy` (lazy, never imported). |
| rich | KEEP | `async_logger.py` (gazebo uses `AsyncLogger`). |
| httpx | KEEP | `async_url_seeder` (link head extraction). Remove `httpx` duplicate entry (line 28; the `httpx[http2]` entry on line 29 already covers it). |
| httpx[http2] | KEEP | `httpx.AsyncClient(http2=True)` in the seeder (async_url_seeder.py:208) requires `h2`. |
| fake-useragent | REMOVE | `user_agent_generator.ValidUAGenerator` (and `BrowserConfig.__init__` always instantiating it). |
| click | REMOVE | `cli.py` only. |
| chardet | REMOVE | `AsyncHTTPCrawlerStrategy._handle_http` only. |
| brotli | KEEP (optional) | `async_url_seeder.py:44,1081` decodes `br` head responses; guarded by `HAS_BROTLI`. Drop it only if you accept failures/garbage on brotli-encoded pages during link preview (httpx also decodes brotli if the package is present). |
| humanize | REMOVE | `cli.py` only. |
| lark | REMOVE | `script/` only. |
| shapely | REMOVE | not imported anywhere in the repo. |
| protego | KEEP | `utils.RobotsParser`. |
| tiktoken | REMOVE | imported by `table_extraction.py:15` but never used; file deleted. |

Implicit dependency to note: `utils.py:42` imports `packaging.version` (not declared); disappears with `VersionManager` removal. `lxml.cssselect` (needs the `cssselect` package) is used in `extraction_strategy`; not declared and goes away with it.

Result: `dependencies = ["aiohttp", "brotli", "httpx[http2]", "lxml", "playwright", "protego", "psutil", "pydantic", "rich"]` (9, from 34; or 8 if brotli is dropped). Optional-dependencies (`pdf, torch, transformer, cosine, sync, all`): REMOVE all; none are used (PyPDF2 only by `processors/pdf`; torch/transformers/sentence-transformers/scikit-learn/selenium by deleted modules; selenium is not imported anywhere). Dev group (`pytest, pytest-asyncio, ruff`) KEEP; `aiohttp` is imported by `tests/test_robots_parser.py:7` (stays runtime so fine).

`[project.scripts]`: all five go. `crawl4ai-download-models` (`model_loader`), `crawl4ai-migrate` (`migrations`), `crawl4ai-setup` and `crawl4ai-doctor` (`install`), `crwl` (`cli`). Gazebo does not invoke any (git grep clean; no Dockerfile references).

Other packaging:
- `setup.py`: not a post-install hook but it runs at build time and **creates `~/.crawl4ai/{cache,html_content,cleaned_html,markdown_content,extracted_content,screenshots}` and `rmtree`s `~/.crawl4ai/cache`** on every build/install (lines 10-30). Recommend DELETE `setup.py` and `setup.cfg` (pyproject has everything; `package_data` also lives in pyproject `[tool.setuptools.package-data]`, so `setup.cfg include_package_data` is redundant). There is no playwright install, nltk download or model download in setup.py. Those happen only in `install.py` (deleted).
- `MANIFEST.in`: `include requirements.txt` (the file does not exist at root), `recursive-include crawl4ai/js_snippet *.js`. Trim to the second line (or delete; pyproject package-data covers it).
- No root `requirements*.txt`. Stray ones: `docs/examples/c4a_script/tutorial/requirements.txt`, `docs/md_v2/apps/c4a-script/requirements.txt`, `unvalidated_tests/memory/requirements.txt` all go with their directories.
- `pyproject.toml` also: `requires-python = ">=3.13"` while classifiers list 3.11/3.12 (cosmetic), `readme = "README.md"` (keep a short README or update), `[tool.setuptools] packages find include crawl4ai*` fine.
- `uv.lock` must be regenerated (`uv lock`) in the dependency PR; `renovate.json` fine.
- CI (`.github/workflows/pr-ci.yaml`) installs `libxml2-dev libxslt1-dev libjpeg-dev libgeos-dev` apt packages and runs `playwright install --with-deps chromium` and `uv run pytest tests/`: after trim, `libjpeg-dev` (pillow) and `libgeos-dev` (shapely) can be dropped; `libxml2/libxslt` stay for lxml.

---------------------------------------------------------------------------------------------------

## 4. Tests, docs, other top-level files

`tests/` (CI: `uv run pytest tests/`):

| File | Covers | Verdict |
|---|---|---|
| `tests/__init__.py` | package marker | KEEP |
| `tests/helpers.py` | `TestCacheClient`, `EXAMPLE_*` | KEEP |
| `tests/test_caching.py` | cache ENABLED/BYPASS, `excluded_tags` with cache, uses `AsyncCrawlResponse` | KEEP (core path: `cache_client` get/set, `CacheContext`) |
| `tests/test_robots_parser.py` | `RobotsParser`, `check_robots_txt` | KEEP (core; needs `aiohttp.web` test server) |
| `tests/test_async_web_crawler.py` | `arun`, `arun_many` with `MemoryAdaptiveDispatcher`/`RateLimiter`, viewport, error handling, media, metadata, plus `test_javascript_execution` (`js_code`, line ~66-91) and memory/rate-limit tests | KEEP, but remove `test_javascript_execution` if `js_code` is deleted. Hits the live network (example.com); same as today. |
| `tests/test_crawler_run_config.py` | `url_matcher/is_match` tests (35-117), `test_link_preview` (122, live docs.python.org), `test_css_selector` (149), `test_js_code` (165, live nbcnews), `test_user_agent` (181, live) | KEEP `test_link_preview`, `test_user_agent`; keep `is_match` tests only if `url_matcher` stays; delete `test_js_code` and `test_css_selector` if those features go. Consider replacing live-network tests with the local-server pattern used by #96. |
| `tests/test_utils.py` | `get_home_folder`, `normalize_url` | KEEP `TestNormalizeUrl`; delete `test_get_home_folder` if `get_home_folder` is deleted. |
| `tests/test_safe_eval.py` | `extraction_strategy._safe_eval_expression` | DELETE (module deleted) |
| `tests/test_virtual_scroll.py` | `VirtualScrollConfig` | DELETE |

Net: add 1-2 tests for what is now thinly tested but gazebo-critical: `CrawlerRunConfig.clone(user_agent=, cache_mode=)` preserving all gazebo kwargs, `LinkPreviewConfig` defaults, `crawler.robots_parser.can_fetch`, `arun_many` returns a list of `CrawlResult` (non-stream).

Other directories/files:

| Path | Verdict | Note |
|---|---|---|
| `unvalidated_tests/` (63 files) | DELETE | tests for adaptive, deep crawl, extraction, browser profiles, dispatch/stress, cli, proxy, mhtml, etc.; not run in CI. |
| `docs/` (370 tracked files: apps 14, assets 6, blog 4, codebase 2, examples 148, md_v2 186, releases_review 7, snippets 2, tutorials 1) | DELETE | none of it is imported; includes a Chrome extension app (`docs/md_v2/apps/crawl4ai-assistant`), c4a-script tutorial, etc. |
| `mkdocs.yml` | DELETE | only builds `docs/`. |
| `prompts/prompt_net_requests.md` | DELETE | authoring prompt. |
| `README.md`, `README-first.md` | DELETE `README-first.md`; REWRITE `README.md` to 20-30 lines describing the fork. `pyproject.toml` has `readme = "README.md"`. |
| `CHANGELOG.md` (73K), `cliff.toml` (git-cliff config for CHANGELOG), `ROADMAP.md`, `JOURNAL.md`, `MISSION.md`, `PROGRESSIVE_CRAWLING.md`, `CONTRIBUTORS.md`, `SPONSORS.md`, `CODE_OF_CONDUCT.md` | DELETE | upstream project marketing/history. Keep `LICENSE` (Apache-2.0 requires it) and add a short NOTICE/README statement of the fork origin. |
| `.env.txt` | DELETE | placeholder provider keys (GROQ/OPENAI/ANTHROPIC). |
| `.claude/settings.local.json` | DELETE | upstream author's local permissions file (contains `/Users/unclecode/...` paths); tracked by mistake. |
| `.github/DISCUSSION_TEMPLATE`, `ISSUE_TEMPLATE`, `pull_request_template.md` | DELETE (or keep PR template) | upstream community templates. Keep `workflows/pr-ci.yaml` (edit apt list). |
| `.gitattributes`, `.gitignore`, `.ruff.toml`, `renovate.json` | KEEP | |
| `Crawl4AI.egg-info/`, `logs/` | untracked/ignored; ignore | |
| `scraper_equivalence_results.json` | untracked, pre-existing (not mine); leave | |
| `crawl4ai/crawlers/` | empty untracked dir (only `__pycache__`); remove locally | |

---------------------------------------------------------------------------------------------------

## 5. Risks, judgment calls, and PR order

### Behaviour that changes when a default-triggered path is deleted

1. **`BrowserConfig.__init__` instantiates `UserAgent()` unconditionally** (`async_configs.py:479`). Safe to delete; only `user_agent_mode="random"` / `magic=True` consumed it. Keep `UAGen.generate_client_hints` because it produces the `sec-ch-ua` header gazebo currently sends (derived from gazebo's Chrome/131 user agent; confirmed in the trace: `generate_client_hints` and `_parse_user_agent` ran). Note `.clone(user_agent=<Safari>)` only changes the UA string, not the already-computed `sec-ch-ua` header (existing behaviour; deleting nothing here changes it).
2. **Table extraction runs on every page** (`async_configs.py:1196`, `content_scraping_strategy.py:752`). Deleting it drops `CrawlResult.tables` and `media["tables"]` (always `[]`) and saves per-page work. Gazebo does not read tables. Note `excluded_tags=["table"]` currently suppresses it; no gazebo config does so.
3. **`fit_html` is computed unconditionally** (`async_webcrawler.py:473`, `utils.preprocess_html_for_schema`). Deleting yields `CrawlResult.fit_html = None`. Not read by gazebo. Also drops xxhash.
4. **`config.py` calls `load_dotenv()` at import.** Gazebo (a Django app) will stop having a `.env` in the current working directory silently merged into `os.environ` when `crawl4ai` is imported. Gazebo uses Django settings/env, so likely harmless, but confirm nothing in gazebo depends on that side effect (e.g. local dev `.env`). This is the one dependency removal with a runtime side effect.
5. **Link preview cache**: `LXMLWebScrapingStrategy` is created in `CrawlerRunConfig.__init__` with `cache_client=cache_client` (async_configs.py:1139 region), and gazebo never passes `cache_client` to `CrawlerRunConfig`, so `from_kwargs` passes `None` (`async_configs.py` `kwargs.get("cache_client", None)`). `LinkPreview(self.cache_client, ...)` then gets `None`/the default `NoCacheClient()`; head-peek results are therefore not cached via gazebo's Redis client even though `URL_SEEDER_CACHE_KEY_PREFIX` exists and gazebo clears it. Pre-existing, not caused by pruning; if you refactor the scraper construction, preserve (or fix deliberately) this behaviour.
6. **`arun` wrapper removal**: `self.arun = DeepCrawlDecorator(self)(self.arun)` goes away. Gazebo's tests patch the class attribute `AsyncWebCrawler.arun` with an `AsyncMock`; with the decorator the instance attribute wraps the mock (and calls it as `mock(url, config=config)`), without it `crawler.arun(url=url, config=config)` calls the mock directly. Gazebo's assertions use `call_args.kwargs["config"]` and `call_count`, so both work; re-run gazebo's `test_web_crawler_service.py` against the pruned fork before merging.
7. **`CrawlerRunConfig.clone()`** round-trips through `to_dict()` then `from_kwargs()` (async_configs.py:1528, 1407, 1612). Any param removal needs both edited, else `TypeError` on clone. Note `from_kwargs` default for `word_count_threshold` is 200 whereas `__init__` default is `MIN_WORD_THRESHOLD`: a gazebo config that omits it and is cloned (fallback-UA retry) gets 200, not the original default; pre-existing quirk, keep identical.
8. **Serialization (`dump/load`, `to_serializable_dict/from_serializable_dict`)**: `from_serializable_dict` resolves types with `getattr(crawl4ai, name)`. After shrinking `__init__.py`, objects like `LXMLWebScrapingStrategy` or `PruningContentFilter` can no longer be deserialized. Gazebo never uses `dump/load`; either keep the functions with the reduced export set or delete `dump/load` entirely (recommended: delete; `to_dict` stays for `clone`). `to_serializable_dict` is also not used by `clone`.
9. **`CrawlResult` / `AsyncCrawlResponse` shape**: keep unused fields optional rather than removing, so gazebo's `AsyncCrawlResponse(html, response_headers, status_code)` (test helper) and any pickled/cached `CrawlResult` stay valid. Removing `ssl_certificate` typing from `models.py` is the one needed change.
10. **`MemoryAdaptiveDispatcher` + `RateLimiter(base_delay=(1,3))`** adds a random 1-3 s delay per domain between requests in `arun_many`; keep exactly as is. Dropping `psutil` would change throttling behaviour, so I recommend keeping it.
11. **Headless page-close quirk** (`async_crawler_strategy.py:990-1010`): in headless mode the page is only closed when more than one page exists. Pre-existing; not changed by pruning, but worth a follow-up because gazebo runs many crawls per process.
12. **`js_code`, `css_selector`, `wait_for`, `excluded_selector`, `target_elements`, `only_text`, `session_id`** are unused by gazebo but are exercised by `tests/` (`css_selector`, `js_code`). Decision needed. Recommendation: delete `js_code` (large: `robust_execute_user_script` + `execute_user_script` ~250 lines, plus c4a/lark), `wait_for`/`smart_wait`, `session_id`, `scan_full_page`, virtual scroll. Keeping `css_selector` and `excluded_selector`/`target_elements` is cheap (they live in `_scrap`, ~100 lines, no extra deps) and was recently touched upstream (#96); keep unless you want maximum reduction.
13. **`~/.crawl4ai` directories** are still created by `AsyncWebCrawler.__init__` (lines 150-152) and `AsyncLogger` file logging; consider removing (`cache` dir is no longer used since `async_database` is gone). Gazebo passes its own logger, and `os.makedirs` is harmless but could fail on read-only filesystems; low risk.
14. **Name collisions to watch while trimming**: `extraction_strategy.py` does `from .utils import *` and `from .models import *`; deleting it is clean. `async_dispatcher` imports `CrawlStatus` for the monitor; remove together with `components`.
15. **Import-time side effects/perf**: `CrawlerRunConfig.__init__` signature has instantiated defaults (`RegexChunking()`, `DefaultMarkdownGenerator()`), evaluated at import. Removing `chunking_strategy` removes one; `markdown_generator=DefaultMarkdownGenerator()` default is a shared mutable instance across configs (pre-existing).

### Suggested PR sequence (each keeps `import crawl4ai` working and `uv run pytest tests/` green)

PR 1: Delete dead and unreferenced files, no behaviour change.
- Delete `async_crawler_strategy.back.py`, `async_database.py`, `migrations.py`, `install.py`, `model_loader.py`, `cli.py`, `browser_profiler.py`, `adaptive_crawler.py`, `processors/`, `html2text/cli.py`, `html2text/__main__.py`, `unvalidated_tests/`, `docs/`, `mkdocs.yml`, `prompts/`, root markdown files, `.env.txt`, `.claude/`, `setup.py`, `setup.cfg`, `cliff.toml`, issue templates.
- Edit `__init__.py` to drop `AdaptiveCrawler*`, `BrowserProfiler`.
- Edit `browser_manager.py` only enough to drop the lazy `BrowserProfiler` shims (435-557) (or defer to PR 2).
- Remove `[project.scripts]` entries and trim `MANIFEST.in`. Dependencies untouched except those with zero remaining importers: `aiosqlite`, `shapely`, `nltk` (via model_loader), `humanize`, `click`, `PyYAML`, `anyio` (cli), `tiktoken` (only after removing the unused import in `table_extraction.py:15`).
- Test deletions: none needed.

PR 2: Make `__init__.py` gazebo-shaped and delete feature modules (the large one).
- Delete: `chunking_strategy, content_filter_strategy, extraction_strategy, deep_crawling/, script/, proxy_strategy.py (keep ProxyConfig), table_extraction.py, components/, types.py, ssl_certificate.py, user_agent_generator.py (move `generate_client_hints` into `async_configs.py`)`.
- Edit the dependents in lock-step: `async_configs.py` (imports + params + `to_dict/from_kwargs/clone`), `async_webcrawler.py` (decorator, extraction block, `aseed_urls`, proxy rotation, table/fit_html), `async_dispatcher.py` (monitor, `types`), `markdown_generation_strategy.py` (content filter), `content_scraping_strategy.py` (table block, `fetch_image_file_size`), `models.py` (ssl field, `TokenUsage`, `DisplayMode`, stats), `config.py` (constants), `utils.py` (functions, in the "Delete everything else" list).
- Delete tests: `test_safe_eval.py`, `test_virtual_scroll.py`, plus the `js_code`/`css_selector`/`url_matcher` tests if those are removed here.
- Gate: run the whole `tests/` plus gazebo's `tests/python/e2e/services/test_web_crawler_service.py` and `tests/python/e2e/models/test_supplier_llm_location.py` against the branch via a temporary `rev` bump.

PR 3: Trim the big runtime files (browser and crawler strategy).
- `async_crawler_strategy.py` (remove HTTP strategy, screenshot/pdf/mhtml, scroll/virtual scroll, js execution, console/network capture, downloads, PIL/aiofiles/chardet/aiohttp imports), `browser_manager.py` (ManagedBrowser, stealth, patchright, persistent context, sessions), `browser_adapter.py` (delete and inline `page.evaluate`, or drop `UndetectedAdapter`), `async_url_seeder.py` (sitemap/CC/BM25), `js_snippet/` (unused scripts), `async_configs.py` second pass (browser/run params tied to removed code), `BrowserConfig` UA generation.
- Run `ruff check --select F401,F841` to catch orphaned imports. Tests: remove `test_javascript_execution` etc.

PR 4: Dependencies and packaging.
- Rewrite `pyproject.toml` `dependencies` to the 9-entry list in section 3, delete `optional-dependencies`, regenerate `uv.lock`, drop `libjpeg-dev`/`libgeos-dev` from `pr-ci.yaml`, rewrite `README.md`. Do this last (after PR 3) so each earlier PR still has every dependency its importers need; or fold the safe removals into PR 1 and 2 as noted. Then bump gazebo's `rev`, regenerate gazebo's `uv.lock`, and confirm gazebo declares `pydantic` in its runtime dependencies (currently only in the dev group) and `pyyaml` if its scripts need it.

Expected end state: about 20 modules, roughly 9,000-10,000 lines (from 35,232), 9 runtime dependencies (from 34 plus 6 extras), no console scripts.

### Verification suggestion before PR 2/3
Use `scratchpad/trace_run.py` (run from the repo root with `uv run python`) as a smoke test: it exercises robots, cache hit, link preview with head fetch, markdown, `arun_many`, and prints `sys.modules`. After pruning, its `IMPORTED_FINAL` list should show only the ~20 kept modules and its third-party list should contain none of the REMOVE packages.
