import os
from typing import List, Tuple, Optional, Any
import tempfile
import numpy as np
import soundfile as sf
from elevenlabs.client import ElevenLabs

TEXT_TO_SPEECH_MODEL = "eleven_multilingual_v2"
# Built-in ElevenLabs voices. Use IDs rather than display names: restricted
# API keys commonly cannot list voices, but can still synthesize with these.
DEFAULT_VOICE_MAP = {
    1: "21m00Tcm4TlvDq8ikWAM",  # Rachel
    2: "pNInz6obpgDQGcFmaJgB",  # Adam
}


def create_silence_audio(silence_duration: float, sampling_rate: int) -> np.ndarray:
    if sampling_rate <= 0:
        print(f"Warning: Invalid sampling rate ({sampling_rate}) for silence generation")
        return np.zeros(0, dtype=np.float32)
    return np.zeros(int(sampling_rate * silence_duration), dtype=np.float32)


def combine_audio_segments(audio_segments: List[np.ndarray], silence_duration: float, sampling_rate: int) -> np.ndarray:
    if not audio_segments:
        return np.zeros(0, dtype=np.float32)
    if sampling_rate <= 0:
        combined = np.concatenate(audio_segments) if audio_segments else np.zeros(0, dtype=np.float32)
    else:
        silence = create_silence_audio(silence_duration, sampling_rate)
        combined_with_silence = []
        for i, segment in enumerate(audio_segments):
            combined_with_silence.append(segment)
            if i < len(audio_segments) - 1:
                combined_with_silence.append(silence)
        combined = np.concatenate(combined_with_silence)
    max_amp = np.max(np.abs(combined))
    if max_amp > 0:
        combined = combined / max_amp * 0.95
    return combined


def write_to_disk(output_path: str, audio_data: np.ndarray, sampling_rate: int) -> None:
    if sampling_rate <= 0:
        print(f"Error: Cannot write audio file with invalid sampling rate ({sampling_rate})")
        return
    try:
        sf.write(output_path, audio_data, sampling_rate)
    except Exception as e:
        print(f"Error writing audio file '{output_path}': {e}")


def text_to_speech_elevenlabs(
    client: ElevenLabs,
    text: str,
    speaker_id: int,
    voice_map: Optional[dict] = None,
    model_id: str = TEXT_TO_SPEECH_MODEL,
) -> Optional[Tuple[np.ndarray, int]]:
    if not text.strip():
        return None
    voice_map = voice_map or DEFAULT_VOICE_MAP
    voice_name_or_id = voice_map.get(speaker_id)
    if not voice_name_or_id:
        print(f"No voice found for speaker_id {speaker_id}")
        return None
    try:
        # Request PCM directly. This avoids ffmpeg/MP3 decoding failures and
        # avoids the legacy name-to-voice lookup that needs voices_read.
        audio_generator = client.text_to_speech.convert(
            voice_id=voice_name_or_id,
            text=text,
            model_id=model_id,
            output_format="pcm_24000",
        )
        audio_data = b"".join(chunk for chunk in audio_generator if chunk)
        if not audio_data:
            return None
        samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        return (samples, 24_000) if samples.size else None

    except Exception as e:
        print(f"Error during ElevenLabs API call: {e}")
        import traceback

        traceback.print_exc()
        return None


def create_podcast(
    script: Any,
    output_path: str,
    silence_duration: float = 0.7,
    sampling_rate: int = 24_000,
    lang_code: str = "en",
    elevenlabs_model: str = "eleven_multilingual_v2",
    voice_map: Optional[dict] = None,
    api_key: str = None,
) -> str:
    if not api_key:
        print("ElevenLabs API key is not configured")
        return None
    try:
        client = ElevenLabs(api_key=api_key)
    except Exception as e:
        print(f"Fatal Error: Failed to initialize ElevenLabs client: {e}")
        return None
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    generated_segments = []
    voice_map = voice_map or DEFAULT_VOICE_MAP
    determined_sampling_rate = -1
    entries = script.entries if hasattr(script, "entries") else script
    for i, entry in enumerate(entries):
        if hasattr(entry, "speaker"):
            speaker_id = entry.speaker
            entry_text = entry.text
        else:
            speaker_id = entry["speaker"]
            entry_text = entry["text"]
        result = text_to_speech_elevenlabs(
            client=client,
            text=entry_text,
            speaker_id=speaker_id,
            voice_map=voice_map,
            model_id=elevenlabs_model,
        )
        if result:
            segment_audio, segment_rate = result

            if determined_sampling_rate == -1:
                determined_sampling_rate = segment_rate
            elif determined_sampling_rate != segment_rate:
                try:
                    import librosa

                    segment_audio = librosa.resample(
                        segment_audio,
                        orig_sr=segment_rate,
                        target_sr=determined_sampling_rate,
                    )
                except ImportError:
                    determined_sampling_rate = segment_rate
                except Exception:
                    pass
            generated_segments.append(segment_audio)
    if not generated_segments or determined_sampling_rate <= 0:
        return None
    full_audio = combine_audio_segments(generated_segments, silence_duration, determined_sampling_rate)
    if full_audio.size == 0:
        return None
    write_to_disk(output_path, full_audio, determined_sampling_rate)
    if os.path.exists(output_path):
        return output_path
    else:
        return None
