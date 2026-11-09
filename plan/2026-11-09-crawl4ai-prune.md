---
title: Crawl4ai prune
status: active
tags: [crawl4ai, dependencies]
created: 2026-11-09
updated: 2026-11-09
last_pr: 113
next_step: Mark PR 113 ready for review, merge it, then bump the crawl4ai rev in gazebo and fix the two gazebo findings
---
# Implementation Plan: Prune crawl4ai fork to gazebo's usage

**Status**: In Progress
**Created**: 2026-11-09
**Research**: `.apl/research/2026-11-09-crawl4ai-prune.md` (module, dependency, and test verdicts; traced runtime paths; risks)

## Goal

Reduce the CoProcure crawl4ai fork to the code paths gazebo exercises:
`AsyncWebCrawler` (start, close, arun, non-streaming arun_many, `robots_parser.can_fetch`), `BrowserConfig(user_agent)`,
`CrawlerRunConfig` (the kwargs gazebo passes, plus `clone`), `CacheMode` and `CacheClient` with its key prefixes, `LinkPreviewConfig` and link scoring,
`DefaultMarkdownGenerator` (html2text with citations), `AsyncPlaywrightCrawlerStrategy` (`set_custom_headers`, `set_hook`),
`AsyncLogger`/`LogLevel`, `CrawlResult`/`AsyncCrawlResponse`.

End state: about 20 modules and 9,000–10,000 lines (from 59 modules and 35,232 lines), 9 runtime dependencies
(from 34 plus 6 optional extras), no console scripts.

## PR Dependency Graph

main ─── PR 1: sc-36999 (prune to gazebo usage)

The user chose one PR over three stacked ones. Build it as a series of commits in the order below, so a reviewer
can read it commit by commit and a regression can be bisected. Each commit should keep `import crawl4ai` working
and `uv run pytest tests/` green.

## PRs

