import os
import uuid
import asyncpg

REFERRAL_REWARD = 1000
PROMO_REWARD_PER_MEMBER = int(os.getenv("PROMO_REWARD_PER_MEMBER", "100"))
AD_REWARD = 50
AD_COOLDOWN_SECONDS = 10
DATABASE_URL = os.environ.get("DATABASE_URL")
_pool = None

async def init_db():
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with _pool.acquire() as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, balance BIGINT NOT NULL DEFAULT 0, wallet_address TEXT);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_address TEXT;
        CREATE TABLE IF NOT EXISTS channels (id BIGSERIAL PRIMARY KEY, chat_id TEXT UNIQUE NOT NULL, title TEXT NOT NULL, reward BIGINT NOT NULL DEFAULT 100, join_link TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE);
        CREATE TABLE IF NOT EXISTS claims (user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, channel_id BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE, reward BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (user_id, channel_id));
        CREATE TABLE IF NOT EXISTS campaigns (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id), target_members INTEGER NOT NULL, price_zibsol BIGINT NOT NULL DEFAULT 0, link TEXT NOT NULL, chat_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', payment_method TEXT NOT NULL DEFAULT 'zibsol', price_stars BIGINT NOT NULL DEFAULT 0, stars_charge_id TEXT, completed_members INTEGER NOT NULL DEFAULT 0, reward_per_member BIGINT NOT NULL DEFAULT 100, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS payment_method TEXT NOT NULL DEFAULT 'zibsol';
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS price_stars BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS stars_charge_id TEXT;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS completed_members INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS reward_per_member BIGINT NOT NULL DEFAULT 100;
        ALTER TABLE campaigns ALTER COLUMN reward_per_member SET DEFAULT 100;
        UPDATE campaigns SET reward_per_member=100 WHERE status='pending';
        CREATE TABLE IF NOT EXISTS campaign_participants (campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, reward BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (campaign_id, user_id));
        CREATE TABLE IF NOT EXISTS star_orders (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id), target_members INTEGER NOT NULL, price_stars BIGINT NOT NULL, link TEXT NOT NULL, chat_id TEXT NOT NULL, payload TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'pending', campaign_id BIGINT REFERENCES campaigns(id), telegram_charge_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), paid_at TIMESTAMPTZ);
        CREATE TABLE IF NOT EXISTS withdrawals (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id), amount BIGINT NOT NULL, wallet TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
        CREATE TABLE IF NOT EXISTS ledger (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id), amount BIGINT NOT NULL, reason TEXT NOT NULL, reference TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
        CREATE TABLE IF NOT EXISTS referrals (referred_user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, referrer_user_id BIGINT NOT NULL REFERENCES users(id), reward BIGINT NOT NULL DEFAULT 1000, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
        CREATE TABLE IF NOT EXISTS ad_reward_claims (user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, last_claimed_at TIMESTAMPTZ NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_id);
        CREATE INDEX IF NOT EXISTS idx_star_orders_payload ON star_orders(payload);
        CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
        CREATE INDEX IF NOT EXISTS idx_campaign_participants_user ON campaign_participants(user_id);
        CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status);
        ''')

async def close_db():
    global _pool
    if _pool:
        await _pool.close(); _pool = None

def pool():
    if _pool is None: raise RuntimeError("Database pool is not initialized")
    return _pool

async def user_exists(user_id):
    async with pool().acquire() as db: return await db.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE id=$1)", user_id)

async def upsert_user(user):
    async with pool().acquire() as db:
        await db.execute("INSERT INTO users(id,username,first_name) VALUES($1,$2,$3) ON CONFLICT(id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name", user["id"], user.get("username"), user.get("first_name"))

async def get_balance(user_id):
    async with pool().acquire() as db:
        row=await db.fetchrow("SELECT balance FROM users WHERE id=$1",user_id); return int(row["balance"]) if row else 0
async def get_wallet(user_id):
    async with pool().acquire() as db: return await db.fetchval("SELECT wallet_address FROM users WHERE id=$1",user_id)
async def set_wallet(user_id,wallet_address):
    async with pool().acquire() as db: await db.execute("UPDATE users SET wallet_address=$1 WHERE id=$2",wallet_address,user_id)

async def reward_ad(user_id):
    async with pool().acquire() as db:
        async with db.transaction():
            row=await db.fetchrow("SELECT last_claimed_at FROM ad_reward_claims WHERE user_id=$1 FOR UPDATE", user_id)
            if row:
                seconds=await db.fetchval("SELECT EXTRACT(EPOCH FROM (NOW()-$1::timestamptz))", row["last_claimed_at"])
                if float(seconds) < AD_COOLDOWN_SECONDS:
                    remaining=max(1, int(AD_COOLDOWN_SECONDS-float(seconds)))
                    return None, remaining
            await db.execute("INSERT INTO ad_reward_claims(user_id,last_claimed_at) VALUES($1,NOW()) ON CONFLICT(user_id) DO UPDATE SET last_claimed_at=NOW()", user_id)
            await db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2", AD_REWARD, user_id)
            await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)", user_id, AD_REWARD, "ad_reward", "monetag:11933619")
            balance=await db.fetchval("SELECT balance FROM users WHERE id=$1", user_id)
            return int(balance), 0

async def list_channels(active_only=True):
    async with pool().acquire() as db:
        sql="SELECT id,chat_id,title,reward,join_link,active FROM channels"; sql += " WHERE active=TRUE" if active_only else ""; return await db.fetch(sql+" ORDER BY id")
async def add_channel(chat_id,title,reward,join_link):
    async with pool().acquire() as db:
        row=await db.fetchrow("INSERT INTO channels(chat_id,title,reward,join_link,active) VALUES($1,$2,$3,$4,TRUE) ON CONFLICT(chat_id) DO UPDATE SET title=EXCLUDED.title,reward=EXCLUDED.reward,join_link=EXCLUDED.join_link,active=TRUE RETURNING id",chat_id,title,reward,join_link); return int(row["id"])
async def deactivate_channel(channel_id):
    async with pool().acquire() as db: return (await db.execute("UPDATE channels SET active=FALSE WHERE id=$1",channel_id)).endswith("1")
async def get_channel(channel_id):
    async with pool().acquire() as db: return await db.fetchrow("SELECT chat_id FROM channels WHERE id=$1 AND active=TRUE",channel_id)

async def claim_channel(user_id,channel_id):
    async with pool().acquire() as db:
        async with db.transaction():
            if await db.fetchval("SELECT EXISTS(SELECT 1 FROM claims WHERE user_id=$1 AND channel_id=$2)",user_id,channel_id): return None
            row=await db.fetchrow("SELECT reward FROM channels WHERE id=$1 AND active=TRUE",channel_id)
            if not row:return None
            reward=int(row["reward"]); await db.execute("INSERT INTO claims(user_id,channel_id,reward) VALUES($1,$2,$3)",user_id,channel_id,reward); await db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2",reward,user_id); await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",user_id,reward,"channel_join",str(channel_id)); return reward

async def create_campaign(user_id,target,price,link,chat_id):
    async with pool().acquire() as db:
        async with db.transaction():
            balance=await db.fetchval("SELECT balance FROM users WHERE id=$1 FOR UPDATE",user_id)
            if balance is None or balance<price:return None
            await db.execute("UPDATE users SET balance=balance-$1 WHERE id=$2",price,user_id)
            row=await db.fetchrow("INSERT INTO campaigns(user_id,target_members,price_zibsol,link,chat_id,status,payment_method,reward_per_member) VALUES($1,$2,$3,$4,$5,'pending','zibsol',$6) RETURNING id",user_id,target,price,link,chat_id,PROMO_REWARD_PER_MEMBER); cid=int(row["id"]); await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",user_id,-price,"promotion_purchase",str(cid)); return cid
async def create_star_order(user_id,target,price_stars,link,chat_id):
    async with pool().acquire() as db:
        payload=f"zibsolpromo:{uuid.uuid4().hex}"; row=await db.fetchrow("INSERT INTO star_orders(user_id,target_members,price_stars,link,chat_id,payload) VALUES($1,$2,$3,$4,$5,$6) RETURNING id",user_id,target,price_stars,link,chat_id,payload); return int(row["id"]),payload
async def get_star_order_by_payload(payload):
    async with pool().acquire() as db:return await db.fetchrow("SELECT * FROM star_orders WHERE payload=$1",payload)
async def complete_star_order(payload,telegram_user_id,charge_id):
    async with pool().acquire() as db:
        async with db.transaction():
            order=await db.fetchrow("SELECT * FROM star_orders WHERE payload=$1 FOR UPDATE",payload)
            if not order or int(order["user_id"])!=int(telegram_user_id):return None
            if order["status"]=="paid":return int(order["campaign_id"]) if order["campaign_id"] else None
            c=await db.fetchrow("INSERT INTO campaigns(user_id,target_members,price_zibsol,link,chat_id,status,payment_method,price_stars,stars_charge_id,reward_per_member) VALUES($1,$2,0,$3,$4,'pending','stars',$5,$6,$7) RETURNING id",order["user_id"],order["target_members"],order["link"],order["chat_id"],order["price_stars"],charge_id,PROMO_REWARD_PER_MEMBER); cid=int(c["id"]); await db.execute("UPDATE star_orders SET status='paid',campaign_id=$1,telegram_charge_id=$2,paid_at=NOW() WHERE id=$3",cid,charge_id,order["id"]); return cid
async def list_campaigns_for_user(user_id):
    async with pool().acquire() as db:return await db.fetch("SELECT id,target_members,completed_members,reward_per_member,link,chat_id,status,payment_method FROM campaigns WHERE status='pending' AND completed_members<target_members AND user_id<>$1 ORDER BY id DESC LIMIT 50",user_id)
async def list_campaigns():
    async with pool().acquire() as db:return await db.fetch("SELECT id,user_id,target_members,completed_members,reward_per_member,link,chat_id,status,payment_method,created_at FROM campaigns ORDER BY id DESC LIMIT 100")
async def claim_campaign(user_id,campaign_id):
    async with pool().acquire() as db:
        async with db.transaction():
            campaign=await db.fetchrow("SELECT * FROM campaigns WHERE id=$1 FOR UPDATE",campaign_id)
            if not campaign or campaign["status"]!="pending" or int(campaign["completed_members"])>=int(campaign["target_members"]):return None,"Campaign is not available"
            if int(campaign["user_id"])==int(user_id):return None,"You cannot join your own promotion"
            if await db.fetchval("SELECT EXISTS(SELECT 1 FROM campaign_participants WHERE campaign_id=$1 AND user_id=$2)",campaign_id,user_id):return None,"You already completed this promotion"
            reward=int(campaign["reward_per_member"]); await db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2",reward,user_id); await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",user_id,reward,"campaign_join",str(campaign_id)); await db.execute("INSERT INTO campaign_participants(campaign_id,user_id,reward) VALUES($1,$2,$3)",campaign_id,user_id,reward); completed=int(campaign["completed_members"])+1; status="completed" if completed>=int(campaign["target_members"]) else "pending"; await db.execute("UPDATE campaigns SET completed_members=$1,status=$2 WHERE id=$3",completed,status,campaign_id); return reward,status

async def create_withdrawal(user_id,wallet):
    async with pool().acquire() as db:
        async with db.transaction():
            balance=int(await db.fetchval("SELECT balance FROM users WHERE id=$1 FOR UPDATE",user_id) or 0)
            if balance<10000:return None
            await db.execute("UPDATE users SET balance=0,wallet_address=$1 WHERE id=$2",wallet,user_id); row=await db.fetchrow("INSERT INTO withdrawals(user_id,amount,wallet) VALUES($1,$2,$3) RETURNING id",user_id,balance,wallet); wid=int(row["id"]); await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",user_id,-balance,"withdrawal_hold",str(wid)); return wid
async def apply_referral(referrer_user_id,referred_user_id):
    if referrer_user_id==referred_user_id:return False
    async with pool().acquire() as db:
        async with db.transaction():
            if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE id=$1)",referrer_user_id):return False
            if await db.fetchval("SELECT EXISTS(SELECT 1 FROM referrals WHERE referred_user_id=$1)",referred_user_id):return False
            await db.execute("INSERT INTO referrals(referred_user_id,referrer_user_id,reward) VALUES($1,$2,$3)",referred_user_id,referrer_user_id,REFERRAL_REWARD); await db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2",REFERRAL_REWARD,referrer_user_id); await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",referrer_user_id,REFERRAL_REWARD,"referral",str(referred_user_id)); return True
async def referral_count(user_id):
    async with pool().acquire() as db:return int(await db.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_user_id=$1",user_id))

async def developer_users(limit=500):
    async with pool().acquire() as db:
        return await db.fetch("SELECT u.id,u.username,u.first_name,u.balance,u.wallet_address,COALESCE(r.cnt,0) referrals FROM users u LEFT JOIN (SELECT referrer_user_id,COUNT(*) cnt FROM referrals GROUP BY referrer_user_id) r ON r.referrer_user_id=u.id ORDER BY u.balance DESC,u.id LIMIT $1",limit)
async def developer_stats():
    async with pool().acquire() as db:
        return await db.fetchrow("SELECT (SELECT COUNT(*) FROM users) users,(SELECT COALESCE(SUM(balance),0) FROM users) circulating,(SELECT COUNT(*) FROM withdrawals WHERE status='pending') pending_withdrawals,(SELECT COUNT(*) FROM campaigns WHERE status='pending') active_campaigns")
async def developer_withdrawals():
    async with pool().acquire() as db:
        return await db.fetch("SELECT w.id,w.user_id,u.username,u.first_name,w.amount,w.wallet,w.status,w.created_at FROM withdrawals w JOIN users u ON u.id=w.user_id ORDER BY CASE WHEN w.status='pending' THEN 0 ELSE 1 END,w.id DESC LIMIT 200")
async def approve_withdrawal(withdrawal_id):
    async with pool().acquire() as db:
        return await db.fetchrow("UPDATE withdrawals SET status='approved' WHERE id=$1 AND status='pending' RETURNING id,amount,wallet",withdrawal_id)
async def reject_withdrawal(withdrawal_id):
    async with pool().acquire() as db:
        async with db.transaction():
            row=await db.fetchrow("UPDATE withdrawals SET status='rejected' WHERE id=$1 AND status='pending' RETURNING id,user_id,amount",withdrawal_id)
            if not row:return None
            await db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2",int(row["amount"]),int(row["user_id"]))
            await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",int(row["user_id"]),int(row["amount"]),"withdrawal_refund",str(withdrawal_id))
            return row
async def grant_zibsol(user_id,amount,reason):
    async with pool().acquire() as db:
        async with db.transaction():
            if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM users WHERE id=$1)",user_id):return None
            await db.execute("UPDATE users SET balance=balance+$1 WHERE id=$2",amount,user_id)
            await db.execute("INSERT INTO ledger(user_id,amount,reason,reference) VALUES($1,$2,$3,$4)",user_id,amount,"admin_grant",reason)
            return await db.fetchval("SELECT balance FROM users WHERE id=$1",user_id)
