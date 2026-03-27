"""
database/broadcasts.py — Broadcast logs + MongoDB-based job queue + checkpointing
Fix #5: Crash-safe checkpointing (no Redis needed — pure MongoDB)
Fix #9: Retry queue stored in MongoDB
Fix #11: Broadcast templates in MongoDB
"""
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from database.db import get_db


# ── Broadcast logs ────────────────────────────────────────────────────────────

def _col():
    return get_db()["broadcasts"]

def _fail_col():
    return get_db()["broadcast_failures"]

def _tmpl_col():
    return get_db()["broadcast_templates"]

def _sched_col():
    return get_db()["scheduled_broadcasts"]


async def ensure_indexes():
    await _col().create_index("status")
    await _col().create_index("created_at")
    await _col().create_index("target_bot_id")
    await _fail_col().create_index([("broadcast_id", 1), ("user_id", 1)])
    await _sched_col().create_index("run_at")
    await _sched_col().create_index("status")


async def create_broadcast(
    target_bot_id: Optional[int],
    sender_bot_ids: List[int],
    total_users: int,
    initiated_by: int,
) -> str:
    doc = {
        "target_bot_id":  target_bot_id,
        "sender_bot_ids": sender_bot_ids,
        "total_users":    total_users,
        "success":        0,
        "failed":         0,
        "done":           0,
        "status":         "running",
        "initiated_by":   initiated_by,
        "created_at":     datetime.now(timezone.utc),
        "updated_at":     datetime.now(timezone.utc),
        "checkpoint":     0,    # Fix #5: last safely processed index
        "remaining_users": [],
    }
    r = await _col().insert_one(doc)
    return str(r.inserted_id)


async def save_checkpoint(broadcast_id: str, checkpoint_index: int, success: int, failed: int):
    """
    Fix #5: Save progress checkpoint every N users.
    On restart, broadcast can resume from checkpoint_index instead of start.
    """
    await _col().update_one(
        {"_id": ObjectId(broadcast_id)},
        {"$set": {
            "checkpoint":  checkpoint_index,
            "success":     success,
            "failed":      failed,
            "done":        checkpoint_index,
            "updated_at":  datetime.now(timezone.utc),
        }},
    )


async def save_failed_user(broadcast_id: str, user_id: int, error: str):
    """Fix #9: Store failed users in DB so /retry can pick them up."""
    await _fail_col().update_one(
        {"broadcast_id": broadcast_id, "user_id": user_id},
        {"$set": {
            "broadcast_id": broadcast_id,
            "user_id":      user_id,
            "error":        error,
            "saved_at":     datetime.now(timezone.utc),
        }},
        upsert=True,
    )


async def get_failed_users(broadcast_id: str) -> List[int]:
    cursor = _fail_col().find({"broadcast_id": broadcast_id}, {"user_id": 1})
    return [d["user_id"] async for d in cursor]


async def clear_failed_users(broadcast_id: str):
    await _fail_col().delete_many({"broadcast_id": broadcast_id})


async def update_progress(broadcast_id: str, success: int, failed: int, done: int):
    await _col().update_one(
        {"_id": ObjectId(broadcast_id)},
        {"$set": {
            "success":    success,
            "failed":     failed,
            "done":       done,
            "updated_at": datetime.now(timezone.utc),
        }},
    )


async def finish_broadcast(broadcast_id: str, status: str = "completed", remaining: list = None):
    await _col().update_one(
        {"_id": ObjectId(broadcast_id)},
        {"$set": {
            "status":           status,
            "remaining_users":  remaining or [],
            "updated_at":       datetime.now(timezone.utc),
        }},
    )


async def cancel_broadcast(broadcast_id: str):
    await finish_broadcast(broadcast_id, "cancelled")


async def get_broadcast(broadcast_id: str) -> Optional[dict]:
    doc = await _col().find_one({"_id": ObjectId(broadcast_id)})
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc


async def get_recent_broadcasts(limit: int = 20) -> List[dict]:
    cursor = _col().find({}).sort("created_at", -1).limit(limit)
    docs = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        docs.append(d)
    return docs


async def get_resumable() -> List[dict]:
    """Broadcasts saved mid-flight that can be resumed."""
    cursor = _col().find({"status": "saved", "remaining_users.0": {"$exists": True}})
    docs = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        docs.append(d)
    return docs


# ── Broadcast templates (Fix #11) ─────────────────────────────────────────────

async def save_template(name: str, text: str, created_by: int) -> str:
    r = await _tmpl_col().insert_one({
        "name":       name,
        "text":       text,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc),
    })
    return str(r.inserted_id)


async def get_templates() -> List[dict]:
    cursor = _tmpl_col().find({}).sort("created_at", -1)
    docs = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        docs.append(d)
    return docs


async def delete_template(template_id: str):
    await _tmpl_col().delete_one({"_id": ObjectId(template_id)})


# ── Scheduled broadcasts (Fix #10) ────────────────────────────────────────────

async def schedule_broadcast(
    target_bot_id: Optional[int],
    text: str,
    run_at: datetime,
    created_by: int,
) -> str:
    r = await _sched_col().insert_one({
        "target_bot_id": target_bot_id,
        "text":          text,
        "run_at":        run_at,
        "created_by":    created_by,
        "status":        "pending",   # pending | done | cancelled
        "created_at":    datetime.now(timezone.utc),
    })
    return str(r.inserted_id)


async def get_due_schedules() -> List[dict]:
    """Return all pending schedules whose run_at is in the past."""
    now = datetime.now(timezone.utc)
    cursor = _sched_col().find({"status": "pending", "run_at": {"$lte": now}})
    docs = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        docs.append(d)
    return docs


async def mark_schedule_done(schedule_id: str):
    await _sched_col().update_one(
        {"_id": ObjectId(schedule_id)},
        {"$set": {"status": "done", "done_at": datetime.now(timezone.utc)}},
    )


async def get_pending_schedules() -> List[dict]:
    cursor = _sched_col().find({"status": "pending"}).sort("run_at", 1)
    docs = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        docs.append(d)
    return docs


async def cancel_schedule(schedule_id: str):
    await _sched_col().update_one(
        {"_id": ObjectId(schedule_id)},
        {"$set": {"status": "cancelled"}},
    )
