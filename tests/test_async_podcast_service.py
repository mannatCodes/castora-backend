import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from services.async_podcast_agent_service import PodcastAgentService


class PodcastAgentServiceTests(unittest.TestCase):
    def test_chat_greeting_returns_immediately_without_processing(self):
        service = PodcastAgentService.__new__(PodcastAgentService)
        service.use_celery = False
        service.local_tasks = {}
        service.local_session_tasks = {}
        service.local_lock = object()

        request = SimpleNamespace(session_id="session-1", message="hi")

        with patch.object(service, "_submit_local_task", side_effect=AssertionError("should not submit task for greeting")) as submit_mock:
            with patch("services.async_podcast_agent_service._is_greeting_only", return_value=True):
                response = __import__("asyncio").run(service.chat(request))

        self.assertEqual(response["response"], "Hi! What topic would you like to create a podcast about?")
        self.assertFalse(response["is_processing"])
        self.assertIsNone(response.get("task_id"))
        submit_mock.assert_not_called()

    def test_chat_topic_typo_returns_sources_without_processing(self):
        service = PodcastAgentService.__new__(PodcastAgentService)
        service.use_celery = False
        service.local_tasks = {}
        service.local_session_tasks = {}
        service.local_lock = object()

        request = SimpleNamespace(session_id="session-typo", message="create a podacst about india")
        source_response = {
            "session_id": "session-typo",
            "response": "I found 1 sources for a podcast about india.",
            "stage": "source_selection",
            "session_state": "{}",
            "is_processing": False,
            "process_type": None,
        }

        with patch.object(service, "get_active_task", return_value=None):
            with patch("services.async_podcast_agent_service.SessionService.get_session", return_value={"state": {"stage": "welcome"}}):
                with patch("services.async_podcast_agent_service._prepare_sources_without_ai", return_value=source_response) as prepare_mock:
                    with patch.object(service, "_submit_local_task", side_effect=AssertionError("should not submit task for topic request")):
                        response = __import__("asyncio").run(service.chat(request))

        self.assertFalse(response["is_processing"])
        self.assertEqual(response["stage"], "source_selection")
        self.assertIsNone(response.get("task_id"))
        prepare_mock.assert_called_once_with("session-typo", "create a podacst about india")

    def test_chat_topic_request_uses_sources_even_with_active_task(self):
        service = PodcastAgentService.__new__(PodcastAgentService)
        service.use_celery = False
        service.local_tasks = {}
        service.local_session_tasks = {}
        service.local_lock = object()

        request = SimpleNamespace(session_id="session-india", message="create a podcast about india")
        source_response = {
            "session_id": "session-india",
            "response": "I found 6 sources for a podcast about india.",
            "stage": "source_selection",
            "session_state": "{}",
            "is_processing": False,
            "process_type": None,
        }

        with patch.object(service, "get_active_task", side_effect=AssertionError("topic requests should not check active task first")):
            with patch("services.async_podcast_agent_service.SessionService.get_session", return_value={"state": {"stage": "source_selection"}}):
                with patch("services.async_podcast_agent_service._prepare_sources_without_ai", return_value=source_response) as prepare_mock:
                    response = __import__("asyncio").run(service.chat(request))

        self.assertFalse(response["is_processing"])
        self.assertEqual(response["stage"], "source_selection")
        self.assertIsNone(response.get("task_id"))
        prepare_mock.assert_called_once_with("session-india", "create a podcast about india")

    def test_chat_source_selection_returns_script_without_processing(self):
        service = PodcastAgentService.__new__(PodcastAgentService)
        service.use_celery = False
        service.local_tasks = {}
        service.local_session_tasks = {}
        service.local_lock = object()

        request = SimpleNamespace(session_id="session-select", message="1")
        selection_response = {
            "session_id": "session-select",
            "response": "I generated the podcast script from your selected sources. Please review it.",
            "stage": "script",
            "session_state": "{}",
            "is_processing": False,
            "process_type": None,
        }

        with patch.object(service, "get_active_task", return_value=None):
            with patch("services.async_podcast_agent_service.SessionService.get_session", return_value={"state": {"stage": "source_selection", "show_sources_for_selection": True}}):
                with patch("services.async_podcast_agent_service._handle_source_selection_directly", return_value=selection_response) as selection_mock:
                    with patch.object(service, "_submit_local_task", side_effect=AssertionError("should not submit task for source selection")):
                        response = __import__("asyncio").run(service.chat(request))

        self.assertFalse(response["is_processing"])
        self.assertEqual(response["stage"], "script")
        self.assertIsNone(response.get("task_id"))
        selection_mock.assert_called_once()

    def test_chat_returns_ready_script_when_task_lock_is_stale(self):
        service = PodcastAgentService.__new__(PodcastAgentService)
        service.use_celery = False
        service.local_tasks = {}
        service.local_session_tasks = {}
        service.local_lock = object()

        request = SimpleNamespace(session_id="session-ready", message="1")
        session_state = {
            "stage": "script",
            "response": "Script generated successfully",
            "generated_script": {"title": "Ready", "sections": [{"dialog": []}]},
        }

        with patch.object(service, "get_active_task", return_value="stale-task"):
            with patch("services.async_podcast_agent_service.SessionService.get_session", return_value={"state": session_state}):
                response = __import__("asyncio").run(service.chat(request))

        self.assertFalse(response["is_processing"])
        self.assertEqual(response["stage"], "script")
        self.assertIsNone(response.get("task_id"))
        self.assertEqual(json.loads(response["session_state"])["generated_script"]["title"], "Ready")

    def test_check_status_returns_ready_script_when_celery_pending(self):
        service = PodcastAgentService.__new__(PodcastAgentService)
        service.local_tasks = {}
        service.local_session_tasks = {}

        request = SimpleNamespace(session_id="session-pending", task_id="celery-task")
        session_state = {
            "stage": "script",
            "response": "Script generated successfully",
            "generated_script": {"title": "Ready", "sections": [{"dialog": []}]},
        }

        class DummyAsyncResult:
            state = "PENDING"

        with patch.object(service, "_browser_recording", return_value=None):
            with patch("services.async_podcast_agent_service.agent_chat.AsyncResult", return_value=DummyAsyncResult()):
                with patch("services.async_podcast_agent_service.SessionService.get_session", return_value={"state": session_state}):
                    response = __import__("asyncio").run(service.check_result_status(request))

        self.assertFalse(response["is_processing"])
        self.assertEqual(response["stage"], "script")
        self.assertIsNone(response.get("task_id"))


if __name__ == "__main__":
    unittest.main()
