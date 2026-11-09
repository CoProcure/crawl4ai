import pytest

from crawl4ai import (
    AsyncWebCrawler,
    CacheMode,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    LinkPreviewConfig,
)


def test_clone_preserves_options_and_applies_overrides():
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
    config = CrawlerRunConfig.from_kwargs({})

    assert config.word_count_threshold == 200


def test_from_kwargs_keeps_an_explicit_word_count_threshold():
    config = CrawlerRunConfig.from_kwargs({"word_count_threshold": 5})

    assert config.word_count_threshold == 5


@pytest.mark.asyncio
async def test_link_preview_attaches_head_data_and_total_score_to_links(local_site):
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
