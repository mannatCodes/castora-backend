import unittest
from unittest.mock import patch

from services import celery_tasks


class SourceDiscoveryRelevanceTests(unittest.TestCase):
    def test_local_results_must_match_specific_topic_terms(self):
        topic = "girls trafficking in bangladesh"
        local_results = [
            {
                "title": "Bangladesh reports 44 deaths due to floods; capital Dhaka deluged",
                "description": "Severe flooding in Bangladesh leads to deaths and displacement.",
                "full_text": "Bangladesh flood rain death",
            },
            {
                "title": "5 Delhi Government School Girls Selected For US Space Science Workshop",
                "description": "Delhi school girls selected for workshop.",
                "full_text": "Access Denied",
            },
            {
                "title": "Bangladesh police uncover girls trafficking ring",
                "description": "Authorities report arrests tied to trafficking across the border.",
                "full_text": "Bangladesh girls trafficking investigation",
            },
        ]

        filtered = celery_tasks._filter_relevant_sources(topic, local_results)

        self.assertEqual(len(filtered), 1)
        self.assertIn("trafficking", filtered[0]["title"].lower())

    def test_prepare_sources_uses_google_search_when_local_results_are_irrelevant(self):
        class DummySessionService:
            state = {}

            @staticmethod
            def get_session(session_id):
                return {"state": DummySessionService.state}

            @staticmethod
            def save_session(session_id, session_state):
                DummySessionService.state = session_state

        live_result = {
            "url": "https://example.com/bangladesh-trafficking",
            "title": "Bangladesh police investigate girls trafficking network",
            "description": "A report on trafficking risks facing girls in Bangladesh.",
            "source_name": "google_search",
            "tool_used": "google_live_search",
            "published_date": "",
            "is_scrapping_required": False,
            "full_text": "girls trafficking Bangladesh",
        }

        with patch("services.internal_session_service.SessionService", DummySessionService):
            with patch.object(celery_tasks, "_search_local_articles", return_value=[]):
                with patch.object(celery_tasks, "_search_live_google_sources", return_value=[live_result]) as google_search:
                    with patch.object(celery_tasks, "_search_live_news_sources", side_effect=AssertionError("news fallback should not run")):
                        response = celery_tasks._prepare_sources_without_ai(
                            "session-live",
                            "create a podcast about girls trafficking in bangladesh",
                        )

        google_search.assert_called_once_with("girls trafficking in bangladesh")
        self.assertEqual(response["stage"], "source_selection")
        self.assertFalse(response["is_processing"])
        self.assertIn("Google live", response["response"])
        self.assertEqual(DummySessionService.state["search_results"][0]["tool_used"], "google_live_search")
        self.assertFalse(DummySessionService.state["search_results"][0]["is_scrapping_required"])

    def test_prepare_sources_uses_google_when_local_content_is_too_thin(self):
        class DummySessionService:
            state = {}

            @staticmethod
            def get_session(session_id):
                return {"state": DummySessionService.state}

            @staticmethod
            def save_session(session_id, session_state):
                DummySessionService.state = session_state

        thin_local_result = {
            "url": "https://example.com/local",
            "title": "Relevant but incomplete",
            "description": "A short local match.",
            "full_text": "A short local match.",
        }
        live_result = {
            "url": "https://example.com/google",
            "title": "Detailed Google result",
            "description": "A detailed, current source from Google.",
            "full_text": "A detailed, current source from Google.",
            "tool_used": "google_live_search",
        }

        with patch("services.internal_session_service.SessionService", DummySessionService):
            with patch.object(celery_tasks, "_search_local_articles", return_value=[thin_local_result]):
                with patch.object(celery_tasks, "_search_live_google_sources", return_value=[live_result]) as google_search:
                    response = celery_tasks._prepare_sources_without_ai("session-thin", "create a podcast about climate change")

        google_search.assert_called_once_with("climate change")
        self.assertIn("Google live", response["response"])
        self.assertEqual(DummySessionService.state["search_results"], [live_result])

    def test_prepare_sources_uses_google_news_only_if_google_search_has_no_matches(self):
        class DummySessionService:
            state = {}

            @staticmethod
            def get_session(session_id):
                return {"state": DummySessionService.state}

            @staticmethod
            def save_session(session_id, session_state):
                DummySessionService.state = session_state

        news_result = {
            "url": "https://example.com/bangladesh-trafficking-news",
            "title": "Bangladesh trafficking case raises concern for girls",
            "description": "News report about girls trafficking in Bangladesh.",
            "source_name": "Example News",
            "tool_used": "google_news_live_search",
            "published_date": "",
            "is_scrapping_required": False,
            "full_text": "girls trafficking Bangladesh",
        }

        with patch("services.internal_session_service.SessionService", DummySessionService):
            with patch.object(celery_tasks, "_search_local_articles", return_value=[]):
                with patch.object(celery_tasks, "_search_live_google_sources", return_value=[]) as google_search:
                    with patch.object(celery_tasks, "_search_live_news_sources", return_value=[news_result]) as news_search:
                        response = celery_tasks._prepare_sources_without_ai(
                            "session-news",
                            "create a podcast about girls trafficking in bangladesh",
                        )

        google_search.assert_called_once_with("girls trafficking in bangladesh")
        news_search.assert_called_once_with("girls trafficking in bangladesh")
        self.assertEqual(response["stage"], "source_selection")
        self.assertIn("Google News live", response["response"])
        self.assertEqual(DummySessionService.state["search_results"][0]["tool_used"], "google_news_live_search")
        self.assertFalse(DummySessionService.state["search_results"][0]["is_scrapping_required"])


if __name__ == "__main__":
    unittest.main()
