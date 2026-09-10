import os
import threading
from typing import Any, Callable, Optional
from utils.load_api_keys import load_api_key

_TTS_ENGINES = {}
_tts_error = threading.local()
TTS_OPENAI_MODEL = "gpt-4o-mini-tts"
TTS_ELEVENLABS_MODEL = "eleven_multilingual_v2"


def _elevenlabs_voice_map() -> dict:
    """Use account-owned voices when configured.

    ElevenLabs free accounts cannot synthesize with Voice Library IDs. A
    Voice Design/owned voice ID works on the account that created it. One
    voice is enough for a podcast; it is used for both speakers if a second
    ID is not supplied.
    """
    first_voice = os.environ.get("ELEVENLABS_VOICE_ID_1", "").strip()
    second_voice = os.environ.get("ELEVENLABS_VOICE_ID_2", "").strip()
    if first_voice:
        return {1: first_voice, 2: second_voice or first_voice}
    return {1: "21m00Tcm4TlvDq8ikWAM", 2: "pNInz6obpgDQGcFmaJgB"}


def register_tts_engine(name: str, generator_func: Callable):
    _TTS_ENGINES[name.lower()] = generator_func


def get_last_tts_error() -> str:
    """Return the last provider error from the current worker thread."""
    return getattr(_tts_error, "message", "")


def _set_last_tts_error(message: str) -> None:
    # Provider exceptions may contain request metadata. Keep the useful reason
    # for the Studio UI, but never surface an API key.
    message = str(message or "").replace("\n", " ")[:500]
    _tts_error.message = message


def generate_podcast_audio(
    script: Any,
    output_path: str,
    tts_engine: str = "edge",
    language_code: str = "en",
    silence_duration: float = 0.7,
    voice_map=None,
) -> Optional[str]:

    _set_last_tts_error("")

    output_dir = os.path.dirname(output_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    engine_name = tts_engine.lower()

    if engine_name not in _TTS_ENGINES:
        message = f"Unsupported TTS engine: {tts_engine}"
        print(message)
        _set_last_tts_error(message)
        return None

    try:
        result = _TTS_ENGINES[engine_name](
            script=script, output_path=output_path, language_code=language_code, silence_duration=silence_duration, voice_map=voice_map
        )
        if not result and not get_last_tts_error():
            _set_last_tts_error(f"{tts_engine} returned no audio data")
        return result
    except Exception as e:
        import traceback

        print(f"Error generating audio with {tts_engine}: {e}")
        _set_last_tts_error(f"{tts_engine}: {e}")
        traceback.print_exc()
        return None


def register_default_engines():
    def windows_generator(script, output_path, language_code, silence_duration, voice_map):
        from utils.text_to_audio_windows import create_podcast as windows_create_podcast

        return windows_create_podcast(
            script=script,
            output_path=output_path,
            silence_duration=silence_duration,
        )

    def elevenlabs_generator(script, output_path, language_code, silence_duration, voice_map):
        from utils.text_to_audio_elevenslab import (
            create_podcast as elevenlabs_create_podcast,
            get_last_elevenlabs_error,
        )

        if voice_map is None:
            voice_map = _elevenlabs_voice_map()
        result = elevenlabs_create_podcast(
            script=script,
            output_path=output_path,
            silence_duration=silence_duration,
            voice_map=voice_map,
            elevenlabs_model=TTS_ELEVENLABS_MODEL,
            api_key=load_api_key("ELEVENLABS_API_KEY"),
        )
        if not result:
            raise RuntimeError(get_last_elevenlabs_error() or "ElevenLabs returned no audio data")
        return result

    def kokoro_generator(script, output_path, language_code, silence_duration, voice_map):
        from utils.text_to_audio_kokoro import create_podcast as kokoro_create_podcast

        kokoro_lang_code = "b"
        if language_code == "hi":
            kokoro_lang_code = "h"
        return kokoro_create_podcast(
            script=script, output_path=output_path, silence_duration=silence_duration, sampling_rate=24_000, lang_code=kokoro_lang_code
        )

    def edge_generator(script, output_path, language_code, silence_duration, voice_map):
        from utils.text_to_audio_edge import create_podcast as edge_create_podcast

        return edge_create_podcast(
            script=script,
            output_path=output_path,
            language_code=language_code,
            silence_duration=silence_duration,
        )

    def openai_generator(script, output_path, language_code, silence_duration, voice_map):
        from utils.text_to_audio_openai import create_podcast as openai_create_podcast

        if voice_map is None:
            voice_map = {1: "alloy", 2: "nova"}
            if language_code == "hi":
                voice_map = {1: "alloy", 2: "nova"}
        model = TTS_OPENAI_MODEL
        return openai_create_podcast(
            script=script,
            output_path=output_path,
            silence_duration=silence_duration,
            lang_code=language_code,
            model=model,
            voice_map=voice_map,
            api_key=load_api_key("OPENAI_API_KEY"),
        )

    register_tts_engine("elevenlabs", elevenlabs_generator)
    register_tts_engine("kokoro", kokoro_generator)
    register_tts_engine("edge", edge_generator)
    register_tts_engine("openai", openai_generator)
    if os.name == "nt":
        register_tts_engine("windows", windows_generator)


register_default_engines()
