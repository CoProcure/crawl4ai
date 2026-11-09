import pytest

from crawl4ai import (
    AsyncWebCrawler,
    CacheMode,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    LinkPreviewConfig,
)


def test_clone_preserves_options_and_replaces_overrides():
    config = CrawlerRunConfig(
        check_robots_txt=True,
        cache_mode=CacheMode.ENABLED,
        word_count_threshold=5,
        remove_forms=True,
        exclude_social_media_links=True,
        exclude_all_images=True,
        exclude_external_links=True,
        excluded_tags=["nav"],
        score_links=True,
        link_preview_config=LinkPreviewConfig(concurrency=3),
        markdown_generator=DefaultMarkdownGenerator(),
    )

    cloned = config.clone(user_agent="Fallback/1.0", cache_mode=CacheMode.WRITE_ONLY)

    assert cloned.user_agent == "Fallback/1.0"
    assert cloned.cache_mode == CacheMode.WRITE_ONLY
    assert cloned.check_robots_txt is True
    assert cloned.word_count_threshold == 5
    assert cloned.remove_forms is True
    assert cloned.exclude_social_media_links is True
    assert cloned.exclude_all_images is True
    assert cloned.exclude_external_links is True
    assert cloned.excluded_tags == ["nav"]
    assert cloned.score_links is True
    assert cloned.link_preview_config.concurrency == 3
    assert cloned.markdown_generator is config.markdown_generator


def test_clone_leaves_the_original_config_unchanged():
    config = CrawlerRunConfig(cache_mode=CacheMode.ENABLED)

    config.clone(user_agent="Fallback/1.0", cache_mode=CacheMode.WRITE_ONLY)

    assert config.user_agent is None
    assert config.cache_mode == CacheMode.ENABLED


def test_from_kwargs_defaults_word_count_threshold_to_200_when_unset():
    assert CrawlerRunConfig.from_kwargs({}).word_count_threshold == 200
    assert CrawlerRunConfig.from_kwargs({"word_count_threshold": 5}).word_count_threshold == 5


def test_removed_run_options_are_rejected():
    with pytest.raises(TypeError):
        CrawlerRunConfig(css_selector="h1")


@pytest.mark.asyncio
async def test_link_preview_attaches_head_data_and_score_to_links(local_site):
    config = CrawlerRunConfig(
        link_preview_config=LinkPreviewConfig(include_external=True),
        score_links=True,
    )

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(local_site.url("/"), config=config)

    all_links = result.links["internal"] + result.links["external"]
    links = {link["href"]: link for link in all_links}
    about = links[local_site.url("/about")]
    assert about["head_extraction_status"] == "valid"
    assert about["head_data"]["title"] == "About"
    assert about["total_score"] > 0
    assert local_site.url("/contact") in links


@pytest.mark.asyncio
async def test_links_have_no_head_data_without_link_preview_config(local_site):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(local_site.url("/"), config=CrawlerRunConfig())

    all_links = result.links["internal"] + result.links["external"]
    assert all_links
    assert all(link["head_data"] is None for link in all_links)


@pytest.mark.asyncio
async def test_link_preview():
    config = CrawlerRunConfig(
        link_preview_config=LinkPreviewConfig(),
        score_links=True,
        exclude_external_links=True,
    )
    test_url = "https://docs.python.org/3/"

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(test_url, config=config)

    assert result.success
    assert result.links
    assert result.links["external"] == []

    internal_links = result.links["internal"]

    assert any(il["total_score"] for il in internal_links)
    assert all(il["href"] for il in internal_links)
    assert all("title" in il for il in internal_links)
    assert all("text" in il for il in internal_links)
    assert all("head_data" in il for il in internal_links)
    assert all("head_extraction_status" in il for il in internal_links)
    assert all("head_extraction_error" in il for il in internal_links)
    assert all("total_score" in il for il in internal_links)

@pytest.mark.asyncio
async def test_user_agent():
    url = "https://www.nbcnews.com/business"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 Crawl4AI/1.0"
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url=url, config=CrawlerRunConfig(
                user_agent=user_agent
            )
        )

    assert result.success
    assert crawler.browser_config.user_agent == user_agent
