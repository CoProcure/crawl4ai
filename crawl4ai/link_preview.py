"""
Link Extractor for Crawl4AI

Extracts head content from links discovered during crawling using URLSeeder's
efficient parallel processing and caching infrastructure.
"""

import fnmatch
from typing import Any

from crawl4ai.cache_client import CacheClient, NoCacheClient

from .async_configs import CrawlerRunConfig
from .async_logger import AsyncLogger
from .async_url_seeder import AsyncUrlSeeder
from .models import Link, Links
from .utils import calculate_total_score


class LinkPreview:
    """
    Extracts head content from links using URLSeeder's parallel processing infrastructure.
    
    This class provides intelligent link filtering and head content extraction with:
    - Pattern-based inclusion/exclusion filtering
    - Parallel processing with configurable concurrency
    - Caching for performance
    - Memory-safe processing for large link sets
    """
    
    def __init__(self, cache_client: CacheClient = NoCacheClient(), logger: AsyncLogger | None = None):
        """
        Initialize the LinkPreview.
        
        Args:
            logger: Optional logger instance for recording events
        """
        self.cache_client = cache_client
        self.logger = logger
        self.seeder: AsyncUrlSeeder | None = None
        self._owns_seeder = False
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def start(self):
        """Initialize the URLSeeder instance."""
        if not self.seeder:
            self.seeder = AsyncUrlSeeder(cache_client=self.cache_client, logger=self.logger)
            await self.seeder.__aenter__()
            self._owns_seeder = True
    
    async def close(self):
        """Clean up resources."""
        if self.seeder and self._owns_seeder:
            await self.seeder.__aexit__(None, None, None)
            self.seeder = None
            self._owns_seeder = False
    
    def _log(self, level: str, message: str, tag: str = "LINK_EXTRACT", **kwargs):
        """Helper method to safely log messages."""
        if self.logger:
            log_method = getattr(self.logger, level, None)
            if log_method:
                log_method(message=message, tag=tag, params=kwargs.get('params', {}))
    
    async def extract_link_heads(
        self, 
        links: Links, 
        config: CrawlerRunConfig
    ) -> Links:
        """
        Extract head content for filtered links and attach to Link objects.
        
        Args:
            links: Links object containing internal and external links
            config: CrawlerRunConfig with link_preview_config settings
            
        Returns:
            Links object with head_data attached to filtered Link objects
        """
        link_config = config.link_preview_config
        
        # Ensure seeder is initialized
        await self.start()
        
        # Filter links based on configuration
        filtered_urls = self._filter_links(links, link_config)
        
        if not filtered_urls:
            self._log("info", "No links matched filtering criteria")
            return links
        
        self._log("info", "Extracting head content for {count} filtered links",
                  params={"count": len(filtered_urls)})
        
        # Extract head content using URLSeeder
        head_results = await self._extract_heads_parallel(filtered_urls, link_config)
        
        # Merge results back into Link objects
        updated_links = self._merge_head_data(links, head_results, config)
        
        self._log("info", "Completed head extraction for links, {success} successful",
                  params={"success": len([r for r in head_results if r.get("status") == "valid"])})
        
        return updated_links
    
    def _filter_links(self, links: Links, link_config: dict[str, Any]) -> list[str]:
        """
        Filter links based on configuration parameters.
        
        Args:
            links: Links object containing internal and external links
            link_config: Configuration dictionary for link extraction
            
        Returns:
            List of filtered URL strings
        """
        filtered_urls = []
        
        # Include internal links if configured
        if link_config.include_internal:
            filtered_urls.extend([link.href for link in links.internal if link.href])
            self._log("debug", "Added {count} internal links",
                      params={"count": len(links.internal)})
        
        # Include external links if configured
        if link_config.include_external:
            filtered_urls.extend([link.href for link in links.external if link.href])
            self._log("debug", "Added {count} external links",
                      params={"count": len(links.external)})
        
        # Apply include patterns
        include_patterns = link_config.include_patterns
        if include_patterns:
            filtered_urls = [
                url for url in filtered_urls
                if any(fnmatch.fnmatch(url, pattern) for pattern in include_patterns)
            ]
            self._log("debug", "After include patterns: {count} links remain",
                      params={"count": len(filtered_urls)})
        
        # Apply exclude patterns
        exclude_patterns = link_config.exclude_patterns
        if exclude_patterns:
            filtered_urls = [
                url for url in filtered_urls
                if not any(fnmatch.fnmatch(url, pattern) for pattern in exclude_patterns)
            ]
            self._log("debug", "After exclude patterns: {count} links remain",
                      params={"count": len(filtered_urls)})
        
        # Limit number of links
        max_links = link_config.max_links
        if max_links > 0 and len(filtered_urls) > max_links:
            filtered_urls = filtered_urls[:max_links]
            self._log("debug", "Limited to {max_links} links",
                      params={"max_links": max_links})
        
        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in filtered_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        
        self._log("debug", "Final filtered URLs: {count} unique links",
                  params={"count": len(unique_urls)})
        
        return unique_urls
    
    async def _extract_heads_parallel(
        self,
        urls: list[str],
        link_config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        concurrency = link_config.concurrency

        if link_config.verbose:
            self._log("info", "Starting batch processing: {total} links with {concurrency} concurrent workers",
                      params={"total": len(urls), "concurrency": concurrency})

        results = await self.seeder.extract_head_for_urls(
            urls=urls,
            concurrency=concurrency,
            timeout=link_config.timeout,
            score_threshold=link_config.score_threshold,
        )

        if link_config.verbose:
            successful = len([r for r in results if r.get("status") == "valid"])
            self._log("info", "Batch processing completed: {completed}/{total} processed, {successful} successful, {failed} failed",
                      params={
                          "completed": len(results),
                          "total": len(urls),
                          "successful": successful,
                          "failed": len(results) - successful
                      })

        return results

    def _merge_head_data(
        self,
        original_links: Links,
        head_results: list[dict[str, Any]],
        config: CrawlerRunConfig
    ) -> Links:
        url_to_head_info = {
            result["url"]: result for result in head_results if result.get("url")
        }
        score_links_enabled = getattr(config, "score_links", False)

        def merge(link: Link) -> Link:
            head_info = url_to_head_info.get(link.href)
            if head_info is None:
                return link

            return Link(
                href=link.href,
                text=link.text,
                title=link.title,
                base_domain=link.base_domain,
                head_data=head_info.get("head_data", {}),
                head_extraction_status=head_info.get("status", "unknown"),
                head_extraction_error=head_info.get("error"),
                intrinsic_score=link.intrinsic_score,
                total_score=calculate_total_score(
                    intrinsic_score=link.intrinsic_score,
                    score_links_enabled=score_links_enabled,
                ),
            )

        return Links(
            internal=[merge(link) for link in original_links.internal],
            external=[merge(link) for link in original_links.external],
        )
