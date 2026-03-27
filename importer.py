"""
utils/importer.py — Import users from an external MongoDB collection
Fix #6: Import URL is accepted via DM only and deleted after reading
"""
import asyncio
from typing import Tuple, Optional, Callable

from motor.motor_asyncio import AsyncIOMotorClient
from database.users import import_users_bulk


async def import_from_mongo(
    mongo_url: str,
    db_name: str,
    collection_name: str,
    target_bot_id: int,
    on_progress: Optional[Callable] = None,
) -> Tuple[int, int, str]:
    """
    Connect to external MongoDB, read all documents, import into local users.
    Returns (inserted, skipped, error_message).
    """
    try:
        ext_client = AsyncIOMotorClient(
            mongo_url,
            serverSelectionTimeoutMS=10000,
        )
        # Test connection
        await ext_client.server_info()

        ext_col = ext_client[db_name][collection_name]
        total = await ext_col.count_documents({})
        if total == 0:
            ext_client.close()
            return 0, 0, "Collection is empty"

        CHUNK = 500
        inserted_total = skipped_total = 0

        for offset in range(0, total, CHUNK):
            docs = await ext_col.find(
                {},
                {"user_id": 1, "id": 1, "first_name": 1, "username": 1, "_id": 0}
            ).skip(offset).limit(CHUNK).to_list(length=CHUNK)

            result = await import_users_bulk(docs, target_bot_id)
            inserted_total += result["inserted"]
            skipped_total  += result["skipped"]

            if on_progress:
                try:
                    await on_progress(inserted_total, skipped_total, total)
                except Exception:
                    pass

            await asyncio.sleep(0)   # yield to event loop

        ext_client.close()
        return inserted_total, skipped_total, ""

    except Exception as e:
        return 0, 0, str(e)
