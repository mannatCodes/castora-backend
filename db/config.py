import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
_is_render = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
_default_runtime_root = "/tmp/castora" if os.environ.get("VERCEL") else (
    "/var/data" if _is_render else Path(__file__).resolve().parent.parent
)
# Render's application filesystem is ephemeral.  All databases and podcast
# assets must resolve below its mounted disk, even if the explicit variable is
# accidentally omitted from the dashboard configuration.
APP_ROOT = Path(os.environ.get("CASTORA_RUNTIME_DIR", _default_runtime_root))
DEFAULT_DB_PATHS = {
    "sources_db": "databases/sources.db",
    "tracking_db": "databases/feed_tracking.db",
    "podcasts_db": "databases/podcasts.db",
    "tasks_db": "databases/tasks.db",
    "agent_session_db": "databases/agent_sessions.db",
    "faiss_index_db": "databases/faiss/article_index.faiss",
    "faiss_mapping_file": "databases/faiss/article_id_map.npy",
    "internal_sessions_db": "databases/internal_sessions.db",
    "slack_sessions_db": "databases/slack_sessions.db",
}


def get_db_path(db_name):
    env_var = f"{db_name.upper()}_PATH"
    path = os.environ.get(env_var, DEFAULT_DB_PATHS.get(db_name))
    resolved_path = Path(path)
    if not resolved_path.is_absolute():
        resolved_path = APP_ROOT / resolved_path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved_path)


def get_sources_db_path():
    return get_db_path("sources_db")


def get_tracking_db_path():
    return get_db_path("tracking_db")


def get_podcasts_db_path():
    return get_db_path("podcasts_db")


def get_tasks_db_path():
    return get_db_path("tasks_db")


def get_agent_session_db_path():
    return get_db_path("agent_session_db")


def get_faiss_db_path():
    return get_db_path("faiss_index_db"), get_db_path("faiss_mapping_file")


def get_internal_sessions_db_path():
    return get_db_path("internal_sessions_db")


def get_browser_session_path():
    return "browsers/playwright_persistent_profile"

def get_slack_sessions_db_path():
    return get_db_path("slack_sessions_db")

DB_PATH = "databases"
PODCAST_DIR = "podcasts"
PODCAST_IMG_DIR = PODCAST_DIR + "/images"
PODCAST_AUIDO_DIR = PODCAST_DIR + "/audio"
PODCAST_RECORDINGS_DIR = PODCAST_DIR + "/recordings"
