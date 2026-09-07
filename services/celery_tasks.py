from agno.agent import Agent
from agno.models.groq import Groq
from agno.storage.sqlite import SqliteStorage
import os
from pathlib import Path
from dotenv import load_dotenv
from services.celery_app import app, SessionLockedTask
from db.config import get_agent_session_db_path
import time
import random
from utils.retry_handler import retry_with_backoff, throttle_request

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
from db.agent_config_v2 import (
    AGENT_DESCRIPTION,
    AGENT_INSTRUCTIONS,
    AGENT_MODEL,
    INITIAL_SESSION_STATE,
)
from agents.search_agent import search_agent_run
from agents.scrape_agent import scrape_agent_run
from agents.script_agent import podcast_script_agent_run
from tools.ui_manager import ui_manager_run
from tools.user_source_selection import user_source_selection_run
from tools.session_state_manager import update_language, update_chat_title, mark_session_finished
from agents.image_generate_agent import image_generation_agent_run
from agents.audio_generate_agent import audio_generate_agent_run
from utils.load_api_keys import load_api_key
import json
import traceback
import sqlite3
import re
import requests
from types import SimpleNamespace
from db.config import get_tracking_db_path

load_dotenv()

db_file = get_agent_session_db_path()


GREETING_MESSAGES = {
    "hi",
    "hii",
    "hiii",
    "hello",
    "hey",
    "heyy",
    "yo",
    "namaste",
}


def _is_greeting_only(message: str) -> bool:
    normalized = "".join(ch.lower() for ch in message.strip() if ch.isalnum())
    return normalized in GREETING_MESSAGES


def _friendly_error_message(error: Exception) -> str:
    error_text = str(error)
    retry_after = None
    try:
        parsed = json.loads(error_text)
        error_data = parsed.get("error", {})
        message = error_data.get("message", "")
        if "rate limit" in message.lower():
            marker = "Please try again in "
            if marker in message:
                retry_after = message.split(marker, 1)[1].split(".", 1)[0]
    except Exception:
        message = error_text

    if _is_context_length_error(error):
        return (
            "The conversation has become too long for the AI model to process. "
            "I've prepared a local draft script from your selected sources so you can continue. "
            "Please review it below."
        )

    if _is_rate_limit_error(error):
        return (
            "The AI provider is currently at capacity. "
            "You can still create a podcast using local sources. "
            "Would you like me to prepare source suggestions for your podcast topic? "
            "Or, you can try again in a few minutes."
        )

    return f"I'm sorry, I encountered an error: {error_text}. Please try again."


def _is_tool_call_validation_error(error: Exception) -> bool:
    error_text = str(error).lower()
    return "tool call validation failed" in error_text or "tool_use_failed" in error_text


def _is_rate_limit_error(error: Exception) -> bool:
    """Check if error is a rate limit or quota error"""
    error_text = str(error).lower()
    return any([
        "rate_limit" in error_text,
        "rate limit" in error_text,
        "429" in error_text,
        "quota" in error_text,
        "monthly limit" in error_text,
        "usage limit" in error_text,
        "too many requests" in error_text,
    ])


def _is_context_length_error(error: Exception) -> bool:
    """Check if error is a context length exceeded error"""
    error_text = str(error).lower()
    return any([
        "context_length_exceeded" in error_text,
        "context length" in error_text,
        "please reduce the length" in error_text,
        "max_tokens" in error_text and "exceed" in error_text,
    ])


def _parse_selected_source_indices(message: str) -> list[int]:
    numbers = [int(value) for value in re.findall(r"\b\d+\b", message)]
    return numbers


def _parse_language(message: str, available_languages: list[dict]) -> dict:
    lowered = message.lower()
    for language in available_languages:
        name = language.get("name", "")
        code = language.get("code", "")
        if name and re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            return language
        if code and re.search(rf"\b{re.escape(code.lower())}\b", lowered):
            return language
    return {"code": "en", "name": "English"}


