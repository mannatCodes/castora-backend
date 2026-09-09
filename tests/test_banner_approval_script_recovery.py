import json
from unittest.mock import patch


def test_banner_approval_restores_the_approved_script_before_audio(monkeypatch):
    """An interrupted banner step must not discard the user-approved draft."""
    from services import celery_tasks

    class DummySessionService:
        sessions = {
            "session-banner": {
                "state": {
                    "stage": "banner",
                    "generated_script": {},
                    "approved_script": {
                        "title": "Climate update",
                        "sections": [
                            {"dialog": [{"speaker": "ALEX", "text": "The approved script."}]}
                        ],
                    },
                    "show_banner_for_confirmation": True,
                }
            }
        }

        @classmethod
        def get_session(cls, session_id):
            return cls.sessions[session_id]

        @classmethod
        def save_session(cls, session_id, state):
            cls.sessions[session_id] = {"state": state}

    received_scripts = []

    def fake_audio_agent(agent):
        state = DummySessionService.get_session(agent.session_id)["state"]
        received_scripts.append(state["generated_script"])
        state["audio_url"] = "podcast.wav"
        return "Audio is ready."

    monkeypatch.setattr("services.internal_session_service.SessionService", DummySessionService)
    with patch.object(celery_tasks, "audio_generate_agent_run", side_effect=fake_audio_agent):
        response = celery_tasks._handle_stage_approval_directly(
            "session-banner",
            "I approve this banner. It looks good!",
            DummySessionService.get_session("session-banner")["state"],
        )

    state = json.loads(response["session_state"])
    assert received_scripts[0]["title"] == "Climate update"
    assert state["generated_script"]["title"] == "Climate update"
    assert state["show_audio_for_confirmation"] is True

