"""
utils/broadcaster.py — FAST concurrent broadcast engine (all fixes applied)

Fix #3:  Broadcast runs as asyncio.create_task() — never blocks web dashboard
Fix #5:  Checkpoint saved to MongoDB every 500 users — crash-safe resume
Fix #9:  Failed users saved to DB — /retry command picks them up
Fix #13: Smart block — only permanent after 3 consecutive failures
Fix #15: Auto-notifies LOG_CHANNEL when broadcast completes
"""
import asyncio
import random
import time
from typing import Callable, Dict, List, Optional, Tuple

from pyrogram import Client
from pyrogram.errors import (
    FloodWait, UserIsBlocked, InputUserDeactivated,
    PeerIdInvalid, UserDeactivated, ChatWriteForbidden,
)

from config import BATCH_SIZE, CONCURRENCY, RETRY_DELAY, LOG_CHANNEL
from database import broadcasts as bc_db
from database.users import mark_blocked, increment_fail, reset_fail
from utils.antiban import throttle, handle_flood_wait

# ── Cancel registry ───────────────────────────────────────────────────────────
_CANCEL: Dict[str, bool] = {}

def request_cancel(broadcast_id: str):
    _CANCEL[broadcast_id] = True

def is_cancelled(broadcast_id: str) -> bool:
    return _CANCEL.get(broadcast_id, False)


# ── Single send ───────────────────────────────────────────────────────────────

async def _send_one(
    client: Client,
    bot_id: int,
    uid: int,
    message,
    pin: bool,
    broadcast_id: str,
    sem: asyncio.Semaphore,
) -> bool:
    """
    Send to one user. Handles FloodWait, blocked users, and fail counting.
    Fix #9: Semaphore passed to handle_flood_wait so it's released during sleep.
    Fix #13: Permanent errors → mark_blocked. Temporary → increment_fail.
    """
    async with sem:
        await throttle(bot_id)
        for attempt in range(2):
            try:
                m = await message.copy(chat_id=uid)
                if pin:
                    try:
                        await m.pin(both_sides=True)
                    except Exception:
                        pass
                # Reset fail counter on success (Fix #13)
                asyncio.create_task(reset_fail(uid, bot_id))
                return True

            except FloodWait as e:
                # Fix #9: release semaphore before sleeping
                await handle_flood_wait(e.value, bot_id, sem)
                continue

            except (UserIsBlocked, InputUserDeactivated, UserDeactivated, PeerIdInvalid) as e:
                # Permanent — block immediately, no retry
                err = type(e).__name__
                asyncio.create_task(mark_blocked(uid, bot_id))
                asyncio.create_task(bc_db.save_failed_user(broadcast_id, uid, err))
                return False

            except Exception as e:
                err = str(e).split(":")[0]
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    # Temporary failure — increment counter (Fix #13)
                    asyncio.create_task(increment_fail(uid, bot_id))
                    asyncio.create_task(bc_db.save_failed_user(broadcast_id, uid, err))
                    return False

    return False


# ── Batch sender ──────────────────────────────────────────────────────────────

async def _send_batch(
    client: Client,
    bot_id: int,
    batch: List[int],
    message,
    pin: bool,
    broadcast_id: str,
    sem: asyncio.Semaphore,
) -> int:
    results = await asyncio.gather(
        *[_send_one(client, bot_id, uid, message, pin, broadcast_id, sem) for uid in batch]
    )
    return sum(results)


async def _send_batch_safe(
    client: Client,
    bot_id: int,
    batch: List[int],
    message,
    pin: bool,
    broadcast_id: str,
    sem: asyncio.Semaphore,
    all_clients: list,
) -> int:
    """Batch with per-bot failover."""
    try:
        return await _send_batch(client, bot_id, batch, message, pin, broadcast_id, sem)
    except Exception as e:
        print(f"[broadcaster] Bot {bot_id} batch failed: {e}")
        fallback_clients = [(bid, c) for bid, c in all_clients if bid != bot_id]
        if fallback_clients:
            fb_id, fb_client = random.choice(fallback_clients)
            try:
                return await _send_batch(fb_client, fb_id, batch, message, pin, broadcast_id, sem)
            except Exception as e2:
                print(f"[broadcaster] Fallback also failed: {e2}")
        for uid in batch:
            asyncio.create_task(bc_db.save_failed_user(broadcast_id, uid, "BotFailed"))
        return 0


