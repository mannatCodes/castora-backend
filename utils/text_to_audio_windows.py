"""Offline Windows SAPI speech fallback for local Castora development."""

import base64
import os
import subprocess
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

    absolute_output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(absolute_output_path), exist_ok=True)
    text = " ".join(lines)

    # Prefer pywin32 when it happens to be installed, but do not require it.
    # It is not in requirements.txt and its missing import used to make the
    # only offline TTS option fail on a standard Windows installation.
    try:
        import win32com.client

        voice = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(absolute_output_path, 3, False)  # SSFMCreateForWrite
        voice.AudioOutputStream = stream
        voice.Speak(text, 0)
        stream.Close()
    except Exception as pywin32_error:
        print(f"pywin32 SAPI unavailable; using built-in PowerShell speech: {pywin32_error}")
        try:
            # System.Speech ships with Windows.  Encoding the script prevents
            # quotes and punctuation in a podcast from being interpreted by a
            # shell, and keeps this fallback free of extra Python packages.
            def quote_ps(value: str) -> str:
                return "'" + value.replace("'", "''") + "'"

            command = (
                "Add-Type -AssemblyName System.Speech; "
                "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$speaker.SetOutputToWaveFile({quote_ps(absolute_output_path)}); "
                f"$speaker.Speak({quote_ps(text)}); "
                "$speaker.Dispose()"
            )
            encoded_command = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as powershell_error:
            print(f"Windows SAPI TTS failed: {powershell_error}")
            return None

    if os.path.exists(absolute_output_path) and os.path.getsize(absolute_output_path) > 44:
        return absolute_output_path
    return None
