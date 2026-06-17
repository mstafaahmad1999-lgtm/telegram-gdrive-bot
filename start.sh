#!/data/data/com.termux/files/usr/bin/sh
cd /data/data/com.termux/files/home/telegram-gdrive-bot

# Load env vars
. ./.env 2>/dev/null || true

# Start local Telegram Bot API server (if enabled)
if [ "$LOCAL_BOT_API" = "true" ]; then
    echo "$(date): Starting local Bot API server (2 GB limit enabled)..."
    telegram-bot-api \
        --api-id="$API_ID" \
        --api-hash="$API_HASH" \
        --local \
        --http-port="${LOCAL_BOT_API_PORT:-8081}" \
        --log=/dev/null >> tgbotapi.log 2>&1 &
    sleep 3
    echo "$(date): Local Bot API started"
fi

# Start Cloudflare tunnel
cloudflared tunnel run sovan-bot >> tunnel.log 2>&1 &
echo "$(date): Tunnel started"

# Start dashboard
python dashboard.py >> dashboard.log 2>&1 &
echo "$(date): Dashboard started"

# Bot loop — restarts on crash
while true; do
    echo "$(date): Starting bot..."
    python bot.py >> nohup.out 2>&1
    echo "$(date): Bot stopped. Restarting in 5 seconds..."
    sleep 5
done
