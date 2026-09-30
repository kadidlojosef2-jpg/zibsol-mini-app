import os
from pathlib import Path
import aiosqlite

DB_PATH = os.getenv("DB_PATH", "data/zibsol.db")
REFERRAL_REWARD = 1000

async def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT UNIQUE NOT NULL, title TEXT NOT NULL, reward INTEGER NOT NULL DEFAULT 100, join_link TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS claims (user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, reward INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, channel_id));
        CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, target_members INTEGER NOT NULL, price_zibsol INTEGER NOT NULL, link TEXT NOT NULL, chat_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, wallet TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, reason TEXT NOT NULL, reference TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS referrals (referred_user_id INTEGER PRIMARY KEY, referrer_user_id INTEGER NOT NULL, reward INTEGER NOT NULL DEFAULT 1000, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)
        await db.commit()

async def user_exists(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        return await (await db.execute("SELECT 1 FROM users WHERE id=?", (user_id,))).fetchone() is not None

async def upsert_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO users(id,username,first_name) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name", (user["id"], user.get("username"), user.get("first_name")))
        await db.commit()

async def get_balance(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT balance FROM users WHERE id=?", (user_id,))).fetchone()
        return int(row[0]) if row else 0

async def list_channels(active_only=True):
    async with aiosqlite.connect(DB_PATH) as db:
        sql = "SELECT id,chat_id,title,reward,join_link,active FROM channels"
        if active_only: sql += " WHERE active=1"
        sql += " ORDER BY id"
        return await (await db.execute(sql)).fetchall()

async def add_channel(chat_id, title, reward, join_link):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO channels(chat_id,title,reward,join_link,active) VALUES(?,?,?,?,1) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,reward=excluded.reward,join_link=excluded.join_link,active=1", (chat_id,title,reward,join_link))
        await db.commit()
        return cur.lastrowid

async def deactivate_channel(channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("UPDATE channels SET active=0 WHERE id=?", (channel_id,))
        await db.commit()
        return cur.rowcount > 0

async def claim_channel(user_id, channel_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        if await (await db.execute("SELECT 1 FROM claims WHERE user_id=? AND channel_id=?", (user_id, channel_id))).fetchone(): await db.rollback(); return None
        row = await (await db.execute("SELECT reward FROM channels WHERE id=? AND active=1", (channel_id,))).fetchone()
        if not row: await db.rollback(); return None
        reward = int(row[0])
        await db.execute("INSERT INTO claims(user_id,channel_id,reward) VALUES(?,?,?)", (user_id,channel_id,reward))
        await db.execute("UPDATE users SET balance=balance+? WHERE id=?", (reward,user_id))
        await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES(?,?,?,?)", (user_id,reward,"channel_join",str(channel_id)))
        await db.commit(); return reward

async def create_campaign(user_id, target, price, link, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT balance FROM users WHERE id=?", (user_id,))).fetchone()
        if not row or row[0] < price: await db.rollback(); return None
        await db.execute("UPDATE users SET balance=balance-? WHERE id=?", (price,user_id))
        cur = await db.execute("INSERT INTO campaigns(user_id,target_members,price_zibsol,link,chat_id) VALUES(?,?,?,?,?)", (user_id,target,price,link,chat_id))
        campaign_id = cur.lastrowid
        await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES(?,?,?,?)", (user_id,-price,"promotion_purchase",str(campaign_id)))
        await db.commit(); return campaign_id

async def create_withdrawal(user_id, wallet):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT balance FROM users WHERE id=?", (user_id,))).fetchone()
        balance = int(row[0]) if row else 0
        if balance < 10000: await db.rollback(); return None
        await db.execute("UPDATE users SET balance=0 WHERE id=?", (user_id,))
        cur = await db.execute("INSERT INTO withdrawals(user_id,amount,wallet) VALUES(?,?,?)", (user_id,balance,wallet))
        withdrawal_id = cur.lastrowid
        await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES(?,?,?,?)", (user_id,-balance,"withdrawal_hold",str(withdrawal_id)))
        await db.commit(); return withdrawal_id

async def apply_referral(referrer_user_id, referred_user_id):
    if referrer_user_id == referred_user_id: return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        referrer = await (await db.execute("SELECT id FROM users WHERE id=?", (referrer_user_id,))).fetchone()
        if not referrer: await db.rollback(); return False
        exists = await (await db.execute("SELECT 1 FROM referrals WHERE referred_user_id=?", (referred_user_id,))).fetchone()
        if exists: await db.rollback(); return False
        await db.execute("INSERT INTO referrals(referred_user_id,referrer_user_id,reward) VALUES(?,?,?)", (referred_user_id,referrer_user_id,REFERRAL_REWARD))
        await db.execute("UPDATE users SET balance=balance+? WHERE id=?", (REFERRAL_REWARD,referrer_user_id))
        await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES(?,?,?,?)", (referrer_user_id,REFERRAL_REWARD,"referral",str(referred_user_id)))
        await db.commit(); return True

async def referral_count(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_user_id=?", (user_id,))).fetchone()
        return int(row[0])
