import asyncio
import json
import logging
import os
from urllib.parse import parse_qsl
from decimal import Decimal
from pathlib import Path
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .telegram_auth import validate_init_data
from . import db
BASE=Path(__file__).resolve().parent.parent; WEB=BASE/"web"; app=FastAPI(title="ZIBSOL Mini App API"); app.mount("/static",StaticFiles(directory=WEB),name="static")
ZIBSOL_PER_GRAM=10000; MIN_WITHDRAWAL=10000; STARS_PER_100_MEMBERS=10; _bot_task=None; _bot_username=None; log=logging.getLogger("zibsol")
def _bot_done(task):
 if not task.cancelled() and task.exception(): log.error("Telegram bot polling stopped: %r",task.exception())
@app.on_event("startup")
async def startup():
 global _bot_task
 await db.init_db()
 from .bot import run_bot
 _bot_task=asyncio.create_task(run_bot()); _bot_task.add_done_callback(_bot_done)
@app.on_event("shutdown")
async def shutdown():
 global _bot_task
 if _bot_task:
  _bot_task.cancel()
  try: await _bot_task
  except asyncio.CancelledError: pass
 await db.close_db()
@app.get("/")
async def home(): return FileResponse(WEB/"index.html")
@app.get("/tonconnect-manifest.json")
async def tonconnect_manifest(): return {"url":os.getenv("APP_URL","https://zibsol-mini-app.onrender.com"),"name":"ZIBSOL","iconUrl":os.getenv("TON_ICON_URL","https://ton.org/download/ton_symbol.png")}
@app.get("/solana")
async def solana_token_page():
 return FileResponse(BASE/"solana_site.html")

@app.get("/api/solana/market")
async def solana_market():
 token=os.getenv("ZIBSOL_SOLANA_CA","4TQECKRv74c8ggXpyG1aBEvSe3JsrYoMMBAddXr7pump").strip()
 if not token: raise HTTPException(503,"Solana token address is not configured")
 try:
  async with httpx.AsyncClient(timeout=12) as client:
   response=await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
   response.raise_for_status()
   payload=response.json()
 except Exception as exc:
  log.warning("Solana market data unavailable: %r",exc)
  raise HTTPException(502,"Live market data is temporarily unavailable")
 pairs=[p for p in (payload.get("pairs") or []) if p.get("chainId")=="solana" and (p.get("baseToken") or {}).get("address")==token]
 pairs.sort(key=lambda p:float((p.get("liquidity") or {}).get("usd") or 0),reverse=True)
 if not pairs:
  return {"token_address":token,"available":False,"message":"No Solana market pair was returned by the data provider yet."}
 p=pairs[0]
 return {"token_address":token,"available":True,"pair_address":p.get("pairAddress"),"dex_id":p.get("dexId"),"url":p.get("url"),"price_usd":p.get("priceUsd"),"price_change_24h":(p.get("priceChange") or {}).get("h24"),"volume_24h":(p.get("volume") or {}).get("h24"),"liquidity_usd":(p.get("liquidity") or {}).get("usd"),"fdv":p.get("fdv"),"market_cap":p.get("marketCap"),"symbol":(p.get("baseToken") or {}).get("symbol","ZIBSOL")}

@app.get("/health")
async def health(): return {"ok":True,"database":"postgresql"}
def get_telegram_user(authorization):
 if not authorization or not authorization.startswith("tma "): raise HTTPException(401,"Open this app inside Telegram")
 try: return json.loads(validate_init_data(authorization[4:])["user"])
 except Exception as exc: raise HTTPException(401,str(exc))
def get_start_param(authorization):
 try: return dict(parse_qsl(authorization[4:])).get("start_param")
 except Exception: return None
async def bot_username():
 global _bot_username
 if not _bot_username: _bot_username=(await telegram_api("getMe",{}))["username"]
 return _bot_username
def admin_ids(): return {int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}
def require_admin(authorization):
 user=get_telegram_user(authorization)
 if user["id"] not in admin_ids(): raise HTTPException(403,"Admin only")
 return user
