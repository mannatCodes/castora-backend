from agno.storage.sqlite import SqliteStorage
from db.config import APP_ROOT, get_agent_session_db_path
import json
import os

AGENT_MODEL = "llama-3.3-70b-versatile"

AVAILABLE_LANGS = [
    {"code": "en", "name": "English"},
    {"code": "zh", "name": "Chinese"},
    {"code": "de", "name": "German"},
    {"code": "es", "name": "Spanish"},
    {"code": "ru", "name": "Russian"},
    {"code": "ko", "name": "Korean"},
    {"code": "fr", "name": "French"},
    {"code": "ja", "name": "Japanese"},
    {"code": "pt", "name": "Portuguese"},
    {"code": "tr", "name": "Turkish"},
    {"code": "pl", "name": "Polish"},
    {"code": "ca", "name": "Catalan"},
    {"code": "nl", "name": "Dutch"},
    {"code": "ar", "name": "Arabic"},
    {"code": "sv", "name": "Swedish"},
    {"code": "it", "name": "Italian"},
    {"code": "id", "name": "Indonesian"},
    {"code": "hi", "name": "Hindi"},
    {"code": "fi", "name": "Finnish"},
    {"code": "vi", "name": "Vietnamese"},
    {"code": "he", "name": "Hebrew"},
    {"code": "uk", "name": "Ukrainian"},
    {"code": "el", "name": "Greek"},
    {"code": "ms", "name": "Malay"},
    {"code": "cs", "name": "Czech"},
    {"code": "ro", "name": "Romanian"},
    {"code": "da", "name": "Danish"},
    {"code": "hu", "name": "Hungarian"},
    {"code": "ta", "name": "Tamil"},
    {"code": "no", "name": "Norwegian"},
    {"code": "th", "name": "Thai"},
    {"code": "ur", "name": "Urdu"},
    {"code": "hr", "name": "Croatian"},
    {"code": "bg", "name": "Bulgarian"},
    {"code": "lt", "name": "Lithuanian"},
    {"code": "la", "name": "Latin"},
    {"code": "mi", "name": "Maori"},
    {"code": "ml", "name": "Malayalam"},
    {"code": "cy", "name": "Welsh"},
    {"code": "sk", "name": "Slovak"},
    {"code": "te", "name": "Telugu"},
    {"code": "fa", "name": "Persian"},
    {"code": "lv", "name": "Latvian"},
    {"code": "bn", "name": "Bengali"},
    {"code": "sr", "name": "Serbian"},
    {"code": "az", "name": "Azerbaijani"},
    {"code": "sl", "name": "Slovenian"},
    {"code": "kn", "name": "Kannada"},
    {"code": "et", "name": "Estonian"},
    {"code": "mk", "name": "Macedonian"},
    {"code": "br", "name": "Breton"},
    {"code": "eu", "name": "Basque"},
    {"code": "is", "name": "Icelandic"},
    {"code": "hy", "name": "Armenian"},
    {"code": "ne", "name": "Nepali"},
    {"code": "mn", "name": "Mongolian"},
    {"code": "bs", "name": "Bosnian"},
    {"code": "kk", "name": "Kazakh"},
    {"code": "sq", "name": "Albanian"},
    {"code": "sw", "name": "Swahili"},
    {"code": "gl", "name": "Galician"},
    {"code": "mr", "name": "Marathi"},
    {"code": "pa", "name": "Punjabi"},
    {"code": "si", "name": "Sinhala"},
    {"code": "km", "name": "Khmer"},
    {"code": "sn", "name": "Shona"},
    {"code": "yo", "name": "Yoruba"},
    {"code": "so", "name": "Somali"},
    {"code": "af", "name": "Afrikaans"},
    {"code": "oc", "name": "Occitan"},
    {"code": "ka", "name": "Georgian"},
    {"code": "be", "name": "Belarusian"},
    {"code": "tg", "name": "Tajik"},
    {"code": "sd", "name": "Sindhi"},
    {"code": "gu", "name": "Gujarati"},
    {"code": "am", "name": "Amharic"},
    {"code": "yi", "name": "Yiddish"},
    {"code": "lo", "name": "Lao"},
    {"code": "uz", "name": "Uzbek"},
    {"code": "fo", "name": "Faroese"},
    {"code": "ht", "name": "Haitian creole"},
    {"code": "ps", "name": "Pashto"},
    {"code": "tk", "name": "Turkmen"},
    {"code": "nn", "name": "Nynorsk"},
    {"code": "mt", "name": "Maltese"},
    {"code": "sa", "name": "Sanskrit"},
    {"code": "lb", "name": "Luxembourgish"},
    {"code": "my", "name": "Myanmar"},
    {"code": "bo", "name": "Tibetan"},
    {"code": "tl", "name": "Tagalog"},
    {"code": "mg", "name": "Malagasy"},
    {"code": "as", "name": "Assamese"},
    {"code": "tt", "name": "Tatar"},
    {"code": "haw", "name": "Hawaiian"},
    {"code": "ln", "name": "Lingala"},
    {"code": "ha", "name": "Hausa"},
    {"code": "ba", "name": "Bashkir"},
    {"code": "jw", "name": "Javanese"},
    {"code": "su", "name": "Sundanese"},
    {"code": "yue", "name": "Cantonese"},
]

