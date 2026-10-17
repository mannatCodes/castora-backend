"""Reliable Google News RSS search independent of the optional ``gnews`` wrapper."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests


def search_google_news_rss(topic: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return normalized Google News results, or an empty list on a network failure.

    The ``gnews`` package is convenient but has had compatibility problems with
    newer HTTP clients. Google News exposes the same search results as RSS, so
    this small fallback keeps source discovery available without an API key.
    """
    query = (topic or "").strip()
    if not query or limit <= 0:
        return []

    url = (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Castora/1.0 (+https://castora.app)"},
            timeout=12,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as error:
        print(f"Google News RSS search failed for {query!r}: {error}")
        return []

    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for entry in feed.entries:
        url = str(entry.get("link") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        source = entry.get("source") or {}
        source_name = source.get("title") if isinstance(source, dict) else str(source)
        results.append(
            {
                "url": url,
                "title": str(entry.get("title") or "Untitled source"),
                "description": str(entry.get("summary") or ""),
                "source_name": source_name or "google_news",
                "tool_used": "google_news_rss",
                "published_date": str(entry.get("published") or entry.get("updated") or ""),
                "is_scrapping_required": False,
                "full_text": str(entry.get("summary") or ""),
            }
        )
        if len(results) >= limit:
            break
    return results
