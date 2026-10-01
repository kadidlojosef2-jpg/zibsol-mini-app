import os
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, PreCheckoutQuery
from . import db

router = Router()

@router.message(CommandStart())
async def start(message: Message, command: CommandObject):
    user = message.from_user
    if not user:
        return

    data = {"id": user.id, "username": user.username, "first_name": user.first_name}
    was_new_user = not await db.user_exists(user.id)
    rewarded = False

    if command.args and command.args.startswith("ref_") and was_new_user:
        try:
            referrer_id = int(command.args[4:])
            rewarded = await db.apply_referral(referrer_id, user.id)
        except (ValueError, TypeError):
            pass

    await db.upsert_user(data)

    app_url = os.getenv("APP_URL", "").rstrip("/")
    if not app_url:
        await message.answer("ZIBSOL Mini App is not configured yet.")
        return

    text = "🎉 Welcome to ZIBSOL!\n\nEarn ZIBSOL by joining channels and inviting friends."
    if rewarded:
        text += "\n\n✅ Referral registered. The inviter received 1,000 ZIBSOL."
    elif command.args and command.args.startswith("ref_") and not was_new_user:
        text += "\n\nℹ️ This Telegram account was already registered, so no new referral reward was created."

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

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    order = await db.get_star_order_by_payload(query.invoice_payload)
    if not order or order["status"] != "pending":
        await query.answer(ok=False, error_message="This promotion order is no longer available.")
        return
    if int(order["user_id"]) != int(query.from_user.id) or query.currency != "XTR":
        await query.answer(ok=False, error_message="Payment does not match this order.")
        return
    if int(order["price_stars"]) != int(query.total_amount):
        await query.answer(ok=False, error_message="The invoice amount does not match the order.")
        return
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message):
    if not message.from_user or not message.successful_payment:
        return
    payment = message.successful_payment
    campaign_id = await db.complete_star_order(
        payment.invoice_payload,
        message.from_user.id,
        payment.telegram_payment_charge_id,
    )
    if campaign_id:
        await message.answer(
            f"✅ Payment received: {payment.total_amount} Telegram Stars.\n"
            f"📣 Promotion #{campaign_id} is now queued for processing."
        )

async def run_bot():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
