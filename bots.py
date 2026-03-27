"""
database/bots.py — Bot registry CRUD
"""
from datetime import datetime, timezone
from typing import List, Optional
from database.db import get_db


def _col():
    return get_db()["bots"]


async def ensure_indexes():
    await _col().create_index("bot_id", unique=True)
    await _col().create_index("is_active")


async def register_bot(bot_id: int, bot_name: str, token: str, owner_id: int = 0) -> bool:
    r = await _col().update_one(
        {"bot_id": bot_id},
        {
            "$setOnInsert": {
                "bot_id":        bot_id,
                "bot_name":      bot_name,
                "token":         token,
                "owner_id":      owner_id,
                "is_active":     True,
                "status":        "online",
                "registered_at": datetime.now(timezone.utc),
            },
            "$set": {
                "bot_name": bot_name,
                "token":    token,
                "last_seen": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )
    return r.upserted_id is not None


async def get_bot(bot_id: int) -> Optional[dict]:
    return await _col().find_one({"bot_id": bot_id})


async def get_all_bots() -> List[dict]:
    return await _col().find({}).to_list(length=None)


async def get_active_bots() -> List[dict]:
    return await _col().find({"is_active": True}).to_list(length=None)


async def set_active(bot_id: int, active: bool):
    await _col().update_one({"bot_id": bot_id}, {"$set": {"is_active": active}})


async def set_status(bot_id: int, status: str):
    await _col().update_one(
        {"bot_id": bot_id},
        {"$set": {"status": status, "last_seen": datetime.now(timezone.utc)}},
    )


async def update_heartbeat(bot_id: int):
    await _col().update_one(
        {"bot_id": bot_id},
        {"$set": {"last_seen": datetime.now(timezone.utc), "status": "online"}},
    )


async def remove_bot(bot_id: int):
    await _col().delete_one({"bot_id": bot_id})
