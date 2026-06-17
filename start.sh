#!/data/data/com.termux/files/usr/bin/sh
# Auto-restart bot + dashboard if either crashes
cd /data/data/com.termux/files/home/telegram-gdrive-bot

# Start the web dashboard in the background
python dashboard.py >> dashboard.log 2>&1 &
DASHBOARD_PID=$!
echo "$(date): Dashboard started (PID $DASHBOARD_PID)"

# Bot loop — restarts on crash
while true; do
    echo "$(date): Starting bot..."
    python bot.py >> nohup.out 2>&1
    echo "$(date): Bot stopped. Restarting in 5 seconds..."
    sleep 5
done
