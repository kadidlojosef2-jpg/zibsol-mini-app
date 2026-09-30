import json
import os
from decimal import Decimal
from pathlib import Path
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .telegram_auth import validate_init_data
from . import db

BASE = Path(__file__).resolve().parent.parent
WEB = BASE / "web"
app = FastAPI(title="ZIBSOL Mini App API")
app.mount("/static", StaticFiles(directory=WEB), name="static")
ZIBSOL_PER_GRAM = 10000
MIN_WITHDRAWAL = 10000

@app.on_event("startup")
async def startup():
    await db.init_db()

@app.get("/")
async def home():
    return FileResponse(WEB / "index.html")

@app.get("/health")
async def health():
    return {"ok": True}

def get_telegram_user(authorization):
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(401, "Open this app inside Telegram")
    try:
        pairs = validate_init_data(authorization[4:])
        return json.loads(pairs["user"])
    except Exception as exc:
        raise HTTPException(401, str(exc))

def admin_ids():
    return {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

def require_admin(authorization):
    user = get_telegram_user(authorization)
    if user["id"] not in admin_ids():
        raise HTTPException(403, "Admin only")
    return user

async def telegram_api(method, payload):
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise HTTPException(500, "BOT_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)
    data = response.json()
    if not data.get("ok"):
        raise HTTPException(400, f"Telegram {method}: {data.get('description','unknown error')}")
    return data["result"]

async def verify_bot_is_admin(chat_id):
    me = await telegram_api("getMe", {})
    member = await telegram_api("getChatMember", {"chat_id": chat_id, "user_id": me["id"]})
    if member.get("status") not in {"administrator", "creator"}:
        raise HTTPException(400, "The bot must be an administrator in this channel")
    return me, member

async def verify_user_membership(chat_id, user_id):
    member = await telegram_api("getChatMember", {"chat_id": chat_id, "user_id": user_id})
    status = member.get("status")
    if status in {"creator", "administrator", "member"}:
        return True
    if status == "restricted" and member.get("is_member") is True:
        return True
    return False

@app.get("/api/me")
async def me(authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    balance = await db.get_balance(user["id"])
    return {"user": user, "balance": balance, "gram": str(Decimal(balance) / ZIBSOL_PER_GRAM), "is_admin": user["id"] in admin_ids()}

@app.get("/api/channels")
async def channels(authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    return [{"id":r[0],"chat_id":r[1],"title":r[2],"reward":r[3],"join_link":r[4]} for r in await db.list_channels(True)]

class ClaimBody(BaseModel):
    channel_id: int

@app.post("/api/channels/claim")
async def claim(body: ClaimBody, authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    async with __import__('aiosqlite').connect(db.DB_PATH) as conn:
        row = await (await conn.execute("SELECT chat_id FROM channels WHERE id=? AND active=1", (body.channel_id,))).fetchone()
    if not row:
        raise HTTPException(404, "Channel not found")
    try:
        is_member = await verify_user_membership(row[0], user["id"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Could not verify membership: {exc}")
    if not is_member:
        raise HTTPException(400, "You have not joined this channel yet")
    reward = await db.claim_channel(user["id"], body.channel_id)
    if reward is None:
        raise HTTPException(400, "Already claimed or channel unavailable")
    return {"reward": reward, "balance": await db.get_balance(user["id"])}

class CampaignBody(BaseModel):
    target_members: int
    link: str
    chat_id: str

@app.post("/api/campaigns")
async def campaign(body: CampaignBody, authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    if body.target_members < 100 or body.target_members % 100:
        raise HTTPException(400, "Target must be a multiple of 100")
    if not body.link.startswith("https://t.me/"):
        raise HTTPException(400, "Telegram link required")
    price = body.target_members * 10
    campaign_id = await db.create_campaign(user["id"], body.target_members, price, body.link, body.chat_id)
    if campaign_id is None:
        raise HTTPException(400, "Insufficient balance")
    return {"campaign_id": campaign_id, "price_zibsol": price}

class WithdrawBody(BaseModel):
    wallet: str

@app.post("/api/withdraw")
async def withdraw(body: WithdrawBody, authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    withdrawal_id = await db.create_withdrawal(user["id"], body.wallet)
    if withdrawal_id is None:
        raise HTTPException(400, f"Minimum withdrawal is {MIN_WITHDRAWAL:,} ZIBSOL")
    return {"withdrawal_id": withdrawal_id}

class AddChannelBody(BaseModel):
    chat_id: str
    join_link: str
    reward: int = Field(default=100, ge=1, le=1000000000)
    title: str | None = None

@app.get("/api/admin/status")
async def admin_status(authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    return {"is_admin": user["id"] in admin_ids()}

@app.get("/api/admin/channels")
async def admin_channels(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    return [{"id":r[0],"chat_id":r[1],"title":r[2],"reward":r[3],"join_link":r[4],"active":bool(r[5])} for r in await db.list_channels(False)]

@app.post("/api/admin/channels")
async def add_channel(body: AddChannelBody, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if not body.join_link.startswith("https://t.me/"):
        raise HTTPException(400, "join_link must start with https://t.me/")
    chat = body.chat_id.strip()
    await verify_bot_is_admin(chat)
    info = await telegram_api("getChat", {"chat_id": chat})
    title = body.title.strip() if body.title else info.get("title") or info.get("username") or chat
    channel_id = await db.add_channel(chat, title, body.reward, body.join_link)
    return {"ok": True, "id": channel_id, "title": title, "reward": body.reward}

class DisableChannelBody(BaseModel):
    channel_id: int

@app.post("/api/admin/channels/disable")
async def disable_channel(body: DisableChannelBody, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if not await db.deactivate_channel(body.channel_id):
        raise HTTPException(404, "Channel not found")
    return {"ok": True}
