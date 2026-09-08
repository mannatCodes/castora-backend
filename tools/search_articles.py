import sqlite3
import os
import re
from typing import List, Union
from agno.agent import Agent
from db.config import get_tracking_db_path
import json
import requests


STOP_WORDS = {
    "about",
    "podcast",
    "podacst",
    "podcats",
    "make",
    "create",
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "news",
    "latest",
}


def search_articles(agent: Agent, terms: Union[str, List[str]]) -> str:
    """
    Search for articles related to a podcast topic using direct SQL queries.
    The agent can pass either a string topic or a list of search terms.

    Args:
        agent: The agent instance
        terms: Either a single topic string or a list of search terms

    Returns:
        A formatted string response with the search results
    """
    print(f"Search topic sources: {terms}")
    search_terms = _normalize_terms(terms)
    limit = 8
    db_path = get_tracking_db_path()
    topic = " ".join(search_terms)
    # Prefer current web results. The local RSS database remains available if
    # Google and Google News are temporarily unreachable.
    live_results = search_live_google_sources(topic, limit) or search_live_news_sources(topic, limit)
    if live_results:
        return (
            "is_scrapping_required: False, "
            f"Found {len(live_results)} live Google sources, "
            f"{json.dumps(live_results, indent=2)} potential sources relevant to your topic."
        )
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
            results = execute_simple_search(conn, search_terms, limit)
            if not results:
                return (
                    "is_scrapping_required: False, Found 0, [] potential sources. "
                    "The local article database and live Google fallback did not return relevant matches."
                )
            for article in results:
                article["categories"] = get_article_categories(conn, article["id"])
                article["source_name"] = "article_database"
                article["tool_used"] = "local_article_search"
                article["description"] = article.get("content") or ""
                article["full_text"] = article.get("content") or ""
                article["is_scrapping_required"] = False
            return f"is_scrapping_required: False, Found {len(results)}, {json.dumps(results, indent=2)} potential sources from the local article database that might be relevant to your topic. Quality-check the text matches and ignore invalid results."
    except Exception as e:
        print(f"Error searching articles: {e}")
        return "I encountered a database error while searching, and live Google fallback did not return results."


def _normalize_terms(terms: Union[str, List[str]]) -> List[str]:
    raw_terms = terms if isinstance(terms, list) else [terms]
    normalized = []
    for value in raw_terms:
        text = str(value or "").strip()
        if not text:
            continue
        words = [
            word
            for word in re.findall(r"[a-z0-9]+", text.lower())
            if len(word) > 2 and word not in STOP_WORDS
        ]
        normalized.extend(words or [text])
    return list(dict.fromkeys(normalized)) or [str(terms).strip()]


def execute_simple_search(conn, terms, limit):
    clauses = []
    params = []
    score_parts = []
    score_params = []
    for term in terms[:6]:
        like_term = f"%{term}%"
        clauses.append("(ca.title LIKE ? OR ca.summary LIKE ? OR ca.content LIKE ? OR ca.raw_content LIKE ?)")
        params.extend([like_term, like_term, like_term, like_term])
        score_parts.append(
            "(CASE WHEN ca.title LIKE ? THEN 5 ELSE 0 END + "
            "CASE WHEN ca.summary LIKE ? THEN 3 ELSE 0 END + "
            "CASE WHEN ca.content LIKE ? THEN 1 ELSE 0 END + "
            "CASE WHEN ca.raw_content LIKE ? THEN 1 ELSE 0 END)"
        )
        score_params.extend([like_term, like_term, like_term, like_term])

    query = f"""
        SELECT DISTINCT ca.id, ca.title, ca.url, ca.published_date,
               COALESCE(NULLIF(ca.summary, ''), NULLIF(ca.content, ''), ca.raw_content, '') as content,
               ca.source_id, ca.feed_id,
               ({" + ".join(score_parts)}) as relevance_score
        FROM crawled_articles ca
        WHERE ca.url IS NOT NULL
          AND ca.url != ''
          AND ({" OR ".join(clauses)})
        ORDER BY relevance_score DESC, datetime(ca.published_date) DESC, ca.id DESC
        LIMIT ?
    """
    params = score_params + params + [limit]
    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def get_article_categories(conn, article_id):
    try:
        cursor = conn.execute("SELECT category_name FROM article_categories WHERE article_id = ?", (article_id,))
        return [row["category_name"] for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching article categories: {e}")
        return []


def _load_serper_api_key() -> str:
    for key_name in ("SERPER_API_KEY", "Serper_API_KEY", "serper_api_key"):
        value = os.environ.get(key_name)
        if value:
            return value
    return ""


def search_live_google_sources(topic: str, limit: int) -> List[dict]:
    api_key = _load_serper_api_key()
    if not api_key:
        print("Live Google search skipped because SERPER_API_KEY is not configured.")
        return []
    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": topic, "num": limit},
            timeout=12,
        )
        response.raise_for_status()
        organic_results = response.json().get("organic", [])
    except Exception as e:
        print(f"Live Google search via Serper API failed for {topic!r}: {e}")
        return []

    results = []
    seen_urls = set()
    for item in organic_results:
        url = item.get("link") or item.get("url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        description = item.get("snippet") or item.get("description") or ""
        results.append(
            {
                "url": url,
                "title": item.get("title") or "Untitled source",
                "description": description,
                "source_name": item.get("source") or "google_search",
                "tool_used": "google_live_search",
                "published_date": item.get("date") or "",
                "is_scrapping_required": False,
                "full_text": description,
            }
        )
    return results


def search_live_news_sources(topic: str, limit: int) -> List[dict]:
    try:
        from gnews import GNews

        google_news = GNews(
            language=None,
            country=None,
            period=None,
            max_results=limit,
            exclude_websites=[],
        )
        live_results = google_news.get_news(topic)
    except Exception as e:
        print(f"Live Google News search failed for {topic!r}: {e}")
        return []

    results = []
    seen_urls = set()
    for item in live_results or []:
        url = item.get("url") or item.get("link")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        publisher = item.get("publisher") or {}
        source_name = publisher.get("title") if isinstance(publisher, dict) else str(publisher or "google_news")
        description = item.get("description") or item.get("summary") or ""
        results.append(
            {
                "url": url,
                "title": item.get("title") or "Untitled source",
                "description": description,
                "source_name": source_name or "google_news",
                "tool_used": "google_news_live_search",
                "published_date": item.get("published date") or item.get("published_date") or "",
                "is_scrapping_required": False,
                "full_text": description,
            }
        )
    return results
