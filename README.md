# MultiBot Manager

A Python-based multi-bot broadcast system for Telegram, with a web dashboard and MongoDB-backed scheduling/logging.

## Features

- **Master + child bot architecture** using Pyrogram.
- **Dynamic bot pool**: add/remove child bots at runtime.
- **High-throughput broadcasting** with anti-ban throttling and FloodWait handling.
- **MongoDB persistence** for users, bots, broadcast logs, failures, templates, and schedules.
- **FastAPI dashboard** with token login and session cookie.
- **Scheduled broadcasts** via background scheduler loop.

## Tech Stack

- Python 3
- Pyrogram + TgCrypto
- FastAPI + Uvicorn
- MongoDB (Motor + PyMongo)
- python-dotenv

## Project Structure

The repository currently stores modules at the root level:

- `main.py` — app entrypoint (starts DB init, bots, scheduler, dashboard)
- `bot_manager.py` — manages child bot clients
- `admin.py` / `start.py` — Telegram handlers
- `broadcaster.py`, `antiban.py`, `scheduler.py`, `importer.py` — utility logic
- `users.py`, `bots.py`, `broadcasts.py`, `db.py` — database access layer
- `app.py` — FastAPI dashboard app
- `config.py` — environment/config loading
- `.env.example` — environment template

## Setup

1. **Clone and enter the project**

   ```bash
   git clone <your-repo-url>
   cd bot-manager
   ```

2. **Create and activate a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Create your env file**

   ```bash
   cp .env.example .env
   ```

5. **Edit `.env`** with your:
   - `API_ID`, `API_HASH`
   - `MASTER_TOKEN`
   - `MONGO_URI`, `DB_NAME`
   - `ADMINS`
   - `DASHBOARD_TOKEN`

## Run

```bash
python main.py
```

On startup, it initializes indexes, launches active bots, starts the master bot, runs the scheduler, and serves the web dashboard.

Dashboard URL defaults to:

- `http://0.0.0.0:8080` (or `PORT` if provided by hosting)

## Configuration

See `.env.example` for all options. Key values:

- `WEB_HOST`, `WEB_PORT`, `DASHBOARD_TOKEN`
- `BATCH_SIZE`, `CONCURRENCY`, `MIN_DELAY`, `MAX_DELAY`
- `BOT_RATE_LIMIT`
- `LOG_CHANNEL`
- `SESSIONS_DIR`

## Notes

- Keep `.env` secret and never commit it.
- Use a strong `DASHBOARD_TOKEN` in production.
- Ensure MongoDB is reachable from your runtime environment.