async def telegram_api(method,payload):
 token=os.getenv("BOT_TOKEN")
 if not token: raise HTTPException(500,"BOT_TOKEN is not configured")
 async with httpx.AsyncClient(timeout=15) as client: response=await client.post(f"https://api.telegram.org/bot{token}/{method}",json=payload)
 data=response.json()
 if not data.get("ok"): raise HTTPException(400,f"Telegram {method}: {data.get('description','unknown error')}")
 return data["result"]
async def verify_bot_is_admin(chat_id):
 me=await telegram_api("getMe",{}); member=await telegram_api("getChatMember",{"chat_id":chat_id,"user_id":me["id"]})
 if member.get("status") not in {"administrator","creator"}: raise HTTPException(400,"The bot must be an administrator in this channel")
 return me,member
async def verify_user_membership(chat_id,user_id):
 member=await telegram_api("getChatMember",{"chat_id":chat_id,"user_id":user_id}); status=member.get("status")
 return status in {"creator","administrator","member"} or (status=="restricted" and member.get("is_member") is True)
@app.get("/api/me")
async def me(authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); was_new=not await db.user_exists(user["id"]); await db.upsert_user(user)
 sp=get_start_param(authorization)
 if was_new and sp and sp.startswith("ref_"):
  try: await db.apply_referral(int(sp[4:]),user["id"])
  except ValueError: pass
 balance=await db.get_balance(user["id"])
 return {"user":user,"balance":balance,"gram":str(Decimal(balance)/ZIBSOL_PER_GRAM),"is_admin":user["id"] in admin_ids(),"wallet":await db.get_wallet(user["id"])}
@app.get("/api/referral")
async def referral(authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); uname=await bot_username(); count=await db.referral_count(user["id"]); reward=db.REFERRAL_REWARD; short=os.getenv("MINIAPP_SHORT_NAME","").strip()
 link=f"https://t.me/{uname}/{short}?startapp=ref_{user['id']}" if short else f"https://t.me/{uname}?start=ref_{user['id']}"
 return {"count":count,"reward_per_ref":reward,"total_earned":count*reward,"link":link}
@app.post("/api/ads/reward")
async def ad_reward(authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); balance,remaining=await db.reward_ad(user["id"])
 if balance is None: raise HTTPException(429,f"Ad limit reached. Try again in {remaining} seconds" if remaining>=3600 else f"You can watch another rewarded ad in {remaining} seconds")
 return {"ok":True,"reward":db.AD_REWARD,"balance":balance,"cooldown":db.AD_COOLDOWN_SECONDS}
@app.get("/api/channels")
async def channels(authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); return [{"id":r["id"],"chat_id":r["chat_id"],"title":r["title"],"reward":r["reward"],"join_link":r["join_link"]} for r in await db.list_channels(True)]
class ClaimBody(BaseModel): channel_id:int
@app.post("/api/channels/claim")
async def claim(body:ClaimBody,authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); row=await db.get_channel(body.channel_id)
 if not row: raise HTTPException(404,"Channel not found")
 if not await verify_user_membership(row["chat_id"],user["id"]): raise HTTPException(400,"You have not joined this channel yet")
 reward=await db.claim_channel(user["id"],body.channel_id)
 if reward is None: raise HTTPException(400,"Already claimed or channel unavailable")
 return {"reward":reward,"balance":await db.get_balance(user["id"])}
class CampaignBody(BaseModel): target_members:int; link:str; chat_id:str
@app.get("/api/campaigns")
async def campaigns(authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); rows=await db.list_campaigns_for_user(user["id"])
 return [{"id":int(r["id"]),"target_members":int(r["target_members"]),"completed_members":int(r["completed_members"]),"reward_per_member":int(r["reward_per_member"]),"link":r["link"],"chat_id":r["chat_id"],"status":r["status"],"payment_method":r["payment_method"]} for r in rows]
