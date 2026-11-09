from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from typing import Any
from urllib.parse import urljoin, urlparse

import brotli
import httpx
from lxml import etree
from lxml import html as lxml_html

from crawl4ai.cache_client import (
    DEFAULT_CACHE_TTL_SECONDS,
    URL_SEEDER_CACHE_KEY_PREFIX,
    CacheClient,
    NoCacheClient,
)

from .async_logger import AsyncLoggerBase


def _parse_head(src: str) -> dict[str, Any]:
    try:
        if isinstance(src, str):
            src = src.encode("utf-8", "replace")
        doc = lxml_html.fromstring(src)
    except (ValueError, etree.ParserError):
        return {}
    info: dict[str, Any] = {
        "title": (doc.find(".//title").text or "").strip()
        if doc.find(".//title") is not None else None,
        "charset": None,
        "meta": {}, "link": {}, "jsonld": []
    }
    for el in doc.xpath(".//meta"):
        k = el.attrib.get("name") or el.attrib.get(
            "property") or el.attrib.get("http-equiv")
        if k:
            info["meta"][k.lower()] = el.attrib.get("content", "")
        elif "charset" in el.attrib:
            info["charset"] = el.attrib["charset"].lower()
    for el in doc.xpath(".//link"):
        rel_attr = el.attrib.get("rel", "")
        if not rel_attr:
            continue
        rel_values = rel_attr.lower().split()
        entry = {a: el.attrib[a] for a in (
            "href", "as", "type", "hreflang") if a in el.attrib}
        for rel in rel_values:
            info["link"].setdefault(rel, []).append(entry)
    for script in doc.xpath('.//script[@type="application/ld+json"]'):
        if script.text:
            try:
                jsonld_data = json.loads(script.text.strip())
                info["jsonld"].append(jsonld_data)
            except json.JSONDecodeError:
                pass
    html_elem = doc.find(".//html")
    if html_elem is not None:
        info["lang"] = html_elem.attrib.get("lang", "")
    return info