# ── Main broadcast engine ─────────────────────────────────────────────────────

async def run_broadcast(
    clients: Dict[int, Client],
    user_ids: List[int],
    message,
    broadcast_id: str,
    pin: bool = False,
    resume_from: int = 0,              # Fix #5: resume support
    on_progress: Optional[Callable] = None,
) -> Tuple[int, int]:
    """
    Core engine — concurrent multi-bot broadcast with all fixes.
    Returns (success_count, failed_count).
    """
    total   = len(user_ids)
    done    = resume_from
    success = 0
    failed  = 0
    start   = time.time()

    active_clients = list(clients.items())
    sem = asyncio.Semaphore(CONCURRENCY)

    CHECKPOINT_EVERY = 500   # Fix #5: checkpoint every 500 users

    for i in range(resume_from, total, BATCH_SIZE):
        if is_cancelled(broadcast_id):
            remaining = user_ids[i:]
            await bc_db.finish_broadcast(broadcast_id, "saved", remaining)
            break

        batch = user_ids[i: i + BATCH_SIZE]

        # Split batch across all online bots
        if len(active_clients) > 1:
            slices = _split_batch(batch, len(active_clients))
        else:
            slices = [batch]

        tasks = [
            _send_batch_safe(
                client, bot_id, slices[idx] if idx < len(slices) else [],
                message, pin, broadcast_id, sem, active_clients
            )
            for idx, (bot_id, client) in enumerate(active_clients)
            if idx < len(slices) and slices[idx]
        ]

        results    = await asyncio.gather(*tasks)
        batch_ok   = sum(results)
        batch_fail = len(batch) - batch_ok

        success += batch_ok
        failed  += batch_fail
        done    += len(batch)

        # Fix #5: checkpoint every CHECKPOINT_EVERY users
        if done % CHECKPOINT_EVERY < BATCH_SIZE:
            await bc_db.save_checkpoint(broadcast_id, done, success, failed)

        # Progress update
        await bc_db.update_progress(broadcast_id, success, failed, done)

        if on_progress:
            elapsed = time.time() - start
            speed   = done / elapsed if elapsed > 0 else 0
            eta     = int((total - done) / speed) if speed > 0 else 0
            await on_progress(done, success, failed, total, speed, eta)

    _CANCEL.pop(broadcast_id, None)

    # Fix #15: Notify log channel
    if LOG_CHANNEL:
        master = list(clients.values())[0] if clients else None
        if master:
            try:
                elapsed = int(time.time() - start)
                await master.send_message(
                    LOG_CHANNEL,
                    f"📢 **Broadcast Complete**\n\n"
                    f"🆔 `{broadcast_id[-8:]}`\n"
                    f"📦 Total   : `{total}`\n"
                    f"✅ Success : `{success}`\n"
                    f"❌ Failed  : `{failed}`\n"
                    f"⏱ Time    : `{readable_time(elapsed)}`\n"
                    f"🤖 Bots   : `{len(clients)}`"
                )
            except Exception:
                pass

    return success, failed


# ── Utilities ─────────────────────────────────────────────────────────────────

def _split_batch(batch: List[int], n: int) -> List[List[int]]:
    k, rem = divmod(len(batch), n)
    slices, start = [], 0
    for i in range(n):
        end = start + k + (1 if i < rem else 0)
        slices.append(batch[start:end])
        start = end
    return slices


def progress_bar(done: int, total: int) -> str:
    pct    = (done / total * 100) if total else 0
    filled = int(pct // 5)
    return f"[{'█' * filled}{'░' * (20 - filled)}] {pct:.1f}%"


def readable_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