TOGGLE_UI_STATES = [
    "show_sources_for_selection",
    "show_script_for_confirmation",
    "show_banner_for_confirmation",
    "show_audio_for_confirmation",
]

AGENT_DESCRIPTION = (
    "Your name is Castora, a helpful assistant that guides users "
    "through podcast creation steps."
)

# =========================
# FIXED INSTRUCTIONS
# =========================
AGENT_INSTRUCTIONS = [
    "Guide users through podcast creation: sources → script → image → audio.",
    "1. Understand user intent (handle spelling mistakes, fuzzy queries).",
    "1a. Keep conversation minimal and efficient.",
    "2. Use search tools to fetch diverse, high-quality sources and update chat title.",
    "2a. Scrape full content from all selected sources.",
    "2b. Do not repeatedly ask the user during scraping phase.",
    "3. Ask user to select sources from UI list (no need to list manually).",
    "4. User selects sources via index list; sometimes includes language.",
    "4a. Use user_source_selection tool and disable UI states after selection.",
    "4b. If language provided, use update_language tool (default English).",
    "5. Call podcast script agent with selected sources + full language name.",
    "5a. Enable show_script_for_confirmation after script generation.",
    "6. After confirmation, generate images using image agent.",
    "6a. Enable show_banner_for_confirmation for image review.",
    "7. After image confirmation, generate audio.",
    "7a. Enable show_audio_for_confirmation for audio review.",
    "8. After audio confirmation, mark session finished.",
    "8a. Only mark finished after all stages complete.",
    "APPENDIX:",
    "1. Use ui_manager tool for UI state control.",
    f"1a. Available UI states: {TOGGLE_UI_STATES}",
    "1b. Always disable irrelevant UI states after stage completion.",
    f"2. Supported languages: {json.dumps(AVAILABLE_LANGS)}",
    "3. Use search agent tools smartly; do not pass raw queries blindly.",
    "4. Do NOT include year/date in queries unless explicitly requested.",
]

# =========================
# PATHS
# =========================
# Keep Studio assets beside the runtime SQLite databases.  On Render this is
# the mounted /var/data directory used by FastAPI's static endpoints.
DB_PATH = str(APP_ROOT / "databases")
PODCAST_DIR = str(APP_ROOT / "podcasts")
PODCAST_IMG_DIR = str(APP_ROOT / "podcasts" / "images")
PODCAST_AUDIO_DIR = str(APP_ROOT / "podcasts" / "audio")
PODCAST_RECORDINGS_DIR = str(APP_ROOT / "podcasts" / "recordings")

# =========================
# SESSION STATE
# =========================
INITIAL_SESSION_STATE = {
    "search_results": [],
    "show_sources_for_selection": False,
    "show_script_for_confirmation": False,
    "generated_script": {},
    "selected_language": {"code": "en", "name": "English"},
    "available_languages": AVAILABLE_LANGS,
    "banner_images": [],
    "banner_url": "",
    "audio_url": "",
    "title": "Untitled",
    "created_at": "",
    "finished": False,
    "show_banner_for_confirmation": False,
    "show_audio_for_confirmation": False,
    # The Render web instance cannot reliably load Kokoro's local model.
    # Deployments use the configured hosted TTS provider. A failed provider
    # must be reported; it must never be represented as spoken podcast audio.
    "tts_engine": os.environ.get("PODCAST_STUDIO_DEFAULT_TTS_ENGINE", "elevenlabs"),
}

STORAGE = SqliteStorage(
    table_name="podcast_sessions",
    db_file=get_agent_session_db_path()
)