@app.post("/api/campaigns")
async def campaign(body:CampaignBody,authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user)
 if body.target_members<100 or body.target_members%100: raise HTTPException(400,"Target must be a multiple of 100")
 if not body.link.startswith("https://t.me/"): raise HTTPException(400,"Telegram link required")
 await verify_bot_is_admin(body.chat_id.strip()); price=body.target_members*10; campaign_id=await db.create_campaign(user["id"],body.target_members,price,body.link,body.chat_id.strip())
 if campaign_id is None: raise HTTPException(400,"Insufficient balance")
 return {"campaign_id":campaign_id,"price_zibsol":price,"reward_per_member":db.PROMO_REWARD_PER_MEMBER}
class CampaignClaimBody(BaseModel): campaign_id:int
@app.post("/api/campaigns/claim")
async def campaign_claim(body:CampaignClaimBody,authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); rows=await db.list_campaigns_for_user(user["id"]); c=next((r for r in rows if int(r["id"])==body.campaign_id),None)
 if not c: raise HTTPException(404,"Campaign not found or already complete")
 if not await verify_user_membership(c["chat_id"],user["id"]): raise HTTPException(400,"Join the promoted channel first")
 reward,status=await db.claim_campaign(user["id"],body.campaign_id)
 if reward is None: raise HTTPException(400,status)
 return {"reward":reward,"status":status,"balance":await db.get_balance(user["id"])}
