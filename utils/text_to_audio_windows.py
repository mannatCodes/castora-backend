"""Offline Windows SAPI speech fallback for local Castora development."""

import os
from typing import Any, Optional


def create_podcast(
    script: Any,
    output_path: str,
    silence_duration: float = 0.7,
    **_: Any,
) -> Optional[str]:
    """Narrate all dialog into a WAV file using Windows' built-in speech API."""
    if os.name != "nt":
        print("Windows SAPI TTS is only available on Windows")
        return None

    try:
        import win32com.client

        entries = script.entries if hasattr(script, "entries") else script
        lines = []
        for entry in entries:
            text = entry.get("text", "") if isinstance(entry, dict) else getattr(entry, "text", "")
            text = " ".join(str(text or "").split())
            if text:
                lines.append(text)
        if not lines:
            print("No script dialog was available for Windows SAPI TTS")
            return None

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(os.path.abspath(output_path), 3, False)  # SSFMCreateForWrite
        voice.AudioOutputStream = stream
        voice.Speak((" ".join(lines)), 0)
        stream.Close()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 44:
            return output_path
    except Exception as error:
        print(f"Windows SAPI TTS failed: {error}")
    return None
