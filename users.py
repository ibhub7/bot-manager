"""
database/users.py — All user-related DB operations
Fix #13: Smart blocked detection — only permanently block after 3 consecutive failures
"""
from datetime import datetime, timezone
from typing import List
from motor.motor_asyncio import AsyncIOMotorCollection
from database.db import get_db


def _col() -> AsyncIOMotorCollection:
    return get_db()["users"]


async def ensure_indexes():
    col = _col()
    await col.create_index([("user_id", 1), ("bot_id", 1)], unique=True)
    await col.create_index("bot_id")
    await col.create_index([("bot_id", 1), ("is_active", 1), ("is_blocked", 1), ("closed", 1)])
    await col.create_index("joined_at")


async def add_user(
    user_id: int,
    bot_id: int,
    first_name: str = "",
    username: str = "",
    source: str = "organic",
) -> bool:
    """Upsert user. Returns True if newly inserted."""
    col = _col()
    result = await col.update_one(
        {"user_id": user_id, "bot_id": bot_id},
        {
            "$setOnInsert": {
                "user_id":      user_id,
                "bot_id":       bot_id,
                "first_name":   first_name,
                "username":     username,
                "is_active":    True,
                "is_blocked":   False,
                "closed":       False,
                "source":       source,
                "fail_count":   0,       # Fix #13: track consecutive failures
                "joined_at":    datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    # Refresh name fields but never touch flags
    await col.update_one(
        {"user_id": user_id, "bot_id": bot_id},
        {"$set": {"first_name": first_name, "username": username}},
    )
    return result.upserted_id is not None


async def mark_blocked(user_id: int, bot_id: int):
    """Permanently block — called only for UserIsBlocked/Deactivated errors."""
    await _col().update_one(
        {"user_id": user_id, "bot_id": bot_id},
        {"$set": {"is_blocked": True, "is_active": False, "fail_count": 0}},
    )


async def increment_fail(user_id: int, bot_id: int):
    """
    Fix #13: Increment fail_count. After 3 consecutive failures → auto-block.
    This prevents FloodWait errors from wrongly blocking users.
    """
    col = _col()
    result = await col.find_one_and_update(
        {"user_id": user_id, "bot_id": bot_id},
        {"$inc": {"fail_count": 1}},
        return_document=True,
    )
    if result and result.get("fail_count", 0) >= 3:
        await mark_blocked(user_id, bot_id)


async def reset_fail(user_id: int, bot_id: int):
    """Reset fail count on successful send."""
    await _col().update_one(
        {"user_id": user_id, "bot_id": bot_id},
        {"$set": {"fail_count": 0}},
    )


async def mark_active(user_id: int, bot_id: int):
    await _col().update_one(
        {"user_id": user_id, "bot_id": bot_id},
        {"$set": {"is_blocked": False, "is_active": True, "fail_count": 0}},
    )


async def close_bot_users(bot_id: int) -> int:
    r = await _col().update_many({"bot_id": bot_id}, {"$set": {"closed": True}})
    return r.modified_count


async def open_bot_users(bot_id: int) -> int:
    r = await _col().update_many({"bot_id": bot_id}, {"$set": {"closed": False}})
    return r.modified_count


async def close_user(user_id: int, bot_id: int):
    await _col().update_one({"user_id": user_id, "bot_id": bot_id}, {"$set": {"closed": True}})


async def open_user(user_id: int, bot_id: int):
    await _col().update_one({"user_id": user_id, "bot_id": bot_id}, {"$set": {"closed": False}})


async def get_broadcast_users(bot_id: int) -> List[int]:
    """Fetch eligible user IDs: active + not blocked + not closed."""
    cursor = _col().find(
        {"bot_id": bot_id, "is_active": True, "is_blocked": False, "closed": False},
        {"user_id": 1, "_id": 0},
    )
    return [doc["user_id"] async for doc in cursor]


async def get_all_unique_users() -> List[int]:
    """Deduplicated across ALL bots."""
    pipeline = [
        {"$match": {"is_active": True, "is_blocked": False, "closed": False}},
        {"$group": {"_id": "$user_id"}},
    ]
    return [doc["_id"] async for doc in _col().aggregate(pipeline)]


async def get_failed_users_for_broadcast(broadcast_id: str) -> List[int]:
    """Get users marked as failed for a specific broadcast — for retry."""
    cursor = get_db()["broadcast_failures"].find(
        {"broadcast_id": broadcast_id},
        {"user_id": 1, "_id": 0},
    )
    return [doc["user_id"] async for doc in cursor]


async def stats_for_bot(bot_id: int) -> dict:
    col = _col()
    total    = await col.count_documents({"bot_id": bot_id})
    active   = await col.count_documents({"bot_id": bot_id, "is_active": True})
    blocked  = await col.count_documents({"bot_id": bot_id, "is_blocked": True})
    closed   = await col.count_documents({"bot_id": bot_id, "closed": True})
    eligible = await col.count_documents({
        "bot_id": bot_id, "is_active": True, "is_blocked": False, "closed": False
    })
    imported = await col.count_documents({"bot_id": bot_id, "source": "imported"})
    return {
        "total": total, "active": active, "blocked": blocked,
        "closed": closed, "eligible": eligible, "imported": imported,
    }


async def global_stats() -> dict:
    col = _col()
    total    = await col.count_documents({})
    active   = await col.count_documents({"is_active": True})
    blocked  = await col.count_documents({"is_blocked": True})
    eligible = await col.count_documents({"is_active": True, "is_blocked": False, "closed": False})
    return {"total": total, "active": active, "blocked": blocked, "eligible": eligible}


async def daily_growth(bot_id: int = None, days: int = 14) -> list:
    """Fix #12: Per-day user join counts for analytics chart."""
    from datetime import timedelta
    match = {}
    if bot_id:
        match["bot_id"] = bot_id
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "y": {"$year": "$joined_at"},
                "m": {"$month": "$joined_at"},
                "d": {"$dayOfMonth": "$joined_at"},
            },
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
        {"$limit": days},
    ]
    result = []
    async for doc in _col().aggregate(pipeline):
        d = doc["_id"]
        result.append({
            "date": f"{d['y']}-{d['m']:02d}-{d['d']:02d}",
            "count": doc["count"],
        })
    return result


async def import_users_bulk(users: list, bot_id: int) -> dict:
    """Bulk-upsert imported users."""
    from pymongo import UpdateOne
    col = _col()
    inserted = skipped = 0
    now = datetime.now(timezone.utc)

    for chunk_start in range(0, len(users), 500):
        chunk = users[chunk_start: chunk_start + 500]
        ops = []
        for u in chunk:
            uid = int(u.get("user_id") or u.get("id") or u.get("_id") or 0)
            if not uid:
                continue
            ops.append(UpdateOne(
                {"user_id": uid, "bot_id": bot_id},
                {"$setOnInsert": {
                    "user_id": uid, "bot_id": bot_id,
                    "first_name": u.get("first_name", ""),
                    "username":   u.get("username", ""),
                    "is_active":  True, "is_blocked": False,
                    "closed":     False, "fail_count": 0,
                    "source":     "imported",
                    "joined_at":  now,
                }},
                upsert=True,
            ))
        if ops:
            res = await col.bulk_write(ops, ordered=False)
            inserted += res.upserted_count
            skipped  += len(ops) - res.upserted_count

    return {"inserted": inserted, "skipped": skipped}
