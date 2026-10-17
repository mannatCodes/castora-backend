from unittest.mock import Mock, patch

from utils.google_news_rss import search_google_news_rss


def test_google_news_rss_returns_normalized_results():
    response = Mock()
    response.content = b"""
        <rss><channel><item>
          <title>AI update</title><link>https://news.example/item</link>
          <description>News summary</description><pubDate>Fri, 17 Oct 2026 00:00:00 GMT</pubDate>
        </item></channel></rss>
    """
    response.raise_for_status.return_value = None

    with patch("utils.google_news_rss.requests.get", return_value=response) as get:
        results = search_google_news_rss("artificial intelligence", limit=3)

    assert len(results) == 1
    assert results[0]["tool_used"] == "google_news_rss"
    assert results[0]["url"] == "https://news.example/item"
    assert "q=artificial+intelligence" in get.call_args.args[0]
