import json
from decimal import Decimal
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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

@app.get("/api/me")
async def me(authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    balance = await db.get_balance(user["id"])
    return {"user": user, "balance": balance, "gram": str(Decimal(balance) / ZIBSOL_PER_GRAM)}

@app.get("/api/channels")
async def channels(authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    return [{"id":r[0],"title":r[1],"reward":r[2],"join_link":r[3]} for r in await db.list_channels()]

class ClaimBody(BaseModel):
    channel_id: int

@app.post("/api/channels/claim")
async def claim(body: ClaimBody, authorization: str | None = Header(default=None)):
    user = get_telegram_user(authorization)
    await db.upsert_user(user)
    # TODO before production: call Telegram Bot API getChatMember and verify membership.
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
