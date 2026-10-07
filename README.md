# 🤖 AIBOT — Telegram Management Bot & Userbot Forwarding Engine

High-performance Telegram Automation System featuring a separated **Telegram Management Bot (`./bot/*`)** and **Userbot Forwarding Engine (`./userbot/*`)**, auto-channel join with 1000s pending request retry, and automated `@beem` bot verification.

---

## 🐳 Docker Deployment

You can build and run either service individually or both via Docker Compose:

### Individual Docker Runs

Run Bot only:

```bash
docker run --rm --env-file .env aibot:latest python ./bot/bot.py
```

Run Userbot only:

```bash
docker run --rm --env-file .env aibot:latest python ./userbot/main.py
```

### Full Stack via Docker Compose

Launches PostgreSQL, Redis, Bot, and Userbot:

```bash
docker compose up -d --build
```

To view logs:

```bash
docker compose logs -f bot
docker compose logs -f userbot
```

---

## 🛡️ Auto-Join & @beem Bot Verification Flow

### 1. Auto-Join with 1000s Request Retry

- When forwarding or interacting with channels requiring membership, the userbot automatically sends a join request (`JoinChannelRequest` or `ImportChatInviteRequest`).
- If a channel requires admin/bot approval (`InviteRequestSentError`), the request is recorded in PostgreSQL (`channel_joins` table) with status `'pending'` and timestamp.
- **1000s Resend Rule**: If a join request remains pending after **1000 seconds**, the userbot automatically resends the join request to the channel.
- Once approved/joined, the status is updated to `'joined'` in the database and logged.

### 2. Automated `@beem` Bot Verification

When joining channels protected by `@beem` (such as FLIPS Rainbet), `@beem` sends:

```text
Welcome to FLIPS (Rainbet.com)! Members-only — join the channels below to unlock:
▱▱▱▱▱▱▱▱▱▱ 0 / 2
• @ogusers
• @avoid
Tap Verify when done — approval is instant.
```

The Userbot Engine automatically:

1. **Detects** the verification message in real time (via Telethon event listeners and dialog scans).
2. **Extracts** the required channels (e.g. `@ogusers` and `@avoid`).
3. **Auto-joins** both required channels via Telethon.
4. **Locates and clicks** the **Verify** inline button on `@beem`'s message.
5. **Verifies approval confirmation**:
   ```text
   You're in! All required channels joined — you've been approved. Welcome to FLIPS (Rainbet.com), glad to have you here.
   ```
6. Stores verification success in the database (`channel_joins.status = 'verified'`) and allows forwarding to continue seamlessly.

---

## ⚙️ Configuration (.env)

Ensure your `.env` contains:

```ini
POSTGRES_DB=example_db
POSTGRES_USER=example_db_user
POSTGRES_PASSWORD=your_secure_password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

REDIS_HOST=127.0.0.1
REDIS_PORT=6379

TELEGRAM_BOT_TOKEN=123456789:ABCDefGhIjKlMnOpQrStUvWxYz
ADMIN_ID=123456789
ENCRYPTION_KEY=your_fernet_key_here
```
