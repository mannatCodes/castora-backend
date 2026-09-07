import os
import sys
import traceback
import json
from dotenv import load_dotenv

sys.path.insert(0, '.')
load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

from services.celery_tasks import agent_chat
from services.internal_session_service import SessionService

session_id = 'test-session-123'
message = 'Create a podcast about climate change'

try:
    res = agent_chat.run(session_id, message)
    print('RES_TYPE', type(res))
    if isinstance(res, dict):
        print(json.dumps(res, indent=2))
    else:
        print(repr(res))
except Exception:
    traceback.print_exc()

try:
    sess = SessionService.get_session(session_id)
    print('SESSION', json.dumps(sess, indent=2))
except Exception:
    traceback.print_exc()