def _handle_source_selection_directly(session_id: str, message: str, session_state: dict) -> dict | None:
    if session_state.get("stage") != "source_selection" and not session_state.get("show_sources_for_selection"):
        return None

    selected_sources = _parse_selected_source_indices(message)
    if not selected_sources:
        return None

    from services.internal_session_service import SessionService

    search_results = session_state.get("search_results", [])
    valid_selected_sources = [
        index for index in selected_sources if 1 <= index <= len(search_results)
    ]
    if not valid_selected_sources:
        return {
            "session_id": session_id,
            "response": f"Please select source numbers between 1 and {len(search_results)}.",
            "stage": "source_selection",
            "session_state": json.dumps(session_state),
            "is_processing": False,
            "process_type": None,
        }

    selected_language = _parse_language(
        message,
        session_state.get("available_languages", INITIAL_SESSION_STATE["available_languages"]),
    )
    for index, source in enumerate(search_results, start=1):
        source["confirmed"] = index in valid_selected_sources
        if source["confirmed"]:
            source["full_text"] = source.get("full_text") or source.get("description", "")
            source["is_scrapping_required"] = False

    session_state["search_results"] = search_results
    session_state["selected_language"] = selected_language
    session_state["show_sources_for_selection"] = False
    session_state["show_script_for_confirmation"] = False
    session_state["stage"] = "script_generation"
    SessionService.save_session(session_id, session_state)

    podcast_script_agent_run(
        SimpleNamespace(session_id=session_id),
        session_state.get("title") or message,
        selected_language.get("name", "English"),
    )

    session_state = SessionService.get_session(session_id).get("state", session_state)
    if session_state.get("generated_script"):
        session_state["show_script_for_confirmation"] = True
        session_state["stage"] = "script"
        SessionService.save_session(session_id, session_state)
        response = "I generated the podcast script from your selected sources. Please review it."
    else:
        response = session_state.get("response") or "Script generation did not produce a script. Please try again."

    return {
        "session_id": session_id,
        "response": response,
        "stage": session_state.get("stage", "script"),
        "session_state": json.dumps(session_state),
        "is_processing": False,
        "process_type": None,
    }


def _clean_topic(message: str) -> str:
    topic = message.strip()
    topic = re.sub(r"\bpodacst\b", "podcast", topic, flags=re.IGNORECASE)
    topic = re.sub(r"\bpodcats\b", "podcast", topic, flags=re.IGNORECASE)
    lowered = topic.lower()
    for prefix in (
        "i want to create a podcast about ",
        "i want create a podcast about ",
        "create a podcast about ",
        "make a podcast about ",
        "podcast about ",
        "about ",
        "make podcast about ",
        "create podcast about ",
    ):
        if lowered.startswith(prefix):
            return topic[len(prefix):].strip() or topic
    return topic


