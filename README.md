# Telegram → Google Drive Upload Bot

A single-user Telegram bot that lets you send files, photos, or videos and upload them directly to any folder in your Google Drive — no browser needed.

---

## Features

- Send any file, photo, video, or audio to the bot
- Navigate your Drive folder tree with an inline keyboard
- Upload directly into the chosen folder
- Pagination for folders with many subfolders
- Shows upload progress for large files
- Confirms upload with file name, size, and a direct Drive link

---

## Prerequisites

- Python 3.11 or newer
- A Telegram account
- A Google account with Google Drive

---

## Step 1 — Create a Telegram Bot

1. Open Telegram and start a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to choose a name and username.
3. Copy the **bot token** you receive (looks like `123456789:ABCdef...`).

---

## Step 2 — Enable Google Drive API and Download Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Navigate to **APIs & Services → Library**, search for **Google Drive API**, and click **Enable**.
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
5. Choose **Desktop app** as the application type. Give it any name.
6. Click **Download JSON** and save the file as `credentials.json` in the project root (`telegram-gdrive-bot/`).
7. Go to **APIs & Services → OAuth consent screen** and add your Google account as a **Test user** (required while the app is in "Testing" mode).

---

## Step 3 — Install Dependencies

```bash
cd telegram-gdrive-bot
pip install -r requirements.txt
```

---

## Step 4 — Configure Environment Variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=<your bot token from BotFather>
AUTHORIZED_USER_ID=<your numeric Telegram user ID — see Step 5>
GOOGLE_CREDENTIALS_PATH=credentials.json
GOOGLE_TOKEN_PATH=token.json
```

---

## Step 5 — Find Your Telegram User ID

Run the bot once with a placeholder `AUTHORIZED_USER_ID` (e.g. `0`), then send `/whoami` to the bot. It will reply with your numeric user ID. Update `AUTHORIZED_USER_ID` in `.env` with that number, then restart the bot.

Alternatively, use [@userinfobot](https://t.me/userinfobot) on Telegram — send it any message and it will reply with your user ID.

---

## Step 6 — Authenticate with Google Drive (one time)

```bash
python setup_google_auth.py
```

A browser window will open asking you to sign in to Google and grant the bot access to your Drive. After you approve, a `token.json` file is saved locally. You won't need to do this again unless you revoke access.

---

## Step 7 — Run the Bot

```bash
python bot.py
```

The bot will start polling for messages. Send it a file to get started!

---

## Bot Commands

| Command    | Description                              |
|------------|------------------------------------------|
| `/start`   | Show welcome message and usage guide     |
| `/cancel`  | Cancel the current pending upload        |
| `/whoami`  | Show your Telegram user ID               |

---

## Folder Navigation

When you send a file, the bot replies with an inline keyboard showing your Drive folders:

- **✅ Upload here** — upload the file into the currently displayed folder
- **📁 FolderName** — navigate into that subfolder
- **⬅️ Back** — go up one level
- **◀️ Prev / Next ▶️** — paginate through folders (shown when there are more than 8)

---

## File Size Limits

The standard Telegram Bot API allows bots to download files up to **20 MB**. For larger videos or files, you have two options:

1. **Self-hosted Bot API server** — run Telegram's open-source [Bot API server](https://github.com/tdlib/telegram-bot-api) locally. It supports files up to 2 GB. Set `BOT_API_SERVER=http://localhost:8081` in your `.env` (the bot will use it automatically via `python-telegram-bot`'s `local_mode`).
2. **Google Drive direct upload** — for very large files, upload via the Drive web interface or mobile app.

---

## Running with Docker (optional)

Create a `Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

Build and run:

```bash
docker build -t gdrive-bot .
docker run -d \
  --name gdrive-bot \
  --env-file .env \
  -v $(pwd)/token.json:/app/token.json \
  -v $(pwd)/credentials.json:/app/credentials.json \
  gdrive-bot
```

> **Note:** Run `setup_google_auth.py` locally before using Docker, since OAuth requires a browser. Mount the resulting `token.json` into the container.

---

## Security Notes

- Only one Telegram user (set by `AUTHORIZED_USER_ID`) can interact with the bot.
- `credentials.json` and `token.json` are listed in `.gitignore` — never commit them.
- The bot does not store your files permanently; temp files are deleted after a successful upload.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Drive auth expired" message | Run `python setup_google_auth.py` again |
| Bot doesn't respond | Check that `TELEGRAM_BOT_TOKEN` is correct and the bot is running |
| "Not authorized" reply | Your `AUTHORIZED_USER_ID` doesn't match — use `/whoami` to verify |
| OAuth consent screen error | Make sure your Google account is added as a Test user in Cloud Console |