class StarCampaignBody(BaseModel): target_members:int; link:str; chat_id:str
@app.post("/api/campaigns/stars")
async def campaign_stars(body:StarCampaignBody,authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user)
 if body.target_members<100 or body.target_members%100: raise HTTPException(400,"Target must be a multiple of 100")
 if not body.link.startswith("https://t.me/"): raise HTTPException(400,"Telegram link required")
 await verify_bot_is_admin(body.chat_id.strip()); price_stars=(body.target_members//100)*STARS_PER_100_MEMBERS; order_id,payload=await db.create_star_order(user["id"],body.target_members,price_stars,body.link,body.chat_id.strip())
 invoice=await telegram_api("createInvoiceLink",{"title":f"ZIBSOL promo — {body.target_members} users","description":f"Telegram promotion for {body.target_members} users","payload":payload,"currency":"XTR","prices":[{"label":"Promotion","amount":price_stars}]})
 return {"order_id":order_id,"price_stars":price_stars,"invoice_link":invoice}
class WalletBody(BaseModel): wallet:str
@app.post("/api/wallet")
async def wallet(body:WalletBody,authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); await db.upsert_user(user); value=body.wallet.strip()
 if not value: raise HTTPException(400,"Wallet address is required")
 await db.set_wallet(user["id"],value); return {"ok":True,"wallet":value}
class WithdrawBody(BaseModel): wallet:str
@app.post("/api/withdraw")
async def withdraw(body:WithdrawBody,authorization:str|None=Header(default=None)):
 get_telegram_user(authorization)
 raise HTTPException(503,"Withdrawals are temporarily paused while the payout system is updated.")
class AddChannelBody(BaseModel): chat_id:str; join_link:str; reward:int=Field(default=100,ge=1,le=1000000000); title:str|None=None
@app.get("/api/admin/status")
async def admin_status(authorization:str|None=Header(default=None)):
 user=get_telegram_user(authorization); return {"is_admin":user["id"] in admin_ids()}
@app.get("/api/admin/channels")
async def admin_channels(authorization:str|None=Header(default=None)):
 require_admin(authorization); return [{"id":r["id"],"chat_id":r["chat_id"],"title":r["title"],"reward":r["reward"],"join_link":r["join_link"],"active":bool(r["active"])} for r in await db.list_channels(False)]
@app.get("/api/admin/campaigns")
async def admin_campaigns(authorization:str|None=Header(default=None)):
 require_admin(authorization); return [{"id":int(r["id"]),"user_id":int(r["user_id"]),"target_members":int(r["target_members"]),"completed_members":int(r["completed_members"]),"reward_per_member":int(r["reward_per_member"]),"link":r["link"],"chat_id":r["chat_id"],"status":r["status"],"payment_method":r["payment_method"]} for r in await db.list_campaigns()]
@app.post("/api/admin/channels")
async def add_channel(body:AddChannelBody,authorization:str|None=Header(default=None)):
 require_admin(authorization)
 if not body.join_link.startswith("https://t.me/"): raise HTTPException(400,"join_link must start with https://t.me/")
 chat=body.chat_id.strip(); await verify_bot_is_admin(chat); info=await telegram_api("getChat",{"chat_id":chat}); title=body.title.strip() if body.title else info.get("title") or info.get("username") or chat; channel_id=await db.add_channel(chat,title,body.reward,body.join_link); return {"ok":True,"id":channel_id,"title":title,"reward":body.reward}
class DisableChannelBody(BaseModel): channel_id:int
@app.post("/api/admin/channels/disable")
async def disable_channel(body:DisableChannelBody,authorization:str|None=Header(default=None)):
 require_admin(authorization)
 if not await db.deactivate_channel(body.channel_id): raise HTTPException(404,"Channel not found")
 return {"ok":True}
@app.get("/api/developer/stats")
async def developer_stats(authorization:str|None=Header(default=None)):
 require_admin(authorization); r=await db.developer_stats(); return {"users":int(r["users"]),"circulating":int(r["circulating"]),"pending_withdrawals":int(r["pending_withdrawals"]),"active_campaigns":int(r["active_campaigns"])}
@app.get("/api/developer/users")
async def developer_users(authorization:str|None=Header(default=None)):
 require_admin(authorization); return [{"id":int(r["id"]),"username":r["username"],"first_name":r["first_name"],"balance":int(r["balance"]),"wallet":r["wallet_address"],"referrals":int(r["referrals"])} for r in await db.developer_users()]
@app.get("/api/developer/withdrawals")
async def developer_withdrawals(authorization:str|None=Header(default=None)):
 require_admin(authorization); return [{"id":int(r["id"]),"user_id":int(r["user_id"]),"username":r["username"],"first_name":r["first_name"],"amount":int(r["amount"]),"gram":str(Decimal(r["amount"])/ZIBSOL_PER_GRAM),"wallet":r["wallet"],"status":r["status"],"created_at":r["created_at"].isoformat()} for r in await db.developer_withdrawals()]
class WithdrawalAction(BaseModel): withdrawal_id:int
@app.post("/api/developer/withdrawals/approve")
async def approve_withdrawal(body:WithdrawalAction,authorization:str|None=Header(default=None)):
 require_admin(authorization); r=await db.approve_withdrawal(body.withdrawal_id)
 if not r: raise HTTPException(404,"Pending withdrawal not found")
 return {"ok":True,"withdrawal_id":int(r["id"]),"amount":int(r["amount"]),"wallet":r["wallet"],"note":"Approved for manual GRAM payout"}
@app.post("/api/developer/withdrawals/reject")
async def reject_withdrawal(body:WithdrawalAction,authorization:str|None=Header(default=None)):
 require_admin(authorization); r=await db.reject_withdrawal(body.withdrawal_id)
 if not r: raise HTTPException(404,"Pending withdrawal not found")
 return {"ok":True,"refunded":int(r["amount"])}
async def notify_grant(user_id,amount,reason,balance):
 text=f"🎁 You received {amount:,} ZIBSOL!\n\n📝 Reason: {reason}\n💰 New balance: {balance:,} ZIBSOL"
 try:
  await telegram_api("sendMessage",{"chat_id":user_id,"text":text}); return True
 except Exception as exc:
  log.warning("Could not notify user %s about grant: %r",user_id,exc); return False
class GrantBody(BaseModel): user_id:int; amount:int=Field(gt=0,le=1000000000); reason:str=Field(min_length=1,max_length=200)
@app.post("/api/developer/grant")
async def grant(body:GrantBody,authorization:str|None=Header(default=None)):
 require_admin(authorization); balance=await db.grant_zibsol(body.user_id,body.amount,body.reason)
 if balance is None: raise HTTPException(404,"User not found")
 notified=await notify_grant(body.user_id,body.amount,body.reason.strip(),int(balance))
 return {"ok":True,"user_id":body.user_id,"added":body.amount,"balance":int(balance),"notified":notified}