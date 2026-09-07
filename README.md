# Castora Backend

The Castora backend provides the FastAPI API, scheduled ingestion tasks, and Celery-powered background jobs for source research and podcast generation.

## Requirements

- Python 3.11 or newer
- Redis, running locally on port `6379`
- A Groq API key for AI-powered features
- Node.js is only needed when running the separate frontend repository

## Setup

From this directory:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install
```

Create a `.env` file alongside `main.py`:

```env
GROQ_API_KEY=your_groq_api_key
ELEVENSLAB_API_KEY=your_elevenlabs_api_key
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

`ELEVENSLAB_API_KEY` is optional. Never commit `.env`; it is already excluded by `.gitignore`.

## Run locally

Activate the virtual environment in each terminal:

```powershell
.\venv\Scripts\Activate.ps1
```

Start the API:

```powershell
python main.py
```

The API is available at `http://localhost:7000`. Interactive OpenAPI documentation is available at `http://localhost:7000/docs`.

Start the scheduler in a separate terminal:

```powershell
python -m scheduler
```

Start the Celery worker in another terminal:

```powershell
python -m celery_worker
```

## API areas

All API routes are prefixed with `/api`.

- `/api/articles` — collected articles, filtering, and article metadata
- `/api/sources` — source and feed management
- `/api/podcasts` — podcast records and generated assets
- `/api/tasks` — scheduled task configuration and execution
- `/api/podcast-configs` — podcast configuration
- `/api/podcast-agent` — asynchronous podcast-generation workflow

## Project structure

```text
backend/
|- agents/       AI-assisted search, script, image, and audio agents
|- db/           SQLite database configuration and data-access code
|- processors/   ingestion and content-processing workflows
|- routers/      FastAPI route definitions
|- services/     application services and Celery tasks
|- tools/        search, crawling, and pipeline utilities
|- tests/        automated tests and fixtures
|- main.py       FastAPI entry point
|- scheduler.py  scheduled-task runner
`- celery_worker.py
```

Runtime databases, browser profiles, generated podcast files, logs, and the local virtual environment are intentionally excluded from version control.

## Frontend integration

The separate [Castora frontend](https://github.com/mannatCodes/castora-frontend) uses `http://localhost:7000` as its development API proxy. Run both repositories locally to use the complete application.
