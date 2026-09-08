from agno.agent import Agent
from agno.models.groq import Groq
from pydantic import BaseModel, Field
from typing import List, Optional
from textwrap import dedent
from datetime import datetime
import os
from utils.retry_handler import run_with_retry_and_throttle


# =========================
# DATA MODELS
# =========================
class Dialog(BaseModel):
    speaker: str = Field(..., description="ALEX or MORGAN")
    text: str = Field(...)


class Section(BaseModel):
    type: str
    title: Optional[str] = None
    dialog: List[Dialog]


class PodcastScript(BaseModel):
    title: str
    sections: List[Section]


# =========================
# AGENT PROMPT
# =========================
PODCAST_AGENT_DESCRIPTION = "Generate engaging podcast scripts"

PODCAST_AGENT_INSTRUCTIONS = dedent("""
Create an engaging podcast script using provided sources.

Rules:
- At least 5–8 minutes long
- Speakers: ALEX and MORGAN only
- Conversational and insightful
- Use provided content as grounding
- Avoid repeating the same facts in multiple sections
- Always respond in requested language
""")


# =========================
# FRESH SEARCH FUNCTION
# =========================
def fetch_fresh_search_results(query: str):
    """
    Replace with Serper/Tavily/Google API as needed
    """
    from agno.tools.serper import SerperTools

    search = SerperTools()
    results = search.search(f"{query} latest news")

    formatted = []

    organic_results = results.get("organic", [])

    # DEBUG (helps crawler issues)
    print(f"[DEBUG] Raw results count: {len(organic_results)}")

    for r in organic_results:
        url = r.get("link")

        if not url:
            continue

        formatted.append({
            "title": r.get("title", ""),
            "url": url,
            "description": r.get("snippet", ""),
            "confirmed": True,
            "full_text": r.get("snippet", "")
        })

    return formatted


# =========================
# FORMATTER (SAFE VERSION)
# =========================
def format_search_results_for_podcast(search_results, max_chars_per_source: int = 900, max_sources: int = 4):
    created_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    structured_content = [f"PODCAST CREATION: {created_at}\n"]
    sources = []

    for idx, search_result in enumerate(search_results[:max_sources]):

        if not search_result:
            continue

        if not search_result.get("url"):
            continue

        if not search_result.get("confirmed", False):
            continue

        sources.append(search_result["url"])

        content = search_result.get('full_text') or search_result.get('description', '')
        if content and len(content) > max_chars_per_source:
            content = content[:max_chars_per_source].rsplit(" ", 1)[0] + "..."

        structured_content.append(f"""
SOURCE {idx + 1}:
Title: {search_result.get('title', '')}
URL: {search_result.get('url', '')}
Content: {content}
---END SOURCE {idx + 1}---
""".strip())

    return "\n\n".join(structured_content), sources


def _trim_text(text: str, limit: int = 420) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "..."


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _should_use_ai_script() -> bool:
    # Fast local drafts bypass the configured AI script pipeline entirely.
    # Keep them opt-in; production enables PODCAST_STUDIO_USE_AI_SCRIPT.
    if _env_flag("PODCAST_STUDIO_FAST_SCRIPT", "0"):
        return False
    return _env_flag("PODCAST_STUDIO_USE_AI_SCRIPT", "0")


def _has_live_search_sources(search_results: list[dict]) -> bool:
    live_tools = {"google_live_search", "google_news_live_search"}
    return any(source.get("tool_used") in live_tools for source in search_results)


def _build_local_script(query: str, language_name: str, search_results: list[dict]) -> dict:
    topic = query.strip() or "Untitled Podcast"
    sources = [source.get("url", "") for source in search_results if source.get("url")]
    source_summaries = []

    for index, source in enumerate(search_results[:4], start=1):
        title = source.get("title") or f"Source {index}"
        content = source.get("full_text") or source.get("description") or ""
        source_summaries.append(
            {
                "title": title,
                "url": source.get("url", ""),
                "summary": _trim_text(content),
            }
        )

    if not source_summaries:
        source_summaries.append(
            {
                "title": topic,
                "url": "",
                "summary": "No detailed source text was available, so this draft frames the topic for review.",
            }
        )

    sections = [
        {
            "type": "intro",
            "title": "Opening",
            "dialog": [
                {
                    "speaker": "ALEX",
                    "text": f"Welcome to today's episode. We're exploring {topic}, using the selected sources as our starting point.",
                },
                {
                    "speaker": "MORGAN",
                        "text": "We'll keep this grounded, practical, and useful, moving through the strongest source points without waiting on a remote script model.",
                },
            ],
        }
    ]

    for source in source_summaries:
        sections.append(
            {
                "type": "article",
                "title": source["title"],
                "dialog": [
                    {
                        "speaker": "ALEX",
                        "text": f"One selected source, titled '{source['title']}', gives us this core material: {_trim_text(source['summary'], 300)}",
                    },
                    {
                        "speaker": "MORGAN",
                        "text": "The useful podcast angle is to connect that information to the listener's everyday understanding, not just repeat the source back.",
                    },
                    {
                        "speaker": "ALEX",
                        "text": "For the episode structure, this can become a segment with a clear claim, a supporting example, and a short reflection.",
                    },
                ],
            }
        )

    sections.append(
        {
            "type": "outro",
            "title": "Closing",
            "dialog": [
                {
                    "speaker": "MORGAN",
                    "text": f"That gives us a complete draft episode about {topic}.",
                },
                {
                    "speaker": "ALEX",
                    "text": "The draft is ready for review, and we can move straight to the cover and audio steps from here.",
                },
            ],
        }
    )

    return {
        "title": topic.title(),
        "sections": sections,
        "sources": sources,
        "language": language_name,
        "generation_mode": "fast_local",
    }