### PR 1: Prune crawl4ai to gazebo's usage
- **Ticket:** sc-36999
- **Branch:** `sc-36999-prune-to-gazebo-usage` (from `main`)
- **Status:** Draft PR — [#113](https://github.com/CoProcure/crawl4ai/pull/113)
- **Commit 1: dead code and upstream project files**
  - Delete `async_crawler_strategy.back.py`, `async_database.py`, `migrations.py`, `install.py`, `model_loader.py`, `cli.py`, `browser_profiler.py`, `adaptive_crawler.py`, `processors/`, `html2text/cli.py`, `html2text/__main__.py`.
  - Delete `unvalidated_tests/`, `docs/`, `mkdocs.yml`, `prompts/`, `README-first.md`, `CHANGELOG.md`, `cliff.toml`, `ROADMAP.md`, `JOURNAL.md`, `MISSION.md`, `PROGRESSIVE_CRAWLING.md`, `CONTRIBUTORS.md`, `SPONSORS.md`, `CODE_OF_CONDUCT.md`, `.env.txt`, `.github` issue and discussion templates, `setup.py`, `setup.cfg`.
  - Rewrite `README.md` as a short description of the fork. Keep `LICENSE`.
  - Remove `[project.scripts]`, trim `MANIFEST.in`, drop the browser-profiler shims in `browser_manager.py` and the removed exports from `__init__.py`.
- **Commit 2: gazebo-shaped public API, delete feature modules**
  - `__init__.py` exports only gazebo's names.
  - Delete `chunking_strategy`, `content_filter_strategy`, `extraction_strategy`, `deep_crawling/`, `script/`, `proxy_strategy` (keep `ProxyConfig` only if `BrowserConfig` still needs it), `table_extraction`, `components/`, `types`, `ssl_certificate`, `user_agent_generator` (move `generate_client_hints` into `async_configs.py`; it produces the `sec-ch-ua` header gazebo sends today).
  - Edit dependents together: `async_configs.py` (parameters plus `to_dict`, `from_kwargs`, and `clone` in lockstep; delete `dump`/`load`), `async_webcrawler.py` (deep-crawl decorator, extraction, proxy rotation, table and `fit_html` work), `async_dispatcher.py` (monitor), `markdown_generation_strategy.py`, `content_scraping_strategy.py`, `models.py` (keep unused `CrawlResult` fields optional), `config.py` (drop `load_dotenv()`), `utils.py`.
  - Delete the run options gazebo does not use: `js_code`, `wait_for`/smart wait, `session_id`, `css_selector`, `excluded_selector`, `target_elements`, `scan_full_page`, virtual scroll.
- **Commit 3: browser and crawler internals**
  - `async_crawler_strategy.py`: delete the HTTP strategy, screenshot/PDF/MHTML, scrolling, JS execution, console and network capture, downloads.
  - `browser_manager.py`: delete `ManagedBrowser`, stealth, patchright, persistent contexts, sessions.
  - Delete `browser_adapter.py` (inline the one `page.evaluate` call). Trim `async_url_seeder.py` to `extract_head_for_urls` and what it calls. Delete unused `js_snippet/*.js`.
- **Commit 4: dependencies and CI**
  - `dependencies` = `aiohttp`, `brotli`, `httpx[http2]`, `lxml`, `playwright`, `protego`, `psutil`, `pydantic`, `rich`. Delete `optional-dependencies`. Regenerate `uv.lock`.
  - CI: drop `libjpeg-dev` and `libgeos-dev` from `pr-ci.yaml`.
- **Key files:** `crawl4ai/__init__.py`, `async_configs.py`, `async_webcrawler.py`, `async_crawler_strategy.py`, `browser_manager.py`, `async_url_seeder.py`, `utils.py`, `models.py`, `pyproject.toml`.
- **Must preserve:**
  - `crawl4ai.async_webcrawler.AsyncPlaywrightCrawlerStrategy` stays a module-level name, because gazebo's tests patch it there.
  - `CrawlerRunConfig.clone(user_agent=..., cache_mode=...)` keeps every gazebo kwarg, including the existing `from_kwargs` default of `word_count_threshold=200`.
  - `MemoryAdaptiveDispatcher` and `RateLimiter` throttling in `arun_many` stays unchanged.
  - Link-preview head fetches don't use gazebo's cache client today; keep that behavior.
- **Tests:**
  - Delete `test_safe_eval.py`, `test_virtual_scroll.py`, and the tests for removed options (`js_code`, `css_selector`, `url_matcher` if it goes, `get_home_folder` if it goes).
  - Add tests: `clone` preserves gazebo's kwargs; `arun_many` returns a list of `CrawlResult`; `robots_parser.can_fetch` allows and disallows correctly; link preview attaches `head_data` and `total_score` to internal links. Use a local server, following the pattern from #96.
- **Acceptance criteria:**
  - [ ] `uv run pytest tests/` passes
  - [ ] `ruff check --select F401,F841 crawl4ai` is clean
  - [ ] The research smoke test (`trace_run.py`) runs, and no removed package appears in `sys.modules`
  - [ ] Gazebo's `tests/python/e2e/services/test_web_crawler_service.py` and `tests/python/e2e/models/test_supplier_llm_location.py` pass against this branch

## Follow-up in gazebo (after merge)
- Bump the `crawl4ai` `rev` in gazebo's `pyproject.toml` and regenerate gazebo's `uv.lock`.
- Gazebo needs `pydantic` as a runtime dependency (today it only arrives through crawl4ai).

## Progress Tracking

- [x] sc-36999: Implementation complete
- [x] sc-36999: PR created (#113)
- [ ] sc-36999: Merged

## Review Patterns

## Notes and Decisions

**2026-11-09**: The user chose a single PR instead of three stacked ones, and chose to delete every unused run option, `css_selector` included.
**2026-11-09**: `.claude/settings.local.json` (the upstream author's tracked local settings) stays out of scope; the user can remove it.
**2026-11-09**: Implementation landed as draft #113: 23 modules and 6,283 lines (from 59 and 35,232), 9 runtime dependencies (from 33 entries). Commits 2 and 3 merged in practice, because the strategy, browser manager, and dispatcher read the run options removed in commit 2. `SeedingConfig` and `LinkPreviewConfig.query` (with BM25) were removed too. Gazebo's two crawl4ai test files pass against the branch.
**2026-11-09**: Finding for gazebo: `LinkPreviewConfig(score_threshold=0.2)` without a `query` drops every link's `head_data`, because no link gets a relevance score to compare. This already happens on `main`; the prune keeps it. Gazebo should decide whether it wants `score_threshold`.
**2026-11-09**: Refactor pass (14ae934) removed leftover fields, methods, and `CrawlResult` fields for removed features, and made every test local (32 tests). CI is green on 14ae934.
**2026-11-09**: Finding for gazebo: `AsyncPlaywrightCrawlerStrategy.set_custom_headers` only stores `self.headers`, and nothing reads it, so gazebo's `Accept-Language` header has never been sent. This is already true on `main`; the PR keeps the method because gazebo calls it.
**2026-11-09**: Possible later prune: the proxy, viewport, cookie, locale, and timezone options on `BrowserConfig` and `CrawlerRunConfig` are still read by `browser_manager.py`, but gazebo never sets them.
