import asyncio
import os
import tempfile
from typing import Any

import edge_tts
import miniaudio
import numpy as np
import soundfile as sf


def _get_voice(language_code: str, speaker: int) -> str:
    """Return an Edge TTS voice for the requested language."""

    if language_code == "hi":
        voices = {
            1: "hi-IN-SwaraNeural",
            2: "hi-IN-MadhurNeural",
        }
    else:
        voices = {
            1: "en-US-AriaNeural",
            2: "en-US-GuyNeural",
        }

    return voices.get(speaker, voices[1])


async def _generate_segment(
    text: str,
    voice: str,
    output_path: str,
):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def _run_async(coro):
    """
    Run an async coroutine safely from synchronous code.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import threading

    result = []
    error = []

    def runner():
        try:
            result.append(asyncio.run(coro))
        except Exception as exc:
            error.append(exc)

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()

    if error:
        raise error[0]

    return result[0] if result else None


def _decode_mp3_to_wav_data(mp3_path: str):
    """
    Decode an Edge TTS MP3 file into PCM samples using miniaudio.
    This avoids requiring FFmpeg.
    """

    decoded = miniaudio.decode_file(
        mp3_path,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
    )

    audio_bytes = decoded.samples

    audio = np.frombuffer(
        audio_bytes,
        dtype=np.int16,
    ).astype(np.float32)

    audio /= 32768.0

    return audio, decoded.sample_rate


def _create_silence(
    duration: float,
    sample_rate: int,
) -> np.ndarray:
    """Create silence as a NumPy array."""

    if duration <= 0:
        return np.zeros(0, dtype=np.float32)

    return np.zeros(
        int(duration * sample_rate),
        dtype=np.float32,
    )


def create_podcast(
    script: Any,
    output_path: str,
    language_code: str = "en",
    silence_duration: float = 0.7,
    sampling_rate: int = 24000,
    **kwargs,
) -> str:

    output_dir = os.path.dirname(output_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Convert the script into a list if necessary.
    if isinstance(script, dict):
        script = script.get(
            "script",
            script.get("segments", []),
        )

    if not isinstance(script, list):
        raise ValueError(
            "Podcast script must be a list of dialogue segments."
        )

    generated_segments = []

    with tempfile.TemporaryDirectory() as temp_dir:

        for index, segment in enumerate(script):

            if not isinstance(segment, dict):
                continue

            text = str(
                segment.get("text", "")
            ).strip()

            if not text:
                continue

            speaker = segment.get(
                "speaker",
                1,
            )

            try:
                speaker = int(speaker)
            except (TypeError, ValueError):
                speaker = 1

            voice = _get_voice(
                language_code,
                speaker,
            )

            mp3_path = os.path.join(
                temp_dir,
                f"segment_{index}.mp3",
            )

            print(
                f"Generating Edge TTS segment "
                f"{index + 1} using {voice}"
            )

            _run_async(
                _generate_segment(
                    text=text,
                    voice=voice,
                    output_path=mp3_path,
                )
            )

            if not os.path.exists(mp3_path):
                raise RuntimeError(
                    f"Edge TTS failed to create audio "
                    f"for segment {index}."
                )

            audio, sample_rate = _decode_mp3_to_wav_data(
                mp3_path
            )

            if audio.size == 0:
                raise RuntimeError(
                    f"Edge TTS generated empty audio "
                    f"for segment {index}."
                )

            # Resample if Edge's sample rate differs.
            if sample_rate != sampling_rate:
                import scipy.signal

                new_length = int(
                    len(audio)
                    * sampling_rate
                    / sample_rate
                )

                audio = scipy.signal.resample(
                    audio,
                    new_length,
                ).astype(np.float32)

                sample_rate = sampling_rate

            generated_segments.append(
                audio
            )

    if not generated_segments:
        raise RuntimeError(
            "Edge TTS generated no audio."
        )

    # Combine all dialogue segments with silence.
    silence = _create_silence(
        silence_duration,
        sampling_rate,
    )

    combined_parts = []

    for index, segment_audio in enumerate(
        generated_segments
    ):
        combined_parts.append(segment_audio)

        if index < len(generated_segments) - 1:
            combined_parts.append(silence)

    full_audio = np.concatenate(
        combined_parts
    ).astype(np.float32)

    # Prevent clipping.
    peak = float(
        np.max(np.abs(full_audio))
    )

    if peak > 0.95:
        full_audio = (
            full_audio
            / peak
            * 0.95
        )

    # IMPORTANT:
    # Write a real WAV file.
    sf.write(
        output_path,
        full_audio,
        sampling_rate,
        subtype="PCM_16",
    )

    if not os.path.exists(output_path):
        raise RuntimeError(
            "Edge TTS failed to create final WAV file."
        )

    file_size = os.path.getsize(
        output_path
    )

    if file_size == 0:
        raise RuntimeError(
            "Edge TTS created an empty WAV file."
        )

    print(
        f"Edge TTS audio created: "
        f"{output_path} "
        f"({file_size / 1024:.1f} KB)"
    )

    return output_path