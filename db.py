"""
database/db.py — Single Motor client shared across the whole app
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from config import MONGO_URI, DB_NAME

_client: AsyncIOMotorClient = None

def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            MONGO_URI,
            serverSelectionTimeoutMS=10000,
            maxPoolSize=50,      # connection pool for high load
        )
    return _client

def get_db() -> AsyncIOMotorDatabase:
    return get_client()[DB_NAME]
