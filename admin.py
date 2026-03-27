"""
handlers/admin.py — Master bot admin commands (all fixes applied)

Fix #3:  Broadcast runs via asyncio.create_task() — dashboard stays responsive
Fix #6:  /import_mongo only works in private DM + auto-deletes the message
Fix #8:  /retry <broadcast_id> — re-sends to all failed users
Fix #10: /schedule <bot_id> <YYYY-MM-DD HH:MM> — MongoDB-based scheduler
Fix #11: /savetemplate + /templates — broadcast templates in MongoDB
"""
import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import ADMINS
from database import users as users_db, bots as bots_db, broadcasts as bc_db
from utils.broadcaster import (
    run_broadcast, request_cancel, progress_bar, readable_time
)
from utils.importer import import_from_mongo


def register_admin_handlers(master: Client):
    # Per-admin external Mongo sessions (for /connect_mongodb workflow)
    mongo_sessions: Dict[int, dict] = {}

    # ─── Helper: run broadcast as background task (Fix #3) ───────────────────

    async def _launch_broadcast(
        client, msg, user_ids, pin, target_bot_id, label, resume_from=0, bc_id=None
    ):
        from bot_manager import manager
        online = manager.get_online_clients()
        if not online:
            return await msg.reply("❌ ɴᴏ ʙᴏᴛꜱ ᴏɴʟɪɴᴇ.")

        if not bc_id:
            bc_id = await bc_db.create_broadcast(
                target_bot_id=target_bot_id,
                sender_bot_ids=list(online.keys()),
                total_users=len(user_ids),
                initiated_by=msg.from_user.id,
            )

        sts = await msg.reply(
            f"🚀 ʙʀᴏᴀᴅᴄᴀꜱᴛ ꜱᴛᴀʀᴛᴇᴅ\n"
            f"📦 ᴜꜱᴇʀꜱ  : <code>{len(user_ids)}</code>\n"
            f"🤖 ʙᴏᴛꜱ   : <code>{len(online)}</code>\n"
            f"📌 ᴛᴀʀɢᴇᴛ : {label}\n"
            f"🆔 ɪᴅ     : <code>{bc_id[-8:]}</code>"
        )

        btn = [[InlineKeyboardButton("🛑 ᴄᴀɴᴄᴇʟ", callback_data=f"bc_cancel#{bc_id}")]]

        async def on_progress(done, success, failed, total, speed, eta):
            try:
                await sts.edit(
                    f"╭─── 📊 [{bc_id[-6:]}] ───╮\n\n"
                    f"{progress_bar(done, total)}\n\n"
                    f"📦 ᴛᴏᴛᴀʟ   : <code>{total}</code>\n"
                    f"✅ ꜱᴜᴄᴄᴇꜱꜱ : <code>{success}</code>\n"
                    f"❌ ꜰᴀɪʟᴇᴅ  : <code>{failed}</code>\n"
                    f"⚡ ꜱᴘᴇᴇᴅ   : <code>{round(speed,1)}/s</code>\n"
                    f"⏳ ᴇᴛᴀ     : <code>{readable_time(eta)}</code>\n"
                    f"🤖 ʙᴏᴛꜱ   : <code>{len(online)}</code>\n\n"
                    f"╰──────────────────────────╯",
                    reply_markup=InlineKeyboardMarkup(btn)
                )
            except Exception:
                pass

        # Fix #3: run as background task — doesn't block web dashboard or other commands
        async def _task():
            b_msg = msg.reply_to_message
            success, failed_count = await run_broadcast(
                clients=online,
                user_ids=user_ids,
                message=b_msg,
                broadcast_id=bc_id,
                pin=pin,
                resume_from=resume_from,
                on_progress=on_progress,
            )
            await bc_db.finish_broadcast(bc_id, "completed")
            await sts.edit(
                f"╭─── ✅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ [{bc_id[-6:]}] ───╮\n\n"
                f"{progress_bar(len(user_ids), len(user_ids))}\n\n"
                f"📦 ᴛᴏᴛᴀʟ   : <code>{len(user_ids)}</code>\n"
                f"✅ ꜱᴜᴄᴄᴇꜱꜱ : <code>{success}</code>\n"
                f"❌ ꜰᴀɪʟᴇᴅ  : <code>{failed_count}</code>\n\n"
                f"╰──────────────────────────╯"
            )

        asyncio.create_task(_task())   # ← Fix #3: non-blocking

    # ─── /bots ────────────────────────────────────────────────────────────────

    @master.on_message(filters.command("bots") & filters.user(ADMINS))
    async def cmd_bots(_, msg: Message):
        from config import HEARTBEAT_TIMEOUT
        bots = await bots_db.get_all_bots()
        if not bots:
            return await msg.reply("❌ ɴᴏ ʙᴏᴛꜱ ʀᴇɢɪꜱᴛᴇʀᴇᴅ.")
        lines = ["<b>📋 ʀᴇɢɪꜱᴛᴇʀᴇᴅ ʙᴏᴛꜱ</b>\n"]
        for b in bots:
            last = b.get("last_seen")
            if last:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                age   = (datetime.now(timezone.utc) - last).total_seconds()
                emoji = "🟢" if age < HEARTBEAT_TIMEOUT else "🔴"
            else:
                emoji = "❓"
            s = await users_db.stats_for_bot(b["bot_id"])
            lines.append(
                f"{emoji} @{b.get('bot_name','?')} (<code>{b['bot_id']}</code>)\n"
                f"   👥 {s['total']} | 📤 {s['eligible']} | "
                f"🔒 {s['closed']} | ⛔ {s['blocked']}"
            )
        await msg.reply("\n".join(lines))

    # ─── /addbot ──────────────────────────────────────────────────────────────

    @master.on_message(filters.command("addbot") & filters.user(ADMINS))
    async def cmd_addbot(_, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply("ᴜꜱᴀɢᴇ: /addbot <bot_token>")
        sts = await msg.reply("⏳ ᴠᴇʀɪꜰʏɪɴɢ ᴛᴏᴋᴇɴ...")
        try:
            from bot_manager import manager
            info = await manager.add_bot(args[0])
            await sts.edit(
                f"✅ ʙᴏᴛ ᴀᴅᴅᴇᴅ\n"
                f"@{info['username']} (<code>{info['bot_id']}</code>)"
            )
        except Exception as e:
            await sts.edit(f"❌ {e}")

    # ─── /removebot ───────────────────────────────────────────────────────────

    @master.on_message(filters.command("removebot") & filters.user(ADMINS))
    async def cmd_removebot(_, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply("ᴜꜱᴀɢᴇ: /removebot <bot_id>")
        from bot_manager import manager
        bot_id = int(args[0])
        await manager.remove_bot(bot_id)
        await bots_db.remove_bot(bot_id)
        await msg.reply(f"🗑 ʙᴏᴛ <code>{bot_id}</code> ʀᴇᴍᴏᴠᴇᴅ.")

    # ─── /stats ───────────────────────────────────────────────────────────────

    @master.on_message(filters.command("stats") & filters.user(ADMINS))
    async def cmd_stats(_, msg: Message):
        args = msg.command[1:]
        if args:
            bot_id = int(args[0])
            s   = await users_db.stats_for_bot(bot_id)
            bot = await bots_db.get_bot(bot_id)
            lbl = f"@{bot['bot_name']}" if bot else str(bot_id)
            await msg.reply(
                f"<b>📊 {lbl}</b>\n\n"
                f"👥 ᴛᴏᴛᴀʟ    : <code>{s['total']}</code>\n"
                f"✅ ᴀᴄᴛɪᴠᴇ   : <code>{s['active']}</code>\n"
                f"📤 ᴇʟɪɢɪʙʟᴇ : <code>{s['eligible']}</code>\n"
                f"🔒 ᴄʟᴏꜱᴇᴅ  : <code>{s['closed']}</code>\n"
                f"⛔ ʙʟᴏᴄᴋᴇᴅ : <code>{s['blocked']}</code>\n"
                f"📥 ɪᴍᴘᴏʀᴛᴇᴅ: <code>{s['imported']}</code>"
            )
        else:
            g = await users_db.global_stats()
            await msg.reply(
                f"<b>🌐 ɢʟᴏʙᴀʟ ꜱᴛᴀᴛꜱ</b>\n\n"
                f"👥 ᴛᴏᴛᴀʟ    : <code>{g['total']}</code>\n"
                f"✅ ᴀᴄᴛɪᴠᴇ   : <code>{g['active']}</code>\n"
                f"📤 ᴇʟɪɢɪʙʟᴇ : <code>{g['eligible']}</code>\n"
                f"⛔ ʙʟᴏᴄᴋᴇᴅ : <code>{g['blocked']}</code>"
            )

    # ─── /close_bot_users / /open_bot_users ───────────────────────────────────

    @master.on_message(filters.command("close_bot_users") & filters.user(ADMINS))
    async def cmd_close(_, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply("ᴜꜱᴀɢᴇ: /close_bot_users <bot_id>")
        count = await users_db.close_bot_users(int(args[0]))
        await msg.reply(f"🔒 <code>{count}</code> ᴜꜱᴇʀꜱ ᴄʟᴏꜱᴇᴅ.")

    @master.on_message(filters.command("open_bot_users") & filters.user(ADMINS))
    async def cmd_open(_, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply("ᴜꜱᴀɢᴇ: /open_bot_users <bot_id>")
        count = await users_db.open_bot_users(int(args[0]))
        await msg.reply(f"🔓 <code>{count}</code> ᴜꜱᴇʀꜱ ʀᴇᴏᴘᴇɴᴇᴅ.")

    # ─── /broadcast ───────────────────────────────────────────────────────────

    @master.on_message(
        filters.command(["broadcast", "pin_broadcast", "allbroadcast"]) &
        filters.user(ADMINS) & filters.reply
    )
    async def cmd_broadcast(client: Client, msg: Message):
        cmd  = msg.command[0]
        args = msg.command[1:]
        pin  = "pin" in cmd

        if cmd == "allbroadcast":
            user_ids      = await users_db.get_all_unique_users()
            label         = "ᴀʟʟ ʙᴏᴛꜱ"
            target_bot_id = None
        else:
            if not args:
                return await msg.reply("ᴜꜱᴀɢᴇ: /broadcast <bot_id>")
            target_bot_id = int(args[0])
            user_ids      = await users_db.get_broadcast_users(target_bot_id)
            bot           = await bots_db.get_bot(target_bot_id)
            label         = f"@{bot['bot_name']}" if bot else str(target_bot_id)

        if not user_ids:
            return await msg.reply("❌ ɴᴏ ᴇʟɪɢɪʙʟᴇ ᴜꜱᴇʀꜱ.")

        await _launch_broadcast(client, msg, user_ids, pin, target_bot_id, label)

    # ─── /retry (Fix #8) ──────────────────────────────────────────────────────

    @master.on_message(
        filters.command("retry") & filters.user(ADMINS) & filters.reply
    )
    async def cmd_retry(client: Client, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply("ᴜꜱᴀɢᴇ: /retry <broadcast_id>  (reply ᴛᴏ ᴀ ᴍᴇꜱꜱᴀɢᴇ)")

        bc_id    = args[0]
        bc       = await bc_db.get_broadcast(bc_id)
        if not bc:
            return await msg.reply("❌ ʙʀᴏᴀᴅᴄᴀꜱᴛ ɴᴏᴛ ꜰᴏᴜɴᴅ.")

        user_ids = await bc_db.get_failed_users(bc_id)
        if not user_ids:
            return await msg.reply("✅ ɴᴏ ꜰᴀɪʟᴇᴅ ᴜꜱᴇʀꜱ ᴛᴏ ʀᴇᴛʀʏ.")

        await bc_db.clear_failed_users(bc_id)
        target   = bc.get("target_bot_id")
        label    = f"retry of {bc_id[-8:]}"
        await _launch_broadcast(client, msg, user_ids, False, target, label)

    # ─── /cancel ──────────────────────────────────────────────────────────────

    @master.on_message(filters.command("cancel") & filters.user(ADMINS))
    async def cmd_cancel(_, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply("ᴜꜱᴀɢᴇ: /cancel <broadcast_id>")
        request_cancel(args[0])
        await bc_db.cancel_broadcast(args[0])
        await msg.reply(f"🛑 ᴄᴀɴᴄᴇʟʟᴇᴅ.")

    @master.on_callback_query(filters.regex(r"^bc_cancel#"))
    async def cb_cancel(_, query: CallbackQuery):
        if query.from_user.id not in ADMINS:
            return await query.answer("❌", show_alert=True)
        bc_id = query.data.split("#", 1)[1]
        request_cancel(bc_id)
        await bc_db.cancel_broadcast(bc_id)
        await query.answer("🛑 ᴄᴀɴᴄᴇʟʟɪɴɢ...", show_alert=True)
        await query.message.edit_reply_markup(None)

    # ─── /import_mongo (Fix #6 — DM only + auto-delete) ──────────────────────

    @master.on_message(
        filters.command("import_mongo") &
        filters.user(ADMINS) &
        filters.private   # Fix #6: only in private chat
    )
    async def cmd_import(_, msg: Message):
        args = msg.command[1:]
        if len(args) < 4:
            return await msg.reply(
                "ᴜꜱᴀɢᴇ (ᴅᴍ ᴏɴʟʏ):\n"
                "/import_mongo <mongo_url> <db> <collection> <bot_id>"
            )

        # Fix #6: delete the command message immediately (credentials stay private)
        try:
            await msg.delete()
        except Exception:
            pass

        mongo_url, db_name, collection, bot_id = (
            args[0], args[1], args[2], int(args[3])
        )
        sts = await msg.respond("⏳ ᴄᴏɴɴᴇᴄᴛɪɴɢ ᴛᴏ ᴇxᴛᴇʀɴᴀʟ ᴅʙ...")

        async def progress(ins, skp, total):
            try:
                await sts.edit(
                    f"📥 ɪᴍᴘᴏʀᴛɪɴɢ...\n"
                    f"✅ ɪɴꜱᴇʀᴛᴇᴅ : <code>{ins}</code>\n"
                    f"⏭ ꜱᴋɪᴘᴘᴇᴅ  : <code>{skp}</code>\n"
                    f"📦 ᴛᴏᴛᴀʟ    : <code>{total}</code>"
                )
            except Exception:
                pass

        ins, skp, err = await import_from_mongo(
            mongo_url, db_name, collection, bot_id, on_progress=progress
        )

        if err:
            await sts.edit(f"❌ ꜰᴀɪʟᴇᴅ: <code>{err}</code>")
        else:
            await sts.edit(
                f"✅ ɪᴍᴘᴏʀᴛ ᴄᴏᴍᴘʟᴇᴛᴇᴅ\n"
                f"✅ ɪɴꜱᴇʀᴛᴇᴅ : <code>{ins}</code>\n"
                f"⏭ ꜱᴋɪᴘᴘᴇᴅ  : <code>{skp}</code>"
            )

    # ─── External Mongo tools (connect/list/clear/drop/clone) ────────────────

    def _session(admin_id: int) -> Optional[dict]:
        return mongo_sessions.get(admin_id)

    @master.on_message(
        filters.command("connect_mongodb") & filters.user(ADMINS) & filters.private
    )
    async def cmd_connect_mongodb(_, msg: Message):
        args = msg.command[1:]
        if not args:
            return await msg.reply(
                "ᴜꜱᴀɢᴇ: /connect_mongodb <mongo_url> [db_name]"
            )

        mongo_url = args[0]
        db_name = args[1] if len(args) > 1 else None

        # Close any previous session for this admin
        old = _session(msg.from_user.id)
        if old:
            try:
                old["client"].close()
            except Exception:
                pass

        sts = await msg.reply("⏳ ᴄᴏɴɴᴇᴄᴛɪɴɢ...")
        try:
            client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=10000)
            await client.server_info()
            if not db_name:
                db_name = await client.get_default_database().name if client.get_default_database() else None
            mongo_sessions[msg.from_user.id] = {
                "client": client,
                "url": mongo_url,
                "db_name": db_name,
            }
            await sts.edit(
                "✅ ᴍᴏɴɢᴏᴅʙ ᴄᴏɴɴᴇᴄᴛᴇᴅ\n"
                f"🗂 ᴅᴇꜰᴀᴜʟᴛ ᴅʙ: <code>{db_name or '(not set)'}</code>\n"
                "ɴᴇxᴛ: /mongo_collections [db_name]"
            )
        except Exception as e:
            await sts.edit(f"❌ ᴄᴏɴɴᴇᴄᴛ ꜰᴀɪʟᴇᴅ: <code>{e}</code>")

    @master.on_message(
        filters.command("mongo_disconnect") & filters.user(ADMINS) & filters.private
    )
    async def cmd_mongo_disconnect(_, msg: Message):
        sess = _session(msg.from_user.id)
        if not sess:
            return await msg.reply("❌ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴍᴏɴɢᴏ ꜱᴇꜱꜱɪᴏɴ.")
        try:
            sess["client"].close()
        except Exception:
            pass
        mongo_sessions.pop(msg.from_user.id, None)
        await msg.reply("✅ ᴍᴏɴɢᴏ ꜱᴇꜱꜱɪᴏɴ ᴄʟᴏꜱᴇᴅ.")

    @master.on_message(
        filters.command("mongo_collections") & filters.user(ADMINS) & filters.private
    )
    async def cmd_mongo_collections(_, msg: Message):
        sess = _session(msg.from_user.id)
        if not sess:
            return await msg.reply("❌ ꜰɪʀꜱᴛ ᴜꜱᴇ /connect_mongodb <url> [db_name]")

        db_name = msg.command[1] if len(msg.command) > 1 else sess.get("db_name")
        if not db_name:
            return await msg.reply("❌ ᴅʙ ɴᴀᴍᴇ ɴᴏᴛ ꜱᴇᴛ. ᴜꜱᴇ /mongo_collections <db_name>")

        db = sess["client"][db_name]
        cols = await db.list_collection_names()
        if not cols:
            return await msg.reply(f"ℹ️ ɴᴏ ᴄᴏʟʟᴇᴄᴛɪᴏɴꜱ ɪɴ <code>{db_name}</code>.")

        lines = [f"<b>🗂 ᴄᴏʟʟᴇᴄᴛɪᴏɴꜱ ɪɴ {db_name}</b>\n"]
        for c in cols:
            count = await db[c].count_documents({})
            lines.append(f"• <code>{c}</code> — {count} docs")
        await msg.reply("\n".join(lines))

    @master.on_message(
        filters.command("mongo_drop_collection") & filters.user(ADMINS) & filters.private
    )
    async def cmd_mongo_drop_collection(_, msg: Message):
        sess = _session(msg.from_user.id)
        if not sess:
            return await msg.reply("❌ ꜰɪʀꜱᴛ ᴜꜱᴇ /connect_mongodb <url> [db_name]")
        args = msg.command[1:]
        if not args:
            return await msg.reply(
                "ᴜꜱᴀɢᴇ: /mongo_drop_collection <collection> [db_name]"
            )
        collection = args[0]
        db_name = args[1] if len(args) > 1 else sess.get("db_name")
        if not db_name:
            return await msg.reply("❌ ᴅʙ ɴᴀᴍᴇ ɴᴏᴛ ꜱᴇᴛ.")

        await sess["client"][db_name][collection].drop()
        await msg.reply(f"🗑 ᴅʀᴏᴘᴘᴇᴅ <code>{db_name}.{collection}</code>")

    @master.on_message(
        filters.command("mongo_clear_collection") & filters.user(ADMINS) & filters.private
    )
    async def cmd_mongo_clear_collection(_, msg: Message):
        sess = _session(msg.from_user.id)
        if not sess:
            return await msg.reply("❌ ꜰɪʀꜱᴛ ᴜꜱᴇ /connect_mongodb <url> [db_name]")
        args = msg.command[1:]
        if not args:
            return await msg.reply(
                "ᴜꜱᴀɢᴇ: /mongo_clear_collection <collection> [db_name]"
            )
        collection = args[0]
        db_name = args[1] if len(args) > 1 else sess.get("db_name")
        if not db_name:
            return await msg.reply("❌ ᴅʙ ɴᴀᴍᴇ ɴᴏᴛ ꜱᴇᴛ.")

        result = await sess["client"][db_name][collection].delete_many({})
        await msg.reply(
            f"🧹 ᴄʟᴇᴀʀᴇᴅ <code>{db_name}.{collection}</code>\n"
            f"🗑 ᴅᴇʟᴇᴛᴇᴅ: <code>{result.deleted_count}</code>"
        )

    @master.on_message(
        filters.command("mongo_clone_to") & filters.user(ADMINS) & filters.private
    )
    async def cmd_mongo_clone_to(_, msg: Message):
        """
        Clone all collections from connected source DB to target MongoDB.
        Usage:
          /mongo_clone_to <target_mongo_url> [source_db] [target_db]
        """
        sess = _session(msg.from_user.id)
        if not sess:
            return await msg.reply("❌ ꜰɪʀꜱᴛ ᴜꜱᴇ /connect_mongodb <url> [db_name]")

        args = msg.command[1:]
        if not args:
            return await msg.reply(
                "ᴜꜱᴀɢᴇ: /mongo_clone_to <target_mongo_url> [source_db] [target_db]"
            )

        target_url = args[0]
        source_db_name = args[1] if len(args) > 1 else sess.get("db_name")
        target_db_name = args[2] if len(args) > 2 else source_db_name
        if not source_db_name or not target_db_name:
            return await msg.reply("❌ ꜱᴏᴜʀᴄᴇ/ᴛᴀʀɢᴇᴛ ᴅʙ ɴᴀᴍᴇ ʀᴇQᴜɪʀᴇᴅ.")

        sts = await msg.reply("⏳ ᴄʟᴏɴɪɴɢ ᴍᴏɴɢᴏᴅʙ...")
        target_client = None
        try:
            source_db = sess["client"][source_db_name]
            target_client = AsyncIOMotorClient(target_url, serverSelectionTimeoutMS=10000)
            await target_client.server_info()
            target_db = target_client[target_db_name]

            total_collections = 0
            total_docs = 0
            for col_name in await source_db.list_collection_names():
                total_collections += 1
                source_col = source_db[col_name]
                target_col = target_db[col_name]

                await target_col.delete_many({})

                batch = []
                async for doc in source_col.find({}):
                    doc.pop("_id", None)  # avoid duplicate key conflicts across DBs
                    batch.append(doc)
                    if len(batch) >= 500:
                        await target_col.insert_many(batch, ordered=False)
                        total_docs += len(batch)
                        batch = []
                if batch:
                    await target_col.insert_many(batch, ordered=False)
                    total_docs += len(batch)

            await sts.edit(
                "✅ ᴍᴏɴɢᴏ ᴄʟᴏɴᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ\n"
                f"ꜰʀᴏᴍ: <code>{source_db_name}</code>\n"
                f"ᴛᴏ: <code>{target_db_name}</code>\n"
                f"ᴄᴏʟʟᴇᴄᴛɪᴏɴꜱ: <code>{total_collections}</code>\n"
                f"ᴅᴏᴄꜱ: <code>{total_docs}</code>"
            )
        except Exception as e:
            await sts.edit(f"❌ ᴄʟᴏɴᴇ ꜰᴀɪʟᴇᴅ: <code>{e}</code>")
        finally:
            if target_client:
                target_client.close()

    # ─── /savetemplate / /templates (Fix #11) ────────────────────────────────

    @master.on_message(filters.command("savetemplate") & filters.user(ADMINS) & filters.reply)
    async def cmd_save_template(_, msg: Message):
        args = msg.command[1:]
        name = " ".join(args) if args else f"template_{int(time.time())}"
        text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        if not text:
            return await msg.reply("❌ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴛᴇxᴛ ᴍᴇꜱꜱᴀɢᴇ.")
        tid = await bc_db.save_template(name, text, msg.from_user.id)
        await msg.reply(f"✅ ᴛᴇᴍᴘʟᴀᴛᴇ ꜱᴀᴠᴇᴅ: <b>{name}</b>")

    @master.on_message(filters.command("templates") & filters.user(ADMINS))
    async def cmd_templates(_, msg: Message):
        templates = await bc_db.get_templates()
        if not templates:
            return await msg.reply("❌ ɴᴏ ᴛᴇᴍᴘʟᴀᴛᴇꜱ ꜱᴀᴠᴇᴅ.")
        lines = ["<b>📋 ᴛᴇᴍᴘʟᴀᴛᴇꜱ</b>\n"]
        for t in templates:
            lines.append(
                f"📝 <b>{t['name']}</b> (<code>{t['_id'][-6:]}</code>)\n"
                f"   {t['text'][:60]}{'...' if len(t['text']) > 60 else ''}"
            )
        await msg.reply("\n".join(lines))

    # ─── /schedule (Fix #10) ─────────────────────────────────────────────────

    @master.on_message(filters.command("schedule") & filters.user(ADMINS) & filters.reply)
    async def cmd_schedule(_, msg: Message):
        # /schedule <bot_id|all> <YYYY-MM-DD> <HH:MM>
        args = msg.command[1:]
        if len(args) < 3:
            return await msg.reply(
                "ᴜꜱᴀɢᴇ: /schedule <bot_id|all> <YYYY-MM-DD> <HH:MM>\n"
                "(reply to the message you want to schedule)"
            )
        target_raw, date_str, time_str = args[0], args[1], args[2]
        target_bot_id = None if target_raw.lower() == "all" else int(target_raw)

        try:
            run_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            run_at = run_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return await msg.reply("❌ ɪɴᴠᴀʟɪᴅ ᴅᴀᴛᴇ ꜰᴏʀᴍᴀᴛ. ᴜꜱᴇ YYYY-MM-DD HH:MM")

        text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        sid  = await bc_db.schedule_broadcast(target_bot_id, text, run_at, msg.from_user.id)
        await msg.reply(
            f"⏰ ʙʀᴏᴀᴅᴄᴀꜱᴛ ꜱᴄʜᴇᴅᴜʟᴇᴅ\n"
            f"🆔 <code>{sid[-8:]}</code>\n"
            f"📅 ʀᴜɴ ᴀᴛ: <code>{date_str} {time_str} UTC</code>\n"
            f"📌 ᴛᴀʀɢᴇᴛ: <code>{'All' if not target_bot_id else target_bot_id}</code>"
        )

    @master.on_message(filters.command("schedules") & filters.user(ADMINS))
    async def cmd_schedules(_, msg: Message):
        pending = await bc_db.get_pending_schedules()
        if not pending:
            return await msg.reply("❌ ɴᴏ ᴘᴇɴᴅɪɴɢ ꜱᴄʜᴇᴅᴜʟᴇꜱ.")
        lines = ["<b>⏰ ᴘᴇɴᴅɪɴɢ ꜱᴄʜᴇᴅᴜʟᴇꜱ</b>\n"]
        for s in pending:
            lines.append(
                f"🆔 <code>{s['_id'][-6:]}</code> → "
                f"<code>{s['run_at'].strftime('%Y-%m-%d %H:%M')}</code> UTC | "
                f"ᴛᴀʀɢᴇᴛ: <code>{'All' if not s['target_bot_id'] else s['target_bot_id']}</code>"
            )
        await msg.reply("\n".join(lines))

    # ─── /history ─────────────────────────────────────────────────────────────

    @master.on_message(filters.command("history") & filters.user(ADMINS))
    async def cmd_history(_, msg: Message):
        docs = await bc_db.get_recent_broadcasts(10)
        if not docs:
            return await msg.reply("❌ ɴᴏ ʜɪꜱᴛᴏʀʏ.")
        emoji_map = {"completed": "✅", "running": "🔄", "cancelled": "🛑", "saved": "💾"}
        lines = ["<b>📜 ʀᴇᴄᴇɴᴛ ʙʀᴏᴀᴅᴄᴀꜱᴛꜱ</b>\n"]
        for d in docs:
            e = emoji_map.get(d["status"], "❓")
            lines.append(
                f"{e} <code>{d['_id'][-8:]}</code> | "
                f"✅{d['success']}/❌{d['failed']}/{d['total_users']} | {d['status']}"
            )
        await msg.reply("\n".join(lines))

    # ─── /help ────────────────────────────────────────────────────────────────

    @master.on_message(filters.command("help") & filters.user(ADMINS))
    async def cmd_help(_, msg: Message):
        await msg.reply(
            "<b>🛠 ᴍᴀꜱᴛᴇʀ ʙᴏᴛ ᴄᴏᴍᴍᴀɴᴅꜱ</b>\n\n"
            "<b>ʙᴏᴛ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ</b>\n"
            "/bots — list bots\n"
            "/addbot &lt;token&gt; — add bot\n"
            "/removebot &lt;bot_id&gt; — remove bot\n\n"
            "<b>ᴜꜱᴇʀ ᴄᴏɴᴛʀᴏʟ</b>\n"
            "/stats | /stats &lt;bot_id&gt;\n"
            "/close_bot_users &lt;bot_id&gt;\n"
            "/open_bot_users &lt;bot_id&gt;\n\n"
            "<b>ʙʀᴏᴀᴅᴄᴀꜱᴛ</b> (reply to message)\n"
            "/broadcast &lt;bot_id&gt;\n"
            "/pin_broadcast &lt;bot_id&gt;\n"
            "/allbroadcast\n"
            "/retry &lt;broadcast_id&gt; — retry failed users\n"
            "/cancel &lt;broadcast_id&gt;\n\n"
            "<b>ꜱᴄʜᴇᴅᴜʟᴇʀ</b>\n"
            "/schedule &lt;bot_id|all&gt; &lt;YYYY-MM-DD&gt; &lt;HH:MM&gt;\n"
            "/schedules — list pending\n\n"
            "<b>ᴛᴇᴍᴘʟᴀᴛᴇꜱ</b>\n"
            "/savetemplate &lt;name&gt; — save replied msg as template\n"
            "/templates — list all templates\n\n"
            "<b>ɪᴍᴘᴏʀᴛ</b> (DM only)\n"
            "/import_mongo &lt;url&gt; &lt;db&gt; &lt;col&gt; &lt;bot_id&gt;\n\n"
            "<b>ᴇxᴛᴇʀɴᴀʟ ᴍᴏɴɢᴏ</b> (DM only)\n"
            "/connect_mongodb &lt;url&gt; [db]\n"
            "/mongo_collections [db]\n"
            "/mongo_clear_collection &lt;collection&gt; [db]\n"
            "/mongo_drop_collection &lt;collection&gt; [db]\n"
            "/mongo_clone_to &lt;target_url&gt; [source_db] [target_db]\n"
            "/mongo_disconnect\n\n"
            "<b>ʟᴏɢꜱ</b>\n"
            "/history"
        )
