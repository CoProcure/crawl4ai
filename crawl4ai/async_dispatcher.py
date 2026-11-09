import asyncio
import random
import time
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import psutil

from .async_configs import CrawlerRunConfig
from .models import (
    CrawlerTaskResult,
    CrawlResult,
    DomainState,
)
from .utils import get_true_memory_usage_percent

if TYPE_CHECKING:
    from .async_webcrawler import AsyncWebCrawler


class RateLimiter:
    def __init__(
        self,
        base_delay: tuple[float, float] = (1.0, 3.0),
        max_delay: float = 60.0,
        max_retries: int = 3,
        rate_limit_codes: list[int] = None,
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.rate_limit_codes = rate_limit_codes or [429, 503]
        self.domains: dict[str, DomainState] = {}

    def get_domain(self, url: str) -> str:
        return urlparse(url).netloc

    async def wait_if_needed(self, url: str) -> None:
        domain = self.get_domain(url)
        state = self.domains.get(domain)

        if not state:
            self.domains[domain] = DomainState()
            state = self.domains[domain]

        now = time.time()
        if state.last_request_time:
            wait_time = max(0, state.current_delay - (now - state.last_request_time))
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        # Random delay within base range if no current delay
        if state.current_delay == 0:
            state.current_delay = random.uniform(*self.base_delay)

        state.last_request_time = time.time()

    def update_delay(self, url: str, status_code: int) -> bool:
        domain = self.get_domain(url)
        state = self.domains[domain]

        if status_code in self.rate_limit_codes:
            state.fail_count += 1
            if state.fail_count > self.max_retries:
                return False

            # Exponential backoff with random jitter
            state.current_delay = min(
                state.current_delay * 2 * random.uniform(0.75, 1.25), self.max_delay
            )
        else:
            # Gradually reduce delay on success
            state.current_delay = max(
                random.uniform(*self.base_delay), state.current_delay * 0.75
            )
            state.fail_count = 0

        return True


class BaseDispatcher(ABC):
    def __init__(self, rate_limiter: RateLimiter | None = None):
        self.crawler = None
        self._domain_last_hit: dict[str, float] = {}
        self.concurrent_sessions = 0
        self.rate_limiter = rate_limiter

    @abstractmethod
    async def crawl_url(
        self,
        url: str,
        config: CrawlerRunConfig,
        task_id: str,
    ) -> CrawlerTaskResult:
        pass

    @abstractmethod
    async def run_urls(
        self,
        urls: list[str],
        crawler: "AsyncWebCrawler",
        config: CrawlerRunConfig,
    ) -> list[CrawlerTaskResult]:
        pass


class MemoryAdaptiveDispatcher(BaseDispatcher):
    def __init__(
        self,
        memory_threshold_percent: float = 90.0,
        critical_threshold_percent: float = 95.0,
        recovery_threshold_percent: float = 85.0,
        check_interval: float = 1.0,
        max_session_permit: int = 20,
        fairness_timeout: float = 600.0,
        memory_wait_timeout: float | None = 600.0,
        rate_limiter: RateLimiter | None = None,
    ):
        super().__init__(rate_limiter)
        self.memory_threshold_percent = memory_threshold_percent
        self.critical_threshold_percent = critical_threshold_percent
        self.recovery_threshold_percent = recovery_threshold_percent
        self.check_interval = check_interval
        self.max_session_permit = max_session_permit
        self.fairness_timeout = fairness_timeout
        self.memory_wait_timeout = memory_wait_timeout
        self.task_queue = asyncio.PriorityQueue()
        self.memory_pressure_mode = False
        self.current_memory_percent = 0.0
        self._high_memory_start_time: float | None = None

    async def _memory_monitor_task(self):
        while True:
            self.current_memory_percent = get_true_memory_usage_percent()

            if self.current_memory_percent >= self.memory_threshold_percent:
                if not self.memory_pressure_mode:
                    self.memory_pressure_mode = True
                    self._high_memory_start_time = time.time()
                else:
                    if self._high_memory_start_time is None:
                        self._high_memory_start_time = time.time()
                    if (
                        self.memory_wait_timeout is not None
                        and time.time() - self._high_memory_start_time >= self.memory_wait_timeout
                    ):
                        raise MemoryError(
                            "Memory usage exceeded threshold for"
                            f" {self.memory_wait_timeout} seconds"
                        )
            elif self.memory_pressure_mode and self.current_memory_percent <= self.recovery_threshold_percent:
                self.memory_pressure_mode = False
                self._high_memory_start_time = None
            elif self.current_memory_percent < self.memory_threshold_percent:
                self._high_memory_start_time = None

            await asyncio.sleep(self.check_interval)

    def _get_priority_score(self, wait_time: float, retry_count: int) -> float:
        if wait_time > self.fairness_timeout:
            return -wait_time
        return retry_count

    async def crawl_url(
        self,
        url: str,
        config: CrawlerRunConfig,
        task_id: str,
        retry_count: int = 0,
    ) -> CrawlerTaskResult:
        start_time = time.time()
        error_message = ""
        memory_usage = peak_memory = 0.0

        process = psutil.Process()
        start_memory = process.memory_info().rss / (1024 * 1024)

        try:
            self.concurrent_sessions += 1

            if self.rate_limiter:
                await self.rate_limiter.wait_if_needed(url)

            if self.current_memory_percent >= self.critical_threshold_percent:
                enqueue_time = time.time()
                priority = self._get_priority_score(enqueue_time - start_time, retry_count + 1)
                await self.task_queue.put((priority, (url, task_id, retry_count + 1, enqueue_time)))

                return CrawlerTaskResult(
                    task_id=task_id,
                    url=url,
                    result=CrawlResult(
                        url=url, html="", metadata={"status": "requeued"},
                        success=False, error_message="Requeued due to critical memory pressure"
                    ),
                    memory_usage=0,
                    peak_memory=0,
                    start_time=start_time,
                    end_time=time.time(),
                    error_message="Requeued due to critical memory pressure",
                    retry_count=retry_count + 1
                )

            result = await self.crawler.arun(url, config=config)

            end_memory = process.memory_info().rss / (1024 * 1024)
            memory_usage = peak_memory = end_memory - start_memory

            if self.rate_limiter and result.status_code:
                if not self.rate_limiter.update_delay(url, result.status_code):
                    error_message = f"Rate limit retry count exceeded for domain {urlparse(url).netloc}"

            if not result.success:
                error_message = result.error_message

        except Exception as e:
            error_message = str(e)
            result = CrawlResult(
                url=url, html="", metadata={}, success=False, error_message=str(e)
            )

        finally:
            end_time = time.time()
            self.concurrent_sessions -= 1

        return CrawlerTaskResult(
            task_id=task_id,
            url=url,
            result=result,
            memory_usage=memory_usage,
            peak_memory=peak_memory,
            start_time=start_time,
            end_time=end_time,
            error_message=error_message,
            retry_count=retry_count
        )

    async def run_urls(
        self,
        urls: list[str],
        crawler: "AsyncWebCrawler",
        config: CrawlerRunConfig,
    ) -> list[CrawlerTaskResult]:
        self.crawler = crawler

        memory_monitor = asyncio.create_task(self._memory_monitor_task())

        results = []

        try:
            for url in urls:
                task_id = str(uuid.uuid4())
                await self.task_queue.put((0, (url, task_id, 0, time.time())))

            active_tasks = []

            while not self.task_queue.empty() or active_tasks:
                if memory_monitor.done():
                    exc = memory_monitor.exception()
                    if exc:
                        for t in active_tasks:
                            t.cancel()
                        raise exc

                if not self.memory_pressure_mode:
                    slots = self.max_session_permit - len(active_tasks)
                    while slots > 0:
                        try:
                            priority, (url, task_id, retry_count, enqueue_time) = self.task_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        task = asyncio.create_task(
                            self.crawl_url(url, config, task_id, retry_count)
                        )
                        active_tasks.append(task)
                        slots -= 1

                if active_tasks:
                    done, pending = await asyncio.wait(
                        active_tasks, timeout=0.1, return_when=asyncio.FIRST_COMPLETED
                    )

                    for completed_task in done:
                        result = await completed_task
                        results.append(result)

                    active_tasks = list(pending)
                else:
                    await asyncio.sleep(self.check_interval / 2)

                await self._update_queue_priorities()

            return results

        finally:
            memory_monitor.cancel()

    async def _update_queue_priorities(self):
        if self.task_queue.empty():
            return

        temp_items = []

        drain_start = time.time()
        while not self.task_queue.empty() and time.time() - drain_start < 5.0:
            try:
                priority, (url, task_id, retry_count, enqueue_time) = await asyncio.wait_for(
                    self.task_queue.get(), timeout=0.1
                )
            except TimeoutError:
                break

            wait_time = time.time() - enqueue_time
            new_priority = self._get_priority_score(wait_time, retry_count)
            temp_items.append((new_priority, (url, task_id, retry_count, enqueue_time)))

        temp_items.sort(key=lambda x: x[0])

        for item in temp_items:
            await self.task_queue.put(item)