def _topic_terms(topic: str) -> list[str]:
    stop_words = {
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
    return [
        term
        for term in re.findall(r"[a-z0-9]+", _clean_topic(topic).lower())
        if len(term) > 2 and term not in stop_words
    ]


def _term_matches(term: str, haystack: str) -> bool:
    if term in {"trafficking", "trafficked", "trafficker", "traffickers"}:
        return "traffick" in haystack
    if term in {"women", "woman"}:
        return bool(re.search(r"\bwom[ae]n\b", haystack))
    if term in {"girls", "girl"}:
        return bool(re.search(r"\bgirls?\b", haystack))
    return bool(re.search(rf"\b{re.escape(term)}\b", haystack))


def _source_relevance_score(topic_terms: list[str], source: dict) -> int:
    haystack = " ".join(
        str(source.get(key, "") or "")
        for key in ("title", "description", "full_text")
    ).lower()
    return sum(1 for term in topic_terms if _term_matches(term, haystack))


def _filter_relevant_sources(topic: str, sources: list[dict]) -> list[dict]:
    terms = _topic_terms(topic)
    if not terms:
        return sources

    required_score = 1 if len(terms) == 1 else min(2, len(terms))
    distinctive_terms = [
        term for term in terms
        if term in {"trafficking", "trafficked", "trafficker", "traffickers"}
    ]

    scored = []
    for source in sources:
        haystack = " ".join(
            str(source.get(key, "") or "")
            for key in ("title", "description", "full_text")
        ).lower()
        if distinctive_terms and not any(_term_matches(term, haystack) for term in distinctive_terms):
            continue
        score = _source_relevance_score(terms, source)
        if score >= required_score:
            scored.append((score, source))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [source for _, source in scored]


def _is_podcast_topic_request(message: str) -> bool:
    lowered = message.lower()
    normalized = re.sub(r"\b(podacst|podcats)\b", "podcast", lowered)
    topic_markers = (
        "podcast about",
        "create a podcast",
        "make a podcast",
        "create podcast",
        "make podcast",
    )
    return any(marker in normalized for marker in topic_markers)


def _should_prepare_sources_directly(message: str, session_state: dict) -> bool:
    if not _is_podcast_topic_request(message):
        # If in welcome stage, treat any non-greeting message as a topic request
        if session_state.get("stage") == "welcome":
            return True
        return False
    if session_state.get("stage") in {"script", "banner", "audio", "complete"}:
        return False
    return True


def _search_local_articles(topic: str, limit: int = 8) -> list[dict]:
    topic = _clean_topic(topic)
    terms = _topic_terms(topic)
    if not terms:
        terms = [topic]

    def run_query(include_content: bool) -> list[dict]:
        clauses = []
        params = []
        for term in terms[:4]:
            like_term = f"%{term}%"
            if include_content:
                clauses.append("(ca.title LIKE ? OR ca.summary LIKE ? OR ca.content LIKE ?)")
                params.extend([like_term, like_term, like_term])
            else:
                clauses.append("(ca.title LIKE ? OR ca.summary LIKE ?)")
                params.extend([like_term, like_term])

        query = f"""
            SELECT
                ca.id,
                ca.title,
                ca.url,
                ca.published_date,
                COALESCE(NULLIF(ca.summary, ''), NULLIF(ca.content, ''), ca.raw_content, '') AS description
            FROM crawled_articles ca
            WHERE ca.url IS NOT NULL
              AND ca.url != ''
              AND ({" OR ".join(clauses)})
            ORDER BY datetime(ca.published_date) DESC, ca.id DESC
            LIMIT ?
        """
        params.append(limit)

        found = []
        with sqlite3.connect(f"file:{get_tracking_db_path()}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        for row in rows:
            item = dict(row)
            found.append(
                {
                    "url": item.get("url", ""),
                    "title": item.get("title", "Untitled source"),
                    "description": (item.get("description") or "")[:1200],
                    "source_name": "article_database",
                    "tool_used": "local_article_search",
                    "published_date": item.get("published_date") or "",
                    "is_scrapping_required": False,
                    "full_text": item.get("description") or "",
                }
            )
        return found

    results = run_query(include_content=False)
    if not results:
        results = run_query(include_content=True)
    return _filter_relevant_sources(topic, results)


def _load_serper_api_key() -> str | None:
    for key_name in ("SERPER_API_KEY", "Serper_API_KEY", "serper_api_key"):
        value = os.environ.get(key_name)
        if value:
            return value
    return None


def _search_live_google_sources(topic: str, limit: int = 8) -> list[dict]:
    api_key = _load_serper_api_key()
    if api_key:
        try:
            response = requests.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                json={"q": topic, "num": limit},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            organic_results = payload.get("organic", [])
        except Exception as e:
            print(f"Live Google search via Serper API failed for {topic!r}: {e}")
            organic_results = []
    else:
        print("Live Google search skipped because SERPER_API_KEY is not configured.")
        organic_results = []

    results = []
    seen_urls = set()
    for item in organic_results or []:
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
    return _filter_relevant_sources(topic, results)


def _search_live_news_sources(topic: str, limit: int = 8) -> list[dict]:
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
    return _filter_relevant_sources(topic, results)


def _prepare_sources_without_ai(session_id: str, topic: str) -> dict:
    from services.internal_session_service import SessionService

    topic = _clean_topic(topic)
    session = SessionService.get_session(session_id)
    session_state = session.get("state", INITIAL_SESSION_STATE)
    results = _search_local_articles(topic)
    fallback_notice = " I used the local article database so we do not hit the AI provider rate limit."

    if not results:
        results = _search_live_google_sources(topic)
        fallback_notice = " I searched Google live because the local article database did not have relevant matches."

    if not results:
        results = _search_live_news_sources(topic)
        fallback_notice = " I searched Google News live because Google web search did not return relevant matches."

    if not results:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-") or "topic"
        results = [
            {
                "url": f"local://topic/{slug}",
                "title": topic.title(),
                "description": (
                    f"Local fallback source for a podcast about {topic}. "
                    "Add or select richer sources when available for a more detailed episode."
                ),
                "source_name": "local_topic_fallback",
                "tool_used": "local_fallback",
                "published_date": "",
                "is_scrapping_required": False,
                "full_text": (
                    f"This fallback draft is centered on {topic}. "
                    "Use it to continue the podcast workflow while the AI provider is rate-limited."
                ),
            }
        ]
        fallback_notice = " I created a local fallback source so the podcast flow can continue without the AI provider."

    session_state["search_results"] = results
    session_state["stage"] = "source_selection"
    session_state["title"] = topic[:60].strip().title() or "Podcast"
    session_state["show_sources_for_selection"] = True
    session_state["show_script_for_confirmation"] = False
    session_state["show_banner_for_confirmation"] = False
    session_state["show_audio_for_confirmation"] = False
    SessionService.save_session(session_id, session_state)

    return {
        "session_id": session_id,
        "response": (
            f"I found {len(results)} sources for a podcast about {topic}."
            f"{fallback_notice} Please select the sources you want to use from the list."
        ),
        "stage": "source_selection",
        "session_state": json.dumps(session_state),
        "is_processing": False,
        "process_type": None,
    }


def _prepare_sources_after_tool_failure(session_id: str, topic: str) -> dict:
    from services.internal_session_service import SessionService

    topic = _clean_topic(topic)
    session = SessionService.get_session(session_id)
    session_state = session.get("state", INITIAL_SESSION_STATE)
    fallback_notice = ""

    try:
        search_agent_run(SimpleNamespace(session_id=session_id), topic)
        session = SessionService.get_session(session_id)
        session_state = session.get("state", session_state)
    except Exception as search_error:
        print(f"Search agent fallback also failed: {search_error}")
        results = _search_local_articles(topic)
        if not results:
            results = _search_live_google_sources(topic)
            fallback_notice = " I searched Google live because the local article database did not have relevant matches."
        if not results:
            results = _search_live_news_sources(topic)
            fallback_notice = " I searched Google News live because Google web search did not return relevant matches."
        if not results:
            raise search_error
        session_state["search_results"] = results
        if not fallback_notice:
            fallback_notice = " I used the local article database because the live search tool had a validation issue."

    session_state["stage"] = "source_selection"
    session_state["title"] = topic[:60].strip().title() or "Podcast"
    session_state["show_sources_for_selection"] = True
    session_state["show_script_for_confirmation"] = False
    session_state["show_banner_for_confirmation"] = False
    session_state["show_audio_for_confirmation"] = False
    SessionService.save_session(session_id, session_state)

    source_count = len(session_state.get("search_results", []))
    return {
        "session_id": session_id,
        "response": (
            f"I found {source_count} sources for a podcast about {topic}."
            f"{fallback_notice} Please select the sources you want to use from the list."
        ),
        "stage": "source_selection",
        "session_state": json.dumps(session_state),
        "is_processing": False,
        "process_type": None,
    }


def _is_script_approval(message: str, session_state: dict) -> bool:
    lowered = message.lower()
    return (
        session_state.get("stage") == "script"
        and session_state.get("generated_script")
        and ("approve" in lowered or "looks good" in lowered or "good" in lowered)
    )


def _is_banner_approval(message: str, session_state: dict) -> bool:
    lowered = message.lower()
    return (
        session_state.get("stage") == "banner"
        and ("approve" in lowered or "looks good" in lowered or "good" in lowered)
    )


def _is_audio_approval(message: str, session_state: dict) -> bool:
    lowered = message.lower()
    return (
        session_state.get("stage") == "audio"
        and ("audio sounds great" in lowered or "happy with the final podcast" in lowered or "sounds good" in lowered or "great" in lowered)
    )


def _handle_stage_approval_directly(session_id: str, message: str, session_state: dict) -> dict | None:
    from services.internal_session_service import SessionService

    agent_stub = SimpleNamespace(session_id=session_id)

    if _is_script_approval(message, session_state):
        session_state["show_script_for_confirmation"] = False
        session_state["stage"] = "banner_generation"
        SessionService.save_session(session_id, session_state)
        result_message = image_generation_agent_run(agent_stub, session_state.get("title", "podcast cover"))
        session_state = SessionService.get_session(session_id).get("state", session_state)
        session_state["stage"] = "banner"
        session_state["show_banner_for_confirmation"] = True
        SessionService.save_session(session_id, session_state)
        return {
            "session_id": session_id,
            "response": result_message or "Banner step is ready. Please review the banner.",
            "stage": "banner",
            "session_state": json.dumps(session_state),
            "is_processing": False,
            "process_type": None,
        }

    if _is_banner_approval(message, session_state):
        session_state["show_banner_for_confirmation"] = False
        session_state["stage"] = "audio_generation"
        SessionService.save_session(session_id, session_state)
        result_message = audio_generate_agent_run(agent_stub)
        session_state = SessionService.get_session(session_id).get("state", session_state)
        if session_state.get("audio_url"):
            session_state["stage"] = "audio"
            session_state["show_audio_for_confirmation"] = True
            response = result_message or "Audio is ready. Please review it."
        else:
            session_state["stage"] = "audio"
            session_state["show_audio_for_confirmation"] = True
            response = result_message or "Audio generation had an issue, but you can continue or retry."
        SessionService.save_session(session_id, session_state)
        return {
            "session_id": session_id,
            "response": response,
            "stage": session_state.get("stage", "audio"),
            "session_state": json.dumps(session_state),
            "is_processing": False,
            "process_type": None,
        }

    if _is_audio_approval(message, session_state):
        session_state["show_audio_for_confirmation"] = False
        SessionService.save_session(session_id, session_state)
        result_message = mark_session_finished(agent_stub)
        session_state = SessionService.get_session(session_id).get("state", session_state)
        return {
            "session_id": session_id,
            "response": result_message or "Your podcast has been created successfully.",
            "stage": session_state.get("stage", "complete"),
            "session_state": json.dumps(session_state),
            "is_processing": False,
            "process_type": None,
        }

    return None


@app.task(bind=True, max_retries=0, base=SessionLockedTask)
def agent_chat(self, session_id, message):
    try:
        print(f"Processing message for session {session_id}: {message[:50]}...")
        db_file = get_agent_session_db_path()
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        from services.internal_session_service import SessionService

        session_state = SessionService.get_session(session_id).get("state", INITIAL_SESSION_STATE)
        if _is_greeting_only(message):
            session_state["stage"] = session_state.get("stage", "welcome")
            SessionService.save_session(session_id, session_state)
            return {
                "session_id": session_id,
                "response": "Hi! What topic would you like to create a podcast about?",
                "stage": session_state.get("stage", "welcome"),
                "session_state": json.dumps(session_state),
                "is_processing": False,
                "process_type": None,
            }

        if _should_prepare_sources_directly(message, session_state):
            return _prepare_sources_without_ai(session_id, message)

        direct_selection_response = _handle_source_selection_directly(session_id, message, session_state)
        if direct_selection_response:
            return direct_selection_response

        direct_approval_response = _handle_stage_approval_directly(session_id, message, session_state)
        if direct_approval_response:
            return direct_approval_response

        _agent = Agent(
            model=Groq(id=AGENT_MODEL, api_key=load_api_key()),
            storage=SqliteStorage(table_name="podcast_sessions", db_file=db_file),
            add_history_to_messages=True,
            read_chat_history=True,
            add_state_in_messages=True,
            num_history_runs=3,
            instructions=AGENT_INSTRUCTIONS,
            description=AGENT_DESCRIPTION,
            session_state=session_state,
            session_id=session_id,
            tools=[
                search_agent_run,
                scrape_agent_run,
                ui_manager_run,
                user_source_selection_run,
                update_language,
                podcast_script_agent_run,
                image_generation_agent_run,
                audio_generate_agent_run,
                update_chat_title,
                mark_session_finished,
            ],
            markdown=True,
        )
        # Use throttling and retry logic for rate limit handling
        # Increased retries and delay for better quota limit handling
        def run_agent():
            throttle_request("groq")
            return _agent.run(message, session_id=session_id)
        
        response = retry_with_backoff(run_agent, max_retries=5, base_delay=3)
        print(f"Response generated for session {session_id}")
        _agent.write_to_storage(session_id=session_id)
        session_state = SessionService.get_session(session_id).get("state", INITIAL_SESSION_STATE)
        return {
            "session_id": session_id,
            "response": response.content,
            "stage": _agent.session_state.get("stage", "unknown"),
            "session_state": json.dumps(session_state),
            "is_processing": False,
            "process_type": None,
        }
    except Exception as e:
        traceback.print_exc()
        print(f"Error in agent_chat for session {session_id}: {str(e)}")
        
        if _is_tool_call_validation_error(e):
            try:
                return _prepare_sources_after_tool_failure(session_id, message)
            except Exception as fallback_error:
                traceback.print_exc()
                print(f"Fallback source preparation failed for session {session_id}: {fallback_error}")
        
        if _is_rate_limit_error(e):
            print(f"[RATE_LIMIT] Preserving existing session state for session {session_id}")
            try:
                from services.internal_session_service import SessionService

                session_state = SessionService.get_session(session_id).get("state", INITIAL_SESSION_STATE)
                if session_state.get("generated_script") and session_state.get("stage") in {"script", "audio", "complete"}:
                    session_state["is_processing"] = False
                    session_state["show_sources_for_selection"] = False
                    session_state["show_script_for_confirmation"] = True
                    session_state["response"] = (
                        "The AI provider is currently at capacity, but a draft script is already available. "
                        "You can review it and continue to audio generation."
                    )
                    SessionService.save_session(session_id, session_state)
                    return {
                        "session_id": session_id,
                        "response": session_state["response"],
                        "stage": session_state.get("stage", "script"),
                        "session_state": json.dumps(session_state),
                        "is_processing": False,
                        "process_type": None,
                    }

                topic = _clean_topic(message)
                if topic and len(topic) > 2:
                    return _prepare_sources_without_ai(session_id, topic)
            except Exception as fallback_error:
                print(f"Rate-limit fallback handling also failed: {fallback_error}")
        
        return {
            "session_id": session_id,
            "response": _friendly_error_message(e),
            "stage": "error",
            "session_state": "{}",
            "is_processing": False,
            "process_type": None,
        }
