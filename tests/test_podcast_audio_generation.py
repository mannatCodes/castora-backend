import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from agents import audio_generate_agent
import services.internal_session_service as internal_session_service
from utils import text_to_audio_openai


class DummyAgent:
    def __init__(self):
        self.session_id = "test-session"


class DummySessionService:
    sessions = {}

    @classmethod
    def get_session(cls, session_id):
        return cls.sessions.setdefault(
            session_id,
            {
                "state": {
                    "generated_script": {
                        "title": "Test Podcast",
                        "sections": [{"dialog": [{"speaker": "ALEX", "text": "Hello world"}]}],
                    }
                }
            },
        )

    @classmethod
    def save_session(cls, session_id, session_state):
        cls.sessions[session_id] = {"state": session_state}


def _write_tone(path, duration_seconds=5.0, sampling_rate=24000):
    t = np.linspace(0, duration_seconds, int(sampling_rate * duration_seconds), endpoint=False)
    generated_audio = 0.1 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    sf.write(path, generated_audio, sampling_rate)


class AudioGenerationTests(unittest.TestCase):
    def setUp(self):
        DummySessionService.sessions = {}

    def test_audio_generation_uses_real_tts_by_default(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            generated_path = os.path.join(tmp_dir, "generated.wav")
            _write_tone(generated_path)

            with patch.dict(os.environ, {"PODCAST_STUDIO_USE_REAL_TTS": "1"}, clear=False):
                with patch.object(internal_session_service, "SessionService", DummySessionService):
                    with patch.object(audio_generate_agent, "PODCAST_AUDIO_FOLDER", tmp_dir):
                        calls = []

                        def fake_generate_podcast_audio(**kwargs):
                            calls.append(kwargs)
                            return generated_path

                        with patch.object(audio_generate_agent, "generate_podcast_audio", side_effect=fake_generate_podcast_audio):
                            with patch.object(audio_generate_agent, "_create_placeholder_audio", return_value=None):
                                result = audio_generate_agent.audio_generate_agent_run(DummyAgent())

                        self.assertTrue(calls, "expected the real TTS path to be used by default")
                        self.assertIn("generated the audio", result.lower())

    def test_audio_generation_rejects_silent_tts_and_uses_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            silent_path = os.path.join(tmp_dir, "silent.wav")
            silent_audio = np.zeros((24000,), dtype=np.float32)
            sf.write(silent_path, silent_audio, 24000)

            valid_path = os.path.join(tmp_dir, "generated_valid.wav")
            _write_tone(valid_path)

            with patch.dict(
                os.environ,
                {
                    "PODCAST_STUDIO_USE_REAL_TTS": "1",
                    "PODCAST_STUDIO_TTS_FALLBACKS": "1",
                    "OPENAI_API_KEY": "test-openai-key",
                    "ELEVENSLAB_API_KEY": "",
                },
                clear=False,
            ):
                with patch.object(internal_session_service, "SessionService", DummySessionService):
                    with patch.object(audio_generate_agent, "PODCAST_AUDIO_FOLDER", tmp_dir):
                        calls = []

                        def fake_generate_podcast_audio(**kwargs):
                            calls.append(kwargs)
                            return silent_path if len(calls) == 1 else valid_path

                        with patch.object(audio_generate_agent, "generate_podcast_audio", side_effect=fake_generate_podcast_audio):
                            with patch.object(audio_generate_agent, "_create_placeholder_audio", return_value=None):
                                result = audio_generate_agent.audio_generate_agent_run(DummyAgent())

                        self.assertEqual(len(calls), 2, "expected a fallback TTS engine call after silent audio was rejected")
                        self.assertTrue(
                            "windows" in result.lower() or "openai" in result.lower(),
                            "expected a usable local or hosted TTS fallback after silent audio was rejected",
                        )

    def test_audio_generation_rejects_too_short_audio(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            short_path = os.path.join(tmp_dir, "short.wav")
            _write_tone(short_path, duration_seconds=1.0)

            with patch.dict(
                os.environ,
                {
                    "PODCAST_STUDIO_USE_REAL_TTS": "1",
                    "PODCAST_STUDIO_TTS_FALLBACKS": "0",
                },
                clear=False,
            ):
                with patch.object(internal_session_service, "SessionService", DummySessionService):
                    with patch.object(audio_generate_agent, "PODCAST_AUDIO_FOLDER", tmp_dir):
                        with patch.object(audio_generate_agent, "generate_podcast_audio", return_value=short_path):
                            result = audio_generate_agent.audio_generate_agent_run(DummyAgent())

            self.assertIn("failed to generate podcast audio", result.lower())

    def test_audio_generation_never_replaces_failed_speech_with_a_tone(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "PODCAST_STUDIO_USE_REAL_TTS": "1",
                    "PODCAST_STUDIO_TTS_FALLBACKS": "0",
                    "PODCAST_STUDIO_ALLOW_PLACEHOLDER_AUDIO": "true",
                },
                clear=False,
            ):
                with patch.object(internal_session_service, "SessionService", DummySessionService):
                    with patch.object(audio_generate_agent, "PODCAST_AUDIO_FOLDER", tmp_dir):
                        with patch.object(audio_generate_agent, "generate_podcast_audio", return_value=None):
                            with patch.object(audio_generate_agent, "_create_placeholder_audio") as placeholder:
                                result = audio_generate_agent.audio_generate_agent_run(DummyAgent())

            self.assertIn("failed to generate podcast audio", result.lower())
            placeholder.assert_not_called()
            state = DummySessionService.sessions["test-session"]["state"]
            self.assertNotIn("audio_url", state)
            self.assertFalse(state["show_audio_for_confirmation"])

    def test_extract_script_entries_normalizes_speakers(self):
        script_data = {
            "sections": [
                {
                    "dialog": [
                        {"speaker": "alex", "text": " Hello   from Alex "},
                        {"speaker": "Speaker 2", "text": "Morgan is here."},
                        {"speaker": "Narrator", "text": "This should be skipped."},
                    ]
                }
            ]
        }

        entries = audio_generate_agent._extract_script_entries(script_data)

        self.assertEqual(
            entries,
            [
                {"text": "Hello from Alex", "speaker": 1},
                {"text": "Morgan is here.", "speaker": 2},
            ],
        )

    def test_local_windows_engine_is_available_as_a_fallback(self):
        with patch.object(audio_generate_agent.os, "name", "nt"):
            with patch.dict(os.environ, {"PODCAST_STUDIO_TTS_FALLBACKS": "1"}, clear=False):
                engines = audio_generate_agent._configured_tts_engines("elevenlabs")

        self.assertEqual(engines[0], "elevenlabs")
        self.assertIn("edge", engines)
        self.assertIn("windows", engines)

    def test_production_edge_preference_repairs_saved_paid_engine_selection(self):
        with patch.dict(
            os.environ,
            {
                "PODCAST_STUDIO_PREFER_EDGE_TTS": "true",
                "PODCAST_STUDIO_TTS_FALLBACKS": "true",
                "ELEVENLABS_API_KEY": "test-elevenlabs-key",
            },
            clear=False,
        ):
            engines = audio_generate_agent._configured_tts_engines("elevenlabs")

        self.assertEqual(engines[0], "edge")
        self.assertIn("elevenlabs", engines)

    def test_windows_session_prefers_configured_hosted_tts(self):
        with patch.object(audio_generate_agent.os, "name", "nt"):
            with patch.dict(
                os.environ,
                {
                    "PODCAST_STUDIO_TTS_FALLBACKS": "1",
                    "PODCAST_STUDIO_PREFER_HOSTED_TTS": "1",
                    "ELEVENLABS_API_KEY": "test-elevenlabs-key",
                },
                clear=False,
            ):
                engines = audio_generate_agent._configured_tts_engines("windows")

        self.assertEqual(engines[0], "elevenlabs")
        self.assertIn("edge", engines)
        self.assertIn("windows", engines)

    def test_openai_tts_uses_openai_api_key_when_available(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "podcast.wav")

            def fake_sf_write(path, audio, samplerate):
                with open(path, "wb") as f:
                    f.write(b"RIFF")

            with patch("utils.text_to_audio_openai.OpenAI") as openai_cls, \
                 patch("utils.text_to_audio_openai.load_api_key", side_effect=lambda key_name=None: "groq-key" if key_name is None else "openai-key") as load_api_key_mock, \
                 patch("utils.text_to_audio_openai.text_to_speech_openai", return_value=(np.ones(16, dtype=np.float32), 24000)) as text_to_speech_mock, \
                 patch("utils.text_to_audio_openai.sf.write", side_effect=fake_sf_write) as sf_write_mock:
                result = text_to_audio_openai.create_podcast(
                    script=[{"text": "hello world", "speaker": 1}],
                    output_path=output_path,
                    api_key=None,
                )

            self.assertEqual(openai_cls.call_args.kwargs["api_key"], "openai-key")
            self.assertEqual(load_api_key_mock.call_args_list[0].args[0], "OPENAI_API_KEY")
            self.assertEqual(result, output_path)
            text_to_speech_mock.assert_called_once()
            sf_write_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
