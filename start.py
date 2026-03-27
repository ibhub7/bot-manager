"""
handlers/start.py — /start handler for each child bot
"""
from pyrogram import Client, filters
from pyrogram.types import Message
from database import users as users_db


def register_start_handler(client: Client, bot_id: int):
    """Attach handlers to a specific bot client."""

    @client.on_message(filters.command("start") & filters.private)
    async def _start(_, msg: Message):
        user   = msg.from_user
        is_new = await users_db.add_user(
            user_id=user.id,
            bot_id=bot_id,
            first_name=user.first_name or "",
            username=user.username or "",
        )
        if is_new:
            print(f"[bot:{bot_id}] New user: {user.id}")

        await msg.reply(
            f"👋 Hello **{user.first_name}**!\nᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴛʜᴇ ʙᴏᴛ.",
            quote=True,
        )

    @client.on_message(filters.private & ~filters.service, group=-1)
    async def _track(_, msg: Message):
        user = msg.from_user
        if user and not user.is_bot:
            await users_db.add_user(
                user_id=user.id,
                bot_id=bot_id,
                first_name=user.first_name or "",
                username=user.username or "",
            )
