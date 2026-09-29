# ZIBSOL Telegram Mini App

Starter Telegram Mini App with FastAPI, SQLite, Telegram initData verification, channel rewards, promotion purchases and withdrawal requests.

## Important
This starter does not send blockchain transactions. Withdrawals are saved as pending requests until the exact ZIBSOL network/contract and secure signing process are configured.

Before production, channel rewards must verify membership server-side with Telegram `getChatMember`.

## Render
Deploy as a Docker Web Service. Set `BOT_TOKEN`, `ADMIN_IDS` and `APP_URL` as Render environment variables. Never commit `.env` or the bot token.