# ZIBSOL Channel Generator

Creates Telegram broadcast channels sequentially with random names, attempts to assign a random public username, attempts to invite the ZIBSOL bot, and records each channel in PostgreSQL.

## Safety

Run this from your own computer with your own Telegram user account. The program creates channels one at a time and uses a configurable delay. If Telegram returns a FloodWait, it stops instead of trying to bypass the limit.

Telegram API credentials and the phone number stay local in `.env` and must never be committed.

## 1. Get Telegram API credentials

Create your API ID and API hash at `https://my.telegram.org` under API development tools. Do not send the API hash to anyone.

## 2. Install

```powershell
cd channel_generator
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell blocks activation, you can skip activation and run:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Configure

Copy `.env.example` to `.env` and fill in:

- `API_ID`
- `API_HASH`
- `PHONE`
- `BOT_USERNAME`
- `DATABASE_URL`
- `CHANNEL_COUNT`
- `DELAY_MIN` / `DELAY_MAX`

Never commit `.env`.

## 4. Run

```powershell
python main.py
```

On the first run Telethon asks for the Telegram login code and, if enabled, your 2FA password. A local `channel_generator_session.session` file is then created. Treat that file like a credential and never upload it to GitHub.

The generator records every created channel in `channel_generator_log`. It also tries to import it into the application's `channels` table when that table exposes the expected fields. If your current production schema differs, the generator keeps the channel in the log rather than guessing at the production schema.

The generator cannot guarantee that Telegram will allow a bot to be invited or made administrator automatically. If the invite fails, add the bot as an administrator manually before users are asked to verify membership.
