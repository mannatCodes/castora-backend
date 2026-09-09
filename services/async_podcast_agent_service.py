import os
import json
import uuid
import time
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from fastapi import status
from fastapi.responses import JSONResponse
import aiosqlite
import glob
import traceback
from redis.asyncio import ConnectionPool, Redis
from db.config import get_agent_session_db_path
from db.agent_config_v2 import (
    PODCAST_DIR,
    PODCAST_AUDIO_DIR,
    PODCAST_IMG_DIR,
    PODCAST_RECORDINGS_DIR,
    AVAILABLE_LANGS,
    INITIAL_SESSION_STATE,
)
from services.celery_tasks import agent_chat
from dotenv import load_dotenv
from services.internal_session_service import SessionService
from services.celery_tasks import (
    _handle_source_selection_directly,
    _is_greeting_only,
    _prepare_sources_without_ai,
    _should_prepare_sources_directly,
)

load_dotenv()


class PodcastAgentService:
    def __init__(self):
        os.makedirs(PODCAST_DIR, exist_ok=True)
        os.makedirs(PODCAST_AUDIO_DIR, exist_ok=True)
        os.makedirs(PODCAST_IMG_DIR, exist_ok=True)
        os.makedirs(PODCAST_RECORDINGS_DIR, exist_ok=True)

        self.redis_host = os.environ.get("REDIS_HOST", "localhost")
        self.redis_port = int(os.environ.get("REDIS_PORT", 6379))
        self.redis_db = int(os.environ.get("REDIS_DB", 0))
        # Render provides a complete Redis URL (including credentials and TLS
        # settings).  Rebuilding a localhost URL loses those details, so the
        # API could not see locks/results created by the deployed worker.
        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            self.redis_pool = ConnectionPool.from_url(redis_url, db=self.redis_db + 1, max_connections=10)
        else:
            self.redis_pool = ConnectionPool.from_url(
                f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db + 1}",
                max_connections=10,
            )
        self.redis = Redis(connection_pool=self.redis_pool)
        # Use the durable worker whenever a broker is configured. Local
        # development remains worker-free unless explicitly opted in.
        self.use_celery = os.environ.get(
            "PODCAST_AGENT_USE_CELERY", "1" if redis_url else "0"
        ).lower() in {"1", "true", "yes"}
        self.executor = ThreadPoolExecutor(max_workers=int(os.environ.get("PODCAST_AGENT_WORKERS", 2)))
        self.local_task_timeout_seconds = int(os.environ.get("PODCAST_AGENT_TASK_TIMEOUT_SECONDS", 180))
        self.local_tasks = {}
        self.local_session_tasks = {}
        self.local_lock = threading.Lock()

    def _submit_local_task(self, session_id, message):
        task_id = f"local:{uuid.uuid4()}"
        future = self.executor.submit(agent_chat.run, session_id, message)
        with self.local_lock:
            self.local_tasks[task_id] = {
                "future": future,
                "session_id": session_id,
                "created_at": time.time(),
            }
            self.local_session_tasks[session_id] = task_id
        return task_id

    def _get_local_active_task(self, session_id):
        with self.local_lock:
            task_id = self.local_session_tasks.get(session_id)
            if not task_id:
                return None
            task_info = self.local_tasks.get(task_id)
            if not task_info:
                self.local_session_tasks.pop(session_id, None)
                return None
            if task_info["future"].done():
                return None
            return task_id

    def _pop_local_task(self, task_id):
        with self.local_lock:
            task_info = self.local_tasks.pop(task_id, None)
            if task_info:
                session_id = task_info.get("session_id")
                if self.local_session_tasks.get(session_id) == task_id:
                    self.local_session_tasks.pop(session_id, None)
            return task_info

    def _ready_state_response(self, session_id):
        try:
            session = SessionService.get_session(session_id)
            session_state = session.get("state", {}) or {}
        except Exception as e:
            print(f"Error checking ready session state: {e}")
            return None

        stage = session_state.get("stage")
        generated_script = session_state.get("generated_script")
        if stage == "script" and generated_script:
            return {
                "session_id": session_id,
                "response": session_state.get("response") or "I generated the podcast script from your selected sources. Please review it.",
                "stage": "script",
                "session_state": json.dumps(session_state),
                "is_processing": False,
                "process_type": None,
                "task_id": None,
            }
        if stage in {"source_selection", "banner_generation", "banner", "audio_generation", "audio", "complete", "error"}:
            processing_stages = {"banner_generation", "audio_generation"}
            default_messages = {
                "banner_generation": "Your podcast banner is being prepared.",
                "audio_generation": "Your podcast audio is being generated.",
            }
            return {
                "session_id": session_id,
                "response": session_state.get("response") or default_messages.get(stage, ""),
                "stage": stage,
                "session_state": json.dumps(session_state),
                "is_processing": stage in processing_stages,
                "process_type": "chat" if stage in processing_stages else None,
                "task_id": None,
            }
        return None

    async def get_active_task(self, session_id):
        local_task_id = self._get_local_active_task(session_id)
        if local_task_id:
            return local_task_id
        try:
            lock_info = await self.redis.get(f"lock_info:{session_id}")
            if lock_info:
                try:
                    lock_data = json.loads(lock_info.decode("utf-8"))
                    task_id = lock_data.get("task_id")
                    if task_id:
                        return task_id
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"Error parsing lock info: {e}")
            return None
        except Exception as e:
            print(f"Error checking active task: {e}")
            return None

    def _workers_available(self):
        try:
            inspector = agent_chat.app.control.inspect(timeout=1.0)
            replies = inspector.ping()
            return bool(replies)
        except Exception as e:
            print(f"Error checking Celery worker availability: {e}")
            return False

    async def create_session(self, request=None):
        if request and request.session_id:
            return {"session_id": request.session_id}

        new_session_id = str(uuid.uuid4())
        return {"session_id": new_session_id}

    async def chat(self, request):
        try:
            session_id = request.session_id
            if _is_greeting_only(request.message):
                session_state = deepcopy(INITIAL_SESSION_STATE)
                session_state["stage"] = "welcome"
                SessionService.save_session(session_id, session_state)
                return {
                    "session_id": session_id,
                    "response": "Hi! What topic would you like to create a podcast about?",
                    "stage": session_state["stage"],
                    "session_state": json.dumps(session_state),
                    "is_processing": False,
                    "process_type": None,
                    "task_id": None,
                }

            session = SessionService.get_session(session_id)
            session_state = session.get("state", INITIAL_SESSION_STATE)
            if _should_prepare_sources_directly(request.message, session_state):
                response = _prepare_sources_without_ai(session_id, request.message)
                response["task_id"] = None
                return response
            source_selection_response = _handle_source_selection_directly(session_id, request.message, session_state)
            if source_selection_response:
                source_selection_response["task_id"] = None
                return source_selection_response

            task_id = await self.get_active_task(session_id)
            if task_id:
                ready_response = self._ready_state_response(session_id)
                if ready_response:
                    return ready_response
                return {
                    "session_id": session_id,
                    "response": "Your request is already being processed.",
                    "stage": "processing",
                    "session_state": "{}",
                    "is_processing": True,
                    "process_type": "chat",
                    "task_id": task_id,
                }
            if not self.use_celery:
                task_id = self._submit_local_task(request.session_id, request.message)
                return {
                    "session_id": request.session_id,
                    "response": "Your request is being processed.",
                    "stage": "processing",
                    "session_state": "{}",
                    "is_processing": True,
                    "process_type": "chat",
                    "task_id": task_id,
                }
            if not self._workers_available():
                return {
                    "session_id": session_id,
                    "response": "Podcast Studio worker is not running. Start it with: python -m celery_worker",
                    "stage": "error",
                    "session_state": "{}",
                    "is_processing": False,
                    "process_type": None,
                    "task_id": None,
                }
            task = agent_chat.delay(request.session_id, request.message)
            return {
                "session_id": request.session_id,
                "response": "Your request is being processed.",
                "stage": "processing",
                "session_state": "{}",
                "is_processing": True,
                "process_type": "chat",
                "task_id": task.id,
            }
        except Exception as e:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "session_id": request.session_id,
                    "response": f"I encountered an error while processing your request: {str(e)}. Please try again.",
                    "stage": "error",
                    "session_state": "{}",
                    "error": str(e),
                    "is_processing": False,
                },
            )

    def _browser_recording(self, session_id):
        try:
            recordings_dir = os.path.join("podcasts/recordings", session_id)
            webm_files = glob.glob(os.path.join(recordings_dir, "*.webm"))
            if webm_files:
                browser_recording_path = webm_files[0]
                if (os.path.exists(browser_recording_path) and 
                    os.path.getsize(browser_recording_path) > 8192 and 
                    os.access(browser_recording_path, os.R_OK)):
                    return browser_recording_path
            return None
        except Exception as _:
            return None

    async def check_result_status(self, request):
        try:
            if not request.session_id:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": "Session ID is required"},
                )

            browser_recording_path = self._browser_recording(request.session_id)

            task_id = getattr(request, "task_id", None)
            if task_id:
                if task_id.startswith("local:"):
                    task_info = self.local_tasks.get(task_id)
                    if not task_info:
                        return await self.get_session_state(request.session_id)
                    future = task_info["future"]
                    if not future.done():
                        elapsed = time.time() - task_info["created_at"]
                        if elapsed >= self.local_task_timeout_seconds:
                            # A local provider can hang (for example while a
                            # model is downloaded).  Release the Studio UI
                            # instead of polling forever; the user can retry
                            # after correcting the provider configuration.
                            self._pop_local_task(task_id)
                            message = (
                                "Audio generation timed out before the TTS provider responded. "
                                "Please retry; if it persists, check the configured TTS provider."
                            )
                            try:
                                session = SessionService.get_session(request.session_id)
                                session_state = session.get("state", {})
                                session_state["stage"] = "error"
                                session_state["audio_error"] = message
                                session_state["show_audio_for_confirmation"] = False
                                SessionService.save_session(request.session_id, session_state)
                            except Exception as save_error:
                                print(f"Error saving timed-out task state: {save_error}")
                            return {
                                "session_id": request.session_id,
                                "response": message,
                                "stage": "error",
                                "session_state": "{}",
                                "is_processing": False,
                                "process_type": None,
                                "task_id": None,
                                "browser_recording_path": browser_recording_path,
                            }
                        return {
                            "session_id": request.session_id,
                            "response": "Your request is still being processed.",
                            "stage": "processing",
                            "session_state": "{}",
                            "is_processing": True,
                            "process_type": "chat",
                            "task_id": task_id,
                            "browser_recording_path": browser_recording_path,
                        }
                    self._pop_local_task(task_id)
                    try:
                        result = future.result()
                    except Exception as e:
                        traceback.print_exc()
                        error_details = traceback.format_exc()
                        return {
                            "session_id": request.session_id,
                            "response": f"Error processing request: {str(e)}",
                            "stage": "error",
                            "session_state": "{}",
                            "is_processing": False,
                            "browser_recording_path": browser_recording_path,
                            "error": error_details,
                        }
                    if result and isinstance(result, dict):
                        result.setdefault("browser_recording_path", browser_recording_path)
                        return result
                    return {
                        "session_id": request.session_id,
                        "response": "Error processing request: task returned an invalid result.",
                        "stage": "error",
                        "session_state": "{}",
                        "is_processing": False,
                        "browser_recording_path": browser_recording_path,
                    }
                task = agent_chat.AsyncResult(task_id)
                if task.state == "PENDING" or task.state == "STARTED":
                    ready_response = self._ready_state_response(request.session_id)
                    if ready_response:
                        ready_response["browser_recording_path"] = browser_recording_path
                        return ready_response
                    return {
                        "session_id": request.session_id,
                        "response": "Your request is still being processed.",
                        "stage": "processing",
                        "session_state": "{}",
                        "is_processing": True,
                        "process_type": "chat",
                        "task_id": task_id,
                        "browser_recording_path": browser_recording_path,
                    }
                elif task.state == "SUCCESS":
                    result = task.result
                    if result and isinstance(result, dict):
                        if result.get("session_id") != request.session_id:
                            return {
                                "session_id": request.session_id,
                                "response": "Error: Received result for wrong session.",
                                "stage": "error",
                                "session_state": "{}",
                                "is_processing": False,
                                "browser_recording_path": browser_recording_path,
                            }
                        return result
                else:
                    error_info = str(task.result) if task.result else f"Task failed with state: {task.state}"
                    if "not enough values to unpack" in error_info:
                        error_info = (
                            "Podcast Studio worker failed while executing the task. "
                            "Restart the worker with: python -m celery_worker"
                        )
                    return {
                        "session_id": request.session_id,
                        "response": f"Error processing request: {error_info}",
                        "stage": "error",
                        "session_state": "{}",
                        "is_processing": False,
                        "browser_recording_path": browser_recording_path,
                    }
            return await self.get_session_state(request.session_id)
        except Exception as e:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": f"Error checking result status: {str(e)}",
                    "session_id": request.session_id,
                    "response": f"Error checking result status: {str(e)}",
                    "stage": "error",
                    "session_state": "{}",
                    "is_processing": False,
                    "browser_recording_path": browser_recording_path,
                },
            )

    async def get_session_state(self, session_id):
        try:
            # Direct Studio stages are stored in internal_sessions.db. They do
            # not necessarily create a matching Agno chat-history row, so use
            # the workflow state before checking agent_sessions.db.
            ready_response = self._ready_state_response(session_id)
            if ready_response:
                return ready_response

            db_path = get_agent_session_db_path()
            async with aiosqlite.connect(db_path) as conn:
                conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
                async with conn.execute("SELECT session_data FROM podcast_sessions WHERE session_id = ?", (session_id,)) as cursor:
                    row = await cursor.fetchone()
            if not row:
                return {
                    "session_id": session_id,
                    "response": "No session data found.",
                    "stage": "idle",
                    "session_state": "{}",
                    "is_processing": False,
                }

            session = SessionService.get_session(session_id)
            session_state = session.get("state", {})
            return {
                "session_id": session_id,
                "response": "",
                "stage": session_state.get("stage", "idle"),
                "session_state": json.dumps(session_state),
                "is_processing": False,
            }
        except Exception as e:
            return {
                "session_id": session_id,
                "response": f"Error retrieving session state: {str(e)}",
                "stage": "error",
                "session_state": "{}",
                "is_processing": False,
            }

    async def list_sessions(self, page=1, per_page=10):
        try:
            db_path = get_agent_session_db_path()
            async with aiosqlite.connect(db_path) as conn:
                conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
                async with conn.execute(
                    """
                    select name from sqlite_master
                    where type='table' and name='podcast_sessions'
                    """
                ) as cursor:
                    table = await cursor.fetchone()
                if not table:
                    return {
                        "sessions": [],
                        "pagination": {
                            "total": 0,
                            "page": page,
                            "per_page": per_page,
                            "total_pages": 0,
                        },
                    }
                async with conn.execute("SELECT COUNT(*) as count FROM podcast_sessions") as cursor:
                    row = await cursor.fetchone()
                    total_sessions = row["count"] if row else 0
                offset = (page - 1) * per_page
                async with conn.execute(
                    "SELECT session_id, session_data, updated_at FROM podcast_sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?", (per_page, offset)
                ) as cursor:
                    rows = await cursor.fetchall()
                    sessions = []
                    for row in rows:
                        try:
                            session = SessionService.get_session(row["session_id"])
                            session_state = session.get("state", {})
                            title = session_state.get("title", "Untitled Podcast")
                            stage = session_state.get("stage", "welcome")
                            updated_at = row["updated_at"]
                            sessions.append({"session_id": row["session_id"], "topic": title, "stage": stage, "updated_at": updated_at})
                        except Exception as e:
                            print(f"Error parsing session data: {e}")
            return {
                "sessions": sessions,
                "pagination": {
                    "total": total_sessions,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": (total_sessions + per_page - 1) // per_page,
                },
            }
        except Exception as e:
            print(f"Error listing sessions: {e}")
            return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Failed to list sessions: {str(e)}"})

    async def delete_session(self, session_id: str):
        try:
            db_path = get_agent_session_db_path()
            async with aiosqlite.connect(db_path) as conn:
                conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
                async with conn.execute("SELECT session_data FROM podcast_sessions WHERE session_id = ?", (session_id,)) as cursor:
                    row = await cursor.fetchone()
                if not row:
                    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"error": f"Session with ID {session_id} not found"})
                try:
                    session = SessionService.get_session(session_id)
                    session_state = session.get("state", {})
                    stage = session_state.get("stage")
                    is_completed = stage == "complete" or session_state.get("podcast_generated", False)
                    banner_url = session_state.get("banner_url")
                    audio_url = session_state.get("audio_url")
                    web_search_recording = session_state.get("web_search_recording")
                    await conn.execute("DELETE FROM podcast_sessions WHERE session_id = ?", (session_id,))
                    await conn.commit()
                    if is_completed:
                        print(f"Session {session_id} is in 'complete' stage, keeping assets but removing session record")
                    else:
                        if banner_url:
                            banner_path = os.path.join(PODCAST_IMG_DIR, banner_url)
                            if os.path.exists(banner_path):
                                try:
                                    os.remove(banner_path)
                                    print(f"Deleted banner image: {banner_path}")
                                except Exception as e:
                                    print(f"Error deleting banner image: {e}")
                        if audio_url:
                            audio_path = os.path.join(PODCAST_AUDIO_DIR, audio_url)
                            if os.path.exists(audio_path):
                                try:
                                    os.remove(audio_path)
                                    print(f"Deleted audio file: {audio_path}")
                                except Exception as e:
                                    print(f"Error deleting audio file: {e}")
                        if web_search_recording:
                            recording_dir = os.path.join(PODCAST_RECORDINGS_DIR, session_id)
                            if os.path.exists(recording_dir):
                                try:
                                    import shutil

                                    shutil.rmtree(recording_dir)
                                    print(f"Deleted recordings directory: {recording_dir}")
                                except Exception as e:
                                    print(f"Error deleting recordings directory: {e}")
                    if is_completed:
                        return {"success": True, "message": f"Session {session_id} deleted, but assets preserved"}
                    else:
                        return {"success": True, "message": f"Session {session_id} and its associated data deleted successfully"}
                except Exception as e:
                    print(f"Error parsing session data for deletion: {e}")
                    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Error deleting session: {str(e)}"})
        except Exception as e:
            print(f"Error deleting session: {e}")
            return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Failed to delete session: {str(e)}"})

    async def _get_chat_messages(self, row, session_id):
        formatted_messages = []
        session_state = {}
        if row["session_data"]:
            try:
                session = SessionService.get_session(session_id)
                session_state = session.get("state", {})
            except Exception as e:
                print(f"Error parsing session_data: {e}")

        if row["memory"]:
            try:
                memory_data = json.loads(row["memory"]) if isinstance(row["memory"], str) else row["memory"]
                if "runs" in memory_data and isinstance(memory_data["runs"], list):
                    for run in memory_data["runs"]:
                        if "messages" in run and isinstance(run["messages"], list):
                            for msg in run["messages"]:
                                if msg.get("role") in ["user", "assistant"] and "content" in msg:
                                    if msg.get("role") == "assistant" and "tool_calls" in msg:
                                        if not msg.get("content"):
                                            continue
                                    if msg.get("content"):
                                        formatted_messages.append({"role": msg["role"], "content": msg["content"]})
            except json.JSONDecodeError as e:
                print(f"Error parsing memory data: {e}")

        return formatted_messages, session_state

    async def get_session_history(self, session_id: str):
        try:
            formatted_messages = []
            session_state = {}

            # Get workflow state from internal_sessions.db.
            session = SessionService.get_session(session_id)
            session_state = session.get("state", {}) or {}

            # Chat messages are stored in podcast_sessions.
            db_path = get_agent_session_db_path()
            async with aiosqlite.connect(db_path) as conn:
                conn.row_factory = lambda cursor, row: {
                    col[0]: row[idx]
                    for idx, col in enumerate(cursor.description)
                }

                async with conn.execute(
                    "SELECT memory, session_data FROM podcast_sessions WHERE session_id = ?",
                    (session_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row:
                    formatted_messages, _ = await self._get_chat_messages(
                        row, session_id
                    )

            task_id = await self.get_active_task(session_id)
            browser_recording_path = self._browser_recording(session_id)

            ready_response = self._ready_state_response(session_id)

            if ready_response:
                return {
                    "session_id": session_id,
                    "messages": formatted_messages,
                    "state": ready_response["session_state"],
                    "is_processing": False,
                    "process_type": None,
                    "task_id": None,
                    "browser_recording_path": browser_recording_path,
                }

            is_processing = bool(task_id)
            process_type = "chat" if is_processing else None

            return {
                "session_id": session_id,
                "messages": formatted_messages,
                "state": json.dumps(session_state),
                "is_processing": is_processing,
                "process_type": process_type,
                "task_id": task_id if is_processing else None,
                "browser_recording_path": browser_recording_path,
            }

        except Exception as e:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": f"Error retrieving session history: {str(e)}"
                },
            )

    async def get_supported_languages(self):
        return {"languages": AVAILABLE_LANGS}


podcast_agent_service = PodcastAgentService()