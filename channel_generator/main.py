import asyncio
import os
import random
import re

import asyncpg
from dotenv import load_dotenv
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, UsernameOccupiedError, UsernameInvalidError

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
PHONE = os.environ["PHONE"]
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lstrip("@")
DATABASE_URL = os.environ["DATABASE_URL"]
COUNT = int(os.getenv("CHANNEL_COUNT", "10"))
DELAY_MIN = int(os.getenv("DELAY_MIN", "20"))
DELAY_MAX = int(os.getenv("DELAY_MAX", "45"))

WORDS_A = [
    "Nova", "Daily", "Urban", "Digital", "Future", "Market", "Crypto", "World",
    "Tech", "Global", "Bright", "Prime", "Modern", "Alpha", "Signal", "Social",
]
WORDS_B = [
    "Pulse", "Orbit", "Horizon", "Radar", "Wave", "Beacon", "Vertex", "Scope",
    "Stream", "Hub", "Wire", "Brief", "Network", "Focus", "Trend", "Circle",
]


def make_title() -> str:
    return f"{random.choice(WORDS_A)} {random.choice(WORDS_B)}"


def make_username(title: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]", "", title).lower()
    return f"{base}{random.randint(1000, 999999)}"


async def ensure_tables(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_generator_log (
            id BIGSERIAL PRIMARY KEY,
            channel_id BIGINT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            username TEXT,
            invite_link TEXT,
            reward_zibsol BIGINT NOT NULL DEFAULT 100,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            bot_added BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)


async def save_channel(conn, channel, username, invite_link, bot_added):
    # Telegram channel IDs are integers in MTProto, while the ZIBSOL
    # application stores chat_id as TEXT. Convert explicitly.
    chat_id = str(channel.id)

    await conn.execute("""
        INSERT INTO channel_generator_log
            (channel_id, title, username, invite_link, reward_zibsol, bot_added)
        VALUES ($1, $2, $3, $4, 100, $5)
        ON CONFLICT (channel_id) DO UPDATE SET
            title = EXCLUDED.title,
            username = EXCLUDED.username,
            invite_link = EXCLUDED.invite_link,
            bot_added = EXCLUDED.bot_added
    """, channel.id, channel.title, username, invite_link, bot_added)

    # The application schema is:
    # channels(chat_id TEXT UNIQUE, title TEXT, reward BIGINT,
    #          join_link TEXT NOT NULL, active BOOLEAN)
    # Import only when a usable join URL exists.
    if not invite_link:
        print("  ! No usable join link; saved only in channel_generator_log.")
        return

    try:
        await conn.execute("""
            INSERT INTO channels (chat_id, title, reward, join_link, active)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (chat_id) DO UPDATE SET
                title = EXCLUDED.title,
                reward = EXCLUDED.reward,
                join_link = EXCLUDED.join_link,
                active = TRUE
        """, chat_id, channel.title, 100, invite_link)
        print("  -> added to application channels table (100 ZIBSOL)")
    except Exception as exc:
        print(f"  ! Could not auto-import into channels table: {exc}")
        print("    Channel is still recorded in channel_generator_log.")


async def add_bot_as_admin(client, channel):
    """Promote the configured bot directly; do not use InviteToChannelRequest.

    Telegram explicitly rejects inviting bots as ordinary channel members.
    A user account that owns the channel can promote the bot with channels.editAdmin.
    """
    if not BOT_USERNAME:
        print("  ! BOT_USERNAME is empty; skipping bot admin setup.")
        return False

    try:
        bot = await client.get_input_entity(BOT_USERNAME)

        # Minimal useful channel-admin rights. The important part for ZIBSOL
        # is that the bot is an administrator so Bot API getChatMember works.
        rights = types.ChatAdminRights(
            post_messages=True,
            edit_messages=True,
            delete_messages=True,
            invite_users=True,
        )

        await client(functions.channels.EditAdminRequest(
            channel=channel,
            user_id=bot,
            admin_rights=rights,
            rank="ZIBSOL",
        ))
        print(f"  -> @{BOT_USERNAME} promoted to channel administrator")
        return True
    except FloodWaitError:
        raise
    except Exception as exc:
        print(f"  ! Bot admin setup failed: {exc}")
        print("    Add the ZIBSOL bot manually as a channel administrator if required.")
        return False


async def create_one(client, conn, index):
    title = make_title()
    username = make_username(title)

    result = await client(functions.channels.CreateChannelRequest(
        title=title,
        about="Community channel",
        broadcast=True,
        megagroup=False,
    ))
    channel = result.chats[0]

    actual_username = None
    invite_link = None
    try:
        await client(functions.channels.UpdateUsernameRequest(
            channel=channel,
            username=username,
        ))
        actual_username = username
        invite_link = f"https://t.me/{username}"
    except (UsernameOccupiedError, UsernameInvalidError) as exc:
        print(f"  ! Public username unavailable: {exc}")
        try:
            invite = await client(functions.messages.ExportChatInviteRequest(peer=channel))
            invite_link = invite.link
        except Exception as exc2:
            print(f"  ! Could not create invite link: {exc2}")

    bot_added = await add_bot_as_admin(client, channel)
    await save_channel(conn, channel, actual_username, invite_link, bot_added)
    print(f"[{index}] {title} | id={channel.id} | @{actual_username or '-'}")


async def main():
    print("ZIBSOL Channel Generator")
    print(f"Requested channels: {COUNT}")
    print("Names are random and do not contain 'ZIBSOL'.")
    print("Press Ctrl+C to stop safely.\n")

    conn = await asyncpg.connect(DATABASE_URL)
    await ensure_tables(conn)

    client = TelegramClient("channel_generator_session", API_ID, API_HASH)
    await client.start(phone=PHONE)

    try:
        for index in range(1, COUNT + 1):
            try:
                await create_one(client, conn, index)
            except FloodWaitError as exc:
                print(f"Telegram requested a {exc.seconds}s wait. Stopping safely.")
                break
            except Exception as exc:
                print(f"[{index}] ERROR: {exc}")

            if index < COUNT:
                delay = random.randint(DELAY_MIN, DELAY_MAX)
                print(f"  waiting {delay}s before next channel...\n")
                await asyncio.sleep(delay)
    finally:
        await client.disconnect()
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
