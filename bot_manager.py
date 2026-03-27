"""
bot_manager.py — Dynamic Pyrogram client pool
Fix #2: No circular imports — lazy imports inside methods
Fix #4: File-based sessions in SESSIONS_DIR (not in_memory)
"""
import asyncio
import os
from typing import Dict, Optional

from pyrogram import Client
from pyrogram.errors import AuthKeyUnregistered, AccessTokenInvalid

from config import API_ID, API_HASH, HEARTBEAT_INTERVAL, SESSIONS_DIR


class BotManager:
    def __init__(self):
        self._clients: Dict[int, Client] = {}
        self._tasks:   Dict[int, asyncio.Task] = {}

        # Fix #4: create sessions directory
        os.makedirs(SESSIONS_DIR, exist_ok=True)

    # ── Start all active bots ─────────────────────────────────────────────────

    async def start_all(self):
        # Fix #2: import inside method to avoid circular import at module load
        from database import bots as bots_db
        bots = await bots_db.get_active_bots()
        print(f"[BotManager] Starting {len(bots)} bot(s)...")
        results = await asyncio.gather(
            *[self._start_bot(b) for b in bots],
            return_exceptions=True
        )
        online = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        print(f"[BotManager] {online}/{len(bots)} bot(s) started successfully")

    async def _start_bot(self, bot_doc: dict) -> Optional[Client]:
        from database import bots as bots_db

        bot_id = bot_doc["bot_id"]
        token  = bot_doc["token"]

        if bot_id in self._clients:
            return self._clients[bot_id]

        try:
            # Fix #4: file-based session — survives restarts without re-auth
            session_path = os.path.join(SESSIONS_DIR, f"bot_{bot_id}")
            client = Client(
                name=session_path,
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=token,
            )
            await client.start()
            self._clients[bot_id] = client
            await bots_db.set_status(bot_id, "online")

            me = await client.get_me()
            print(f"[BotManager] ✅ @{me.username} (id={bot_id}) online")

            # Start heartbeat
            self._tasks[bot_id] = asyncio.create_task(
                self._heartbeat(bot_id),
                name=f"heartbeat_{bot_id}"
            )
            return client

        except (AuthKeyUnregistered, AccessTokenInvalid) as e:
            print(f"[BotManager] ❌ Bot {bot_id} bad token: {e}")
            from database import bots as bots_db
            await bots_db.set_status(bot_id, "error")
            return None

        except Exception as e:
            print(f"[BotManager] ❌ Bot {bot_id} failed: {e}")
            from database import bots as bots_db
            await bots_db.set_status(bot_id, "error")
            return None

    # ── Runtime add / remove ──────────────────────────────────────────────────

    async def add_bot(self, token: str) -> dict:
        """Add new bot by token, register in DB, start client."""
        from database import bots as bots_db

        # Temporarily connect to get bot info
        session_tmp = os.path.join(SESSIONS_DIR, "_tmp_verify")
        tmp = Client(session_tmp, api_id=API_ID, api_hash=API_HASH, bot_token=token)
        async with tmp:
            me = await tmp.get_me()

        await bots_db.register_bot(me.id, me.username or str(me.id), token)
        bot_doc = await bots_db.get_bot(me.id)
        client  = await self._start_bot(bot_doc)

        # Attach start handler
        from handlers.start import register_start_handler
        if client:
            register_start_handler(client, me.id)

        return {"bot_id": me.id, "username": me.username}

    async def remove_bot(self, bot_id: int):
        from database import bots as bots_db

        task = self._tasks.pop(bot_id, None)
        if task:
            task.cancel()

        client = self._clients.pop(bot_id, None)
        if client:
            try:
                await client.stop()
            except Exception:
                pass

        await bots_db.set_status(bot_id, "offline")
        print(f"[BotManager] Bot {bot_id} stopped")

    async def restart_bot(self, bot_id: int):
        await self.remove_bot(bot_id)
        from database import bots as bots_db
        bot_doc = await bots_db.get_bot(bot_id)
        if bot_doc:
            await self._start_bot(bot_doc)

    # ── Getters ───────────────────────────────────────────────────────────────

    def get_client(self, bot_id: int) -> Optional[Client]:
        return self._clients.get(bot_id)

    def get_all_clients(self) -> Dict[int, Client]:
        return dict(self._clients)

    def get_online_clients(self) -> Dict[int, Client]:
        return {bid: c for bid, c in self._clients.items() if c.is_connected}

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat(self, bot_id: int):
        from database import bots as bots_db
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                client = self._clients.get(bot_id)
                if client and client.is_connected:
                    await bots_db.update_heartbeat(bot_id)
                else:
                    await bots_db.set_status(bot_id, "offline")
                    print(f"[BotManager] Bot {bot_id} disconnected — reconnecting...")
                    bot_doc = await bots_db.get_bot(bot_id)
                    if bot_doc and bot_doc.get("is_active"):
                        await self._start_bot(bot_doc)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[BotManager] Heartbeat error {bot_id}: {e}")

    # ── Shutdown ──────────────────────────────────────────────────────────────

    async def stop_all(self):
        for bot_id in list(self._clients.keys()):
            await self.remove_bot(bot_id)
        print("[BotManager] All bots stopped")


# Global singleton
manager = BotManager()
