#!/data/data/com.termux/files/usr/bin/sh
# Auto-restart bot if it crashes
cd /data/data/com.termux/files/home/telegram-gdrive-bot
while true; do
    echo "$(date): Starting bot..."
    python bot.py >> nohup.out 2>&1
    echo "$(date): Bot stopped. Restarting in 5 seconds..."
    sleep 5
done
