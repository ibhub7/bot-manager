"""
main.py — Entry point
Starts everything in one asyncio event loop:
  1. DB indexes
  2. All child bot clients (BotManager)
  3. Start handlers on each child bot
  4. Master bot (admin commands)
  5. MongoDB-based scheduler loop
  6. FastAPI web dashboard
"""
import asyncio
import os

import uvicorn
from pyrogram import Client

from config import API_ID, API_HASH, MASTER_TOKEN, WEB_HOST, WEB_PORT, SESSIONS_DIR
from database import users as users_db, bots as bots_db, broadcasts as bc_db
from bot_manager import manager
from handlers.admin import register_admin_handlers
from handlers.start import register_start_handler
from web.app import app as web_app


async def init_db():
    await users_db.ensure_indexes()
    await bots_db.ensure_indexes()
    await bc_db.ensure_indexes()
    print("[main] ✅ DB indexes ready")


async def start_master_bot() -> Client:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    master = Client(
        name=os.path.join(SESSIONS_DIR, "master_bot"),
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=MASTER_TOKEN,
    )
    register_admin_handlers(master)
    await master.start()
    me = await master.get_me()
    print(f"[main] ✅ Master bot: @{me.username}")
    return master


async def attach_child_handlers():
    for bot_id, client in manager.get_all_clients().items():
        register_start_handler(client, bot_id)
    print(f"[main] ✅ Handlers on {len(manager.get_all_clients())} child bot(s)")


async def run_web():
    config = uvicorn.Config(web_app, host=WEB_HOST, port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    print("━" * 52)
    print("  🤖  MultiBot System  v2  Starting...")
    print("━" * 52)

    await init_db()
    await manager.start_all()
    await attach_child_handlers()
    master = await start_master_bot()

    # Start MongoDB-based scheduler (Fix #10)
    from utils.scheduler import scheduler_loop
    scheduler_task = asyncio.create_task(
        scheduler_loop(manager.get_online_clients, master),
        name="scheduler"
    )

    print(f"[main] 🌐 Dashboard → http://{WEB_HOST}:{WEB_PORT}")
    print(f"[main] 🔐 Login with your DASHBOARD_TOKEN")
    print("━" * 52)

    try:
        await run_web()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\n[main] Shutting down...")
        scheduler_task.cancel()
        await master.stop()
        await manager.stop_all()
        print("[main] ✅ Clean shutdown")


if __name__ == "__main__":
    asyncio.run(main())
