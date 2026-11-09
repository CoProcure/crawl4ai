---
title: Crawl4ai prune
status: open
tags: [crawl4ai, dependencies]
branch: main
created: 2026-11-09
updated: 2026-11-09
---
# Crawl4ai prune

Continues `.apl/plan/2026-11-09-crawl4ai-prune.md`; research in `.apl/research/2026-11-09-crawl4ai-prune.md`.

## Context

The CoProcure crawl4ai fork exists only to serve gazebo's web scraping (`brochure/services/web_crawler_service.py` and the supplier and agency scrapers in gazebo). PR #113 (sc-36999, merge commit `fe210e1`) cut it down to the paths gazebo runs.

## Done

- **Size:** 59 modules and 35,232 lines became 19 modules and 4,608 lines.
- **Dependencies:** 33 runtime entries became 10: `aiohttp`, `brotli`, `html2text==2024.2.26`, `httpx[http2]`, `lxml`, `playwright`, `protego`, `psutil`, `pydantic`, `rich`. Optional extras, console scripts, `setup.py`, docs, and upstream project files are gone.
- **Public API (`crawl4ai/__init__.py`):** exports only what gazebo imports: `AsyncWebCrawler`, `AsyncLogger`, `BrowserConfig`, `CrawlerRunConfig`, `LinkPreviewConfig`, `CacheMode`, `CrawlResult`, `DefaultMarkdownGenerator`. Gazebo also imports `crawl4ai.async_crawler_strategy.AsyncPlaywrightCrawlerStrategy`, `crawl4ai.async_logger.LogLevel`, `crawl4ai.cache_client`, `crawl4ai.cache_context`, and `crawl4ai.models.AsyncCrawlResponse` by module path.
- **Removed features:** extraction strategies, content filters, chunking, deep crawling, adaptive crawling, the C4A script language, table extraction, the HTTP-only strategy, screenshots/PDF/MHTML, JS execution, sessions, stealth/patchright/managed browsers, proxy rotation, SSL certificates, the CLI, the SQLite cache, sitemap/Common Crawl seeding, and BM25 link scoring. Also the run options `js_code`, `wait_for`, `session_id`, `css_selector`, `excluded_selector`, `target_elements`, `scan_full_page`, and virtual scroll.
- **html2text:** unvendored. `crawl4ai/_html_to_markdown.py` holds `CustomHTML2Text` on top of the PyPI package. Its markdown output was verified identical to the vendored copy on 114 cases.
- **Tests:** all 44 tests in `tests/` run against a local server (`tests/conftest.py`), with no network.

## Next steps (in gazebo)

1. Bump the `crawl4ai` `rev` in gazebo's `pyproject.toml` to `fe210e1` and run `uv lock`.
2. Make `pydantic` a gazebo runtime dependency. Today it's only in gazebo's dev group, and gazebo gets it at runtime through crawl4ai.
3. Bug: link head data is always empty. `LinkPreviewConfig(concurrency=3, score_threshold=0.2)` in `brochure/models/supplier_scraper/crawler.py` has no `query`, so no link gets a relevance score, and the threshold drops every link's `head_data`. As a result, `categorize_webpage_urls.py` sees `{}`. This was already true before the prune. Removing `score_threshold` probably fixes it.
4. Bug: the `Accept-Language` header is never sent. `AsyncPlaywrightCrawlerStrategy.set_custom_headers` stores `self.headers`, and nothing reads it. Either apply the headers to the browser context in the fork, or set them from gazebo's `on_page_context_created` hook.

## Possible later work in this repo

- `BrowserConfig` and `CrawlerRunConfig` still carry proxy, viewport, cookie, locale, and timezone options that `browser_manager.py` reads but gazebo never sets.
- To unpin `html2text`, upgrade it and accept the change: 2025.4.15 adds one space of indentation to nested ordered lists. Rerun an equivalence check like the scratch harness used in #113 before deciding.

## Gotchas

- Gazebo's tests patch `crawl4ai.async_webcrawler.AsyncPlaywrightCrawlerStrategy` and `AsyncWebCrawler.arun`/`arun_many`, so keep those names where they are.
- `CrawlerRunConfig.clone()` round-trips through `to_dict()` and `from_kwargs()`. Add or remove a parameter in `__init__`, `to_dict`, and `from_kwargs` together. Gazebo's 403 retry with a fallback user agent depends on `clone`.
- `from_kwargs` defaults `word_count_threshold` to 200, while `__init__` defaults to `MIN_WORD_THRESHOLD`. This quirk was preserved on purpose.
- Links to `127.0.0.1:<port>` are classified as external, because `is_external_url` compares the host including the port. Local-server tests read both link lists.
- Running crawl4ai locally: unset `UV_ENV_FILE` (it breaks `uv run` here) and use `--python 3.13`. Python 3.15 is picked by default and fails to build some wheels.
- To test gazebo against a crawl4ai checkout without touching gazebo's environment, run from gazebo with `PYTHONPATH=<crawl4ai checkout> uv run --no-sync pytest ...`.
