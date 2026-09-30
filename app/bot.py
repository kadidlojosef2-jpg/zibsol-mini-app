import os
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from . import db

router = Router()

@router.message(CommandStart())
async def start(message: Message, command: CommandObject):
    user = message.from_user
    if not user:
        return
    data = {"id": user.id, "username": user.username, "first_name": user.first_name}
    await db.upsert_user(data)

    rewarded = False
    if command.args and command.args.startswith("ref_"):
        try:
            referrer_id = int(command.args[4:])
            rewarded = await db.apply_referral(referrer_id, user.id)
        except (ValueError, TypeError):
            pass

    app_url = os.getenv("APP_URL", "").rstrip("/")
    if not app_url:
        await message.answer("ZIBSOL Mini App is not configured yet.")
        return

    text = "🎉 Welcome to ZIBSOL!\n\nEarn ZIBSOL by joining channels and inviting friends."
    if rewarded:
        text += "\n\n✅ Your referral was registered. The inviter received 1,000 ZIBSOL."

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Open ZIBSOL APP", web_app=WebAppInfo(url=app_url))
    ]])
    await message.answer(text, reply_markup=keyboard)

@router.message(F.text == "/referrals")
async def referrals(message: Message):
    if not message.from_user:
        return
    count = await db.referral_count(message.from_user.id)
    await message.answer(f"👥 Referrals: {count}\n💰 Reward: {count * db.REFERRAL_REWARD:,} ZIBSOL")

async def run_bot():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
