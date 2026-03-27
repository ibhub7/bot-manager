"""
utils/scheduler.py — MongoDB-based broadcast scheduler (Fix #10)
No Redis needed — polls MongoDB every 60s for due schedules.
"""
import asyncio
from datetime import datetime, timezone

from database.broadcasts import get_due_schedules, mark_schedule_done
from database import users as users_db


async def scheduler_loop(get_clients_fn, master_client):
    """
    Background task: every 60s checks for scheduled broadcasts that are due.
    get_clients_fn: callable returning Dict[bot_id, Client]
    master_client: the master bot Client (to send status messages)
    """
    print("[scheduler] Started")
    while True:
        try:
            await asyncio.sleep(60)
            due = await get_due_schedules()
            if not due:
                continue

            from utils.broadcaster import run_broadcast, readable_time
            clients = get_clients_fn()
            if not clients:
                continue

            for schedule in due:
                sid = schedule["_id"]
                target_bot_id = schedule.get("target_bot_id")
                text = schedule.get("text", "")

                print(f"[scheduler] Running scheduled broadcast {sid}")

                if target_bot_id:
                    user_ids = await users_db.get_broadcast_users(target_bot_id)
                else:
                    user_ids = await users_db.get_all_unique_users()

                if not user_ids:
                    await mark_schedule_done(sid)
                    continue

                # Create a fake text message to broadcast
                # For scheduled text-only broadcasts we send directly
                for uid in user_ids:
                    for bot_id, client in clients.items():
                        try:
                            await client.send_message(uid, text)
                            await asyncio.sleep(0.05)
                            break
                        except Exception:
                            continue

                await mark_schedule_done(sid)
                print(f"[scheduler] Done: {sid}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[scheduler] Error: {e}")
