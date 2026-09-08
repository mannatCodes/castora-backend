import os
from pathlib import Path
from dotenv import load_dotenv


env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


def load_api_key(key_name="GROQ_API_KEY"):
    key_variants = {
        "GROQ_API_KEY": ["GROQ_API_KEY", "Groq_API_KEY", "groq_api_key"],
        "Groq_API_KEY": ["GroQ_API_KEY", "Groq_API_KEY", "GROQ_API_KEY", "groq_api_key"],
        "groq_api_key": ["groq_api_key", "GROQ_API_KEY", "Groq_API_KEY"],
        "OPENAI_API_KEY": ["OPENAI_API_KEY", "OpenAI_API_KEY", "openai_api_key"],
        "ELEVENSLAB_API_KEY": ["ELEVENSLAB_API_KEY", "Elevenlabs_API_KEY", "elevenlab_api_key", "ELEVENLABS_API_KEY"],
        "ELEVENLABS_API_KEY": ["ELEVENLABS_API_KEY", "ELEVENSLAB_API_KEY", "Elevenlabs_API_KEY", "elevenlab_api_key"],
    }
    for candidate in key_variants.get(key_name, [key_name]):
        value = os.environ.get(candidate)
        if value:
            return value
    return None
