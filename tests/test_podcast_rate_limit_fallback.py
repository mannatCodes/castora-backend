import types


def test_script_agent_rate_limit_falls_back_to_local_draft(monkeypatch):
    import agents.script_agent as script_agent

    class DummySessionService:
        sessions = {}

        @staticmethod
        def get_session(session_id):
            return DummySessionService.sessions.setdefault(session_id, {"state": {}})

        @staticmethod
        def save_session(session_id, session_state):
            DummySessionService.sessions[session_id] = {"state": session_state}

    class DummyAgent:
        session_id = "session-1"

    monkeypatch.setattr(script_agent, "run_with_retry_and_throttle", lambda func, **kwargs: (_ for _ in ()).throw(RuntimeError("rate limit reached 429")))
    monkeypatch.setattr("services.internal_session_service.SessionService", DummySessionService)
    monkeypatch.setenv("PODCAST_STUDIO_FAST_SCRIPT", "0")
    monkeypatch.setenv("PODCAST_STUDIO_USE_AI_SCRIPT", "1")

    session_state = {
        "search_results": [
            {
                "url": "https://example.com/article",
                "title": "Example article",
                "description": "A short description",
                "confirmed": True,
            }
        ]
    }
    DummySessionService.sessions["session-1"] = {"state": session_state}

    result = script_agent.podcast_script_agent_run(DummyAgent(), "AI podcast", "English")

    assert "success" in result.lower()
    assert session_state["stage"] == "script"
    assert session_state["generated_script"]["generation_mode"] == "fast_local"
    assert session_state["response"].startswith("The AI provider is rate-limited")


def test_live_google_sources_generate_fast_local_script(monkeypatch):
    import agents.script_agent as script_agent

    class DummySessionService:
        sessions = {}

        @staticmethod
        def get_session(session_id):
            return DummySessionService.sessions.setdefault(session_id, {"state": {}})

        @staticmethod
        def save_session(session_id, session_state):
            DummySessionService.sessions[session_id] = {"state": session_state}

    class DummyAgent:
        session_id = "session-live-script"

    def fail_remote_script(*args, **kwargs):
        raise AssertionError("remote script model should not run for live Google snippets")

    monkeypatch.setattr(script_agent, "run_with_retry_and_throttle", fail_remote_script)
    monkeypatch.setattr("services.internal_session_service.SessionService", DummySessionService)
    monkeypatch.setenv("PODCAST_STUDIO_FAST_SCRIPT", "0")
    monkeypatch.setenv("PODCAST_STUDIO_USE_AI_SCRIPT", "1")

    session_state = {
        "search_results": [
            {
                "url": "https://example.com/bangladesh-trafficking",
                "title": "Bangladesh police investigate girls trafficking network",
                "description": "A report on trafficking risks facing girls in Bangladesh.",
                "full_text": "girls trafficking Bangladesh",
                "tool_used": "google_live_search",
                "confirmed": True,
                "is_scrapping_required": False,
            }
        ]
    }
    DummySessionService.sessions["session-live-script"] = {"state": session_state}

    result = script_agent.podcast_script_agent_run(DummyAgent(), "girls trafficking in bangladesh", "English")

    assert "fast podcast draft" in result.lower()
    assert session_state["stage"] == "script"
    assert session_state["generated_script"]["generation_mode"] == "fast_local"
    assert session_state["show_script_for_confirmation"] is True


def test_agent_chat_rate_limit_does_not_switch_to_source_selection(monkeypatch):
    import services.celery_tasks as celery_tasks

    class DummySessionService:
        sessions = {"session-2": {"state": {}}}

        @staticmethod
        def get_session(session_id):
            return DummySessionService.sessions.setdefault(session_id, {"state": {}})

        @staticmethod
        def save_session(session_id, session_state):
            DummySessionService.sessions[session_id] = {"state": session_state}

    called = {"prepare_sources": False}

    def fake_prepare_sources(session_id, topic):
        called["prepare_sources"] = True
        return {"stage": "source_selection"}

    monkeypatch.setattr(celery_tasks, "_prepare_sources_without_ai", fake_prepare_sources)
    monkeypatch.setattr(celery_tasks, "_is_greeting_only", lambda message: False)
    monkeypatch.setattr(celery_tasks, "_is_podcast_topic_request", lambda message: False)
    monkeypatch.setattr(celery_tasks, "_handle_source_selection_directly", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.internal_session_service.SessionService", DummySessionService)

    def fake_agent_run(*args, **kwargs):
        raise RuntimeError("rate limit reached 429")

    monkeypatch.setattr(celery_tasks, "retry_with_backoff", fake_agent_run)

    response = celery_tasks.agent_chat.run("session-2", "Create a podcast about AI")

    assert called["prepare_sources"] is False
    assert "rate-limited" in response["response"].lower() or "capacity" in response["response"].lower()


def test_podcast_topic_typo_uses_direct_source_path():
    import services.celery_tasks as celery_tasks

    assert celery_tasks._is_podcast_topic_request("create a podacst about india")
    assert celery_tasks._clean_topic("create a podacst about india") == "india"