def _save_local_script(
    session_id: str,
    session_state: dict,
    query: str,
    language_name: str,
    search_results: list[dict],
    reason: str,
) -> str:
    from services.internal_session_service import SessionService

    fallback_script = _build_local_script(query, language_name, search_results)
    session_state["generated_script"] = fallback_script
    session_state["stage"] = "script"
    session_state["is_processing"] = False
    session_state["show_sources_for_selection"] = False
    session_state["show_script_for_confirmation"] = True
    session_state["response"] = reason
    SessionService.save_session(session_id, session_state)
    return reason


# =========================
# MAIN FUNCTION
# =========================
def podcast_script_agent_run(agent: Agent, query: str, language_name: str) -> str:

    from services.internal_session_service import SessionService

    try:
        session_id = agent.session_id
        session = SessionService.get_session(session_id)

        if not session:
            return "Session not found"

        session_state = session.get("state", {})

        search_results = [
            result
            for result in session_state.get("search_results", [])
            if result.get("confirmed", False)
        ]

        if search_results:
            print(f"[INFO] Using {len(search_results)} user-selected sources for: {query}")
        else:
            print(f"[INFO] No confirmed sources found. Generating fresh search results for: {query}")
            search_results = fetch_fresh_search_results(query)

            if not search_results:
                return _save_local_script(
                    session_id,
                    session_state,
                    query,
                    language_name,
                    [],
                    "I could not find external sources, so I created a local podcast draft you can review.",
                )

            seen_urls = set()
            unique_results = []

            for r in search_results:
                url = r.get("url")
                if url and url not in seen_urls:
                    r["confirmed"] = True
                    unique_results.append(r)
                    seen_urls.add(url)

            search_results = unique_results
            session_state["search_results"] = search_results

        if _has_live_search_sources(search_results):
            return _save_local_script(
                session_id,
                session_state,
                query,
                language_name,
                search_results,
                "I created a fast podcast draft from the live Google search snippets. Please review it.",
            )

        if not _should_use_ai_script():
            return _save_local_script(
                session_id,
                session_state,
                query,
                language_name,
                search_results,
                "I created a fast podcast draft from your selected sources. Please review it.",
            )

        # =========================
        # FORMAT INPUT FOR LLM
        # =========================
        content_texts, sources = format_search_results_for_podcast(search_results)

        if not content_texts.strip():
            return _save_local_script(
                session_id,
                session_state,
                query,
                language_name,
                search_results,
                "The selected sources did not have usable text, so I created a local podcast draft you can review.",
            )

        # =========================
        # AGENT
        # =========================
        podcast_script_agent = Agent(
            model=Groq(id="llama-3.3-70b-versatile"),
            instructions=PODCAST_AGENT_INSTRUCTIONS,
            description=PODCAST_AGENT_DESCRIPTION,
            response_model=PodcastScript,
            use_json_mode=False,
            session_id=session_id,
        )

        # Run with retry and throttling for rate limit protection
        prompt = f"""
query: {query}
language: {language_name}

content:
{content_texts}

Rules:
- Speakers only ALEX and MORGAN
- Max 6 sections
- Max 20 dialogues per section
- Must be engaging and non-repetitive
"""
        
        response = run_with_retry_and_throttle(
            lambda: podcast_script_agent.run(prompt, session_id=session_id),
            api_name="groq",
            max_retries=1,
            base_delay=1
        )

        # =========================
        # SAFE PARSING
        # =========================
        raw = response.to_dict()
        result = raw.get("content") if isinstance(raw, dict) else None

        if not result:
            return _save_local_script(
                session_id,
                session_state,
                query,
                language_name,
                search_results,
                "The AI script response was empty, so I created a local podcast draft you can review.",
            )

        if hasattr(result, "model_dump"):
            result = result.model_dump()
        elif not isinstance(result, dict):
            try:
                result = dict(result)
            except Exception:
                return _save_local_script(
                    session_id,
                    session_state,
                    query,
                    language_name,
                    search_results,
                    "The AI script response was not usable, so I created a local podcast draft you can review.",
                )

        result["sources"] = sources

        # =========================
        # SESSION SAVE
        # =========================
        session_state["generated_script"] = result
        session_state["stage"] = "script"
        session_state["is_processing"] = False
        session_state["response"] = "Script generated successfully"

        SessionService.save_session(session_id, session_state)

        print(f"[SUCCESS] Generated script with {len(result.get('sections', []))} sections")

        return f"Podcast generated successfully with {len(sources)} sources"

    except Exception as e:
        import traceback
        traceback.print_exc()

        try:
            error_text = str(e)
            is_rate_limit_error = (
                "rate_limit" in error_text.lower()
                or "rate limit" in error_text.lower()
                or "429" in error_text
            )
            if is_rate_limit_error:
                return _save_local_script(
                    session_id,
                    session_state,
                    query,
                    language_name,
                    search_results,
                    (
                        "The AI provider is rate-limited right now, so I created a local draft "
                        "from your selected sources for review. You can continue with the script and audio steps."
                    ),
                )

            return _save_local_script(
                session_id,
                session_state,
                query,
                language_name,
                search_results if "search_results" in locals() else [],
                "Script generation had an issue, so I created a local podcast draft you can review.",
            )
        except:
            pass

        return f"Script generation failed: {str(e)}"