class AsyncUrlSeeder:
    def __init__(
        self,
        cache_client: CacheClient = NoCacheClient(),
        ttl_seconds: int | None = DEFAULT_CACHE_TTL_SECONDS,
        client: httpx.AsyncClient | None = None,
        logger: AsyncLoggerBase | None = None,
    ):
        self.cache_client = cache_client
        self.ttl_seconds = ttl_seconds
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(http2=True, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) +AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        })
        self.logger = logger

    def _log(self, level: str, message: str, tag: str = "URL_SEED", **kwargs: Any):
        if self.logger:
            log_method = getattr(self.logger, level, None)
            if log_method:
                log_method(message=message, tag=tag,
                           params=kwargs.get('params', {}))

    def _get_cache_key(self, kind: str, url: str) -> str:
        h = hashlib.sha1(url.encode()).hexdigest()
        return f"{URL_SEEDER_CACHE_KEY_PREFIX}{kind}:{h}"

    async def _cache_get(self, kind: str, url: str) -> dict[str, Any] | None:
        cached = self.cache_client.get(
            key=self._get_cache_key(kind, url)
        )
        return json.loads(cached) if cached else None

    async def _cache_set(self, kind: str, url: str, data: dict[str, Any]) -> None:
        self.cache_client.set(
            key=self._get_cache_key(kind, url),
            value=json.dumps(data, separators=(",", ":")),
            ttl_seconds=int(self.ttl_seconds)
        )

    async def extract_head_for_urls(
        self,
        urls: list[str],
        concurrency: int = 10,
        timeout: int = 5,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        self._log("info", "Starting head extraction for {count} custom URLs",
                  params={"count": len(urls)}, tag="URL_SEED")

        queue_size = min(10000, max(1000, concurrency * 100))
        queue = asyncio.Queue(maxsize=queue_size)
        producer_done = asyncio.Event()
        seen: set[str] = set()

        results: list[dict[str, Any]] = []

        async def producer():
            try:
                for url in urls:
                    if url in seen:
                        self._log("debug", "Skipping duplicate URL: {url}",
                                  params={"url": url}, tag="URL_SEED")
                        continue
                    seen.add(url)
                    await queue.put(url)
            finally:
                producer_done.set()

        async def worker(res_list: list[dict[str, Any]]):
            while True:
                try:
                    url = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    if producer_done.is_set() and queue.empty():
                        break
                    continue

                try:
                    await self._validate(url, res_list, timeout=timeout)
                except Exception as e:
                    self._log("error", "Failed to process URL {url}: {error}",
                              params={"url": url, "error": str(e)}, tag="URL_SEED")
                    res_list.append({
                        "url": url,
                        "status": "failed",
                        "head_data": {},
                        "error": str(e)
                    })
                finally:
                    queue.task_done()

        producer_task = asyncio.create_task(producer())

        worker_tasks = []
        for _ in range(concurrency):
            worker_task = asyncio.create_task(worker(results))
            worker_tasks.append(worker_task)

        await producer_task

        await queue.join()

        for task in worker_tasks:
            task.cancel()

        await asyncio.gather(*worker_tasks, return_exceptions=True)

        if score_threshold is not None:
            results = [r for r in results if r.get("relevance_score", 0) >= score_threshold]

        self._log("info", "Completed head extraction for {count} URLs, {success} successful",
                  params={
                      "count": len(urls),
                      "success": len([r for r in results if r.get("status") == "valid"])
                  }, tag="URL_SEED")

        return results

    async def _validate(self, url: str, res_list: list[dict[str, Any]], timeout: int):
        if self._is_nonsense_url(url):
            self._log("debug", "Filtered out nonsense URL: {url}",
                      params={"url": url}, tag="URL_SEED")
            return

        cached = await self._cache_get("head", url)
        if cached:
            res_list.append(cached)
            return

        self._log("debug", "Fetching head for {url}", params={
                  "url": url}, tag="URL_SEED")
        ok, html, final = await self._fetch_head(url, timeout)
        status = "valid" if ok else "not_valid"
        self._log("info" if ok else "warning", "HEAD {status} for {final_url}",
                  params={"status": status.upper(), "final_url": final or url}, tag="URL_SEED")
        head_data = await asyncio.to_thread(_parse_head, html) if ok else {}
        entry = {
            "url": final or url,
            "status": status,
            "head_data": head_data,
        }

        await self._cache_set("head", url, entry)
        res_list.append(entry)

    async def _fetch_head(
        self,
        url: str,
        timeout: int,
        max_redirects: int = 5,
        max_bytes: int = 65_536,
        chunk_size: int = 4096,
    ):
        for _ in range(max_redirects+1):
            try:
                async with self.client.stream(
                    "GET",
                    url,
                    timeout=timeout,
                    headers={"Accept-Encoding": "identity"},
                    follow_redirects=False,
                ) as r:

                    if r.status_code in (301, 302, 303, 307, 308):
                        location = r.headers.get("Location")
                        if location:
                            url = urljoin(url, location)
                            self._log("debug", "Redirecting from {original_url} to {new_url}",
                                      params={"original_url": r.url, "new_url": url}, tag="URL_SEED")
                            continue
                        self._log("warning", "Redirect status {status_code} but no Location header for {url}",
                                  params={"status_code": r.status_code, "url": r.url}, tag="URL_SEED")
                        return False, "", str(r.url)

                    if not (200 <= r.status_code < 400):
                        self._log("warning", "Non-success status {status_code} when fetching head for {url}",
                                  params={"status_code": r.status_code, "url": r.url}, tag="URL_SEED")
                        return False, "", str(r.url)

                    buf = bytearray()
                    async for chunk in r.aiter_bytes(chunk_size):
                        buf.extend(chunk)
                        low = buf.lower()
                        if b"</head>" in low or len(buf) >= max_bytes:
                            await r.aclose()
                            break

                    enc = r.headers.get("Content-Encoding", "").lower()
                    try:
                        if enc == "gzip" and buf[:2] == b"\x1f\x8b":
                            buf = gzip.decompress(buf)
                        elif enc == "br" and buf[:4] == b"\x8b\x6c\x0a\x1a":
                            buf = brotli.decompress(buf)
                        elif enc in {"gzip", "br"}:
                            self._log(
                                "debug",
                                "Skipping bogus {encoding} for {url}",
                                params={"encoding": enc, "url": r.url},
                                tag="URL_SEED",
                            )
                    except Exception as e:
                        self._log(
                            "warning",
                            "Decompression error for {url} ({encoding}): {error}",
                            params={"url": r.url,
                                    "encoding": enc, "error": str(e)},
                            tag="URL_SEED",
                        )

                    idx = buf.lower().find(b"</head>")
                    if idx == -1:
                        self._log("debug", "No </head> tag found in initial bytes of {url}",
                                  params={"url": r.url}, tag="URL_SEED")
                        html_bytes = buf if len(buf) < 10240 else buf[:10240]
                    else:
                        html_bytes = buf[:idx+7]

                    try:
                        html = html_bytes.decode("utf-8", "replace")
                    except Exception as e:
                        self._log(
                            "warning",
                            "Failed to decode head content for {url}: {error}",
                            params={"url": r.url, "error": str(e)},
                            tag="URL_SEED",
                        )
                        html = html_bytes.decode("latin-1", "replace")

                    return True, html, str(r.url)

            except httpx.RequestError as e:
                self._log("debug", "Fetch head network error for {url}: {error}",
                          params={"url": url, "error": str(e)}, tag="URL_SEED")
                return False, "", url

        self._log("warning", "Exceeded max redirects ({max_redirects}) for {url}",
                  params={"max_redirects": max_redirects, "url": url}, tag="URL_SEED")
        return False, "", url

    def _is_nonsense_url(self, url: str) -> bool:
        url_lower = url.lower()

        parsed = urlparse(url)
        path = parsed.path.lower()

        if path.endswith(('/robots.txt', '/sitemap.xml', '/sitemap_index.xml')):
            return True

        if '/sitemap' in path and path.endswith(('.xml', '.xml.gz', '.txt')):
            return True

        utility_files = [
            'ads.txt', 'humans.txt', 'security.txt', '.well-known/security.txt',
            'crossdomain.xml', 'browserconfig.xml', 'manifest.json',
            'apple-app-site-association', '.well-known/apple-app-site-association',
            'favicon.ico', 'apple-touch-icon.png', 'android-chrome-192x192.png'
        ]
        if any(path.endswith(f'/{file}') for file in utility_files):
            return True

        path_parts = path.split('/')
        if any(part.startswith('.') for part in path_parts if part):
            return True

        non_content_paths = [
            '/wp-admin', '/wp-includes', '/wp-content/uploads',
            '/admin', '/login', '/signin', '/signup', '/register',
            '/checkout', '/cart', '/account', '/profile',
            '/search', '/404', '/error',
            '/.git', '/.svn', '/.hg',
            '/cgi-bin', '/scripts', '/includes'
        ]
        if any(ncp in path for ncp in non_content_paths):
            return True

        if any(pattern in url_lower for pattern in ['?print=', '&print=', '/print/', '_print.']):
            return True

        if len(path.strip('/')) < 3 and path not in ['/', '/en', '/de', '/fr', '/es', '/it']:
            return True

        return False

    async def close(self):
        if self._owns_client and self.client:
            await self.client.aclose()
            self._log("debug", "Closed HTTP client", tag="URL_SEED")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False
