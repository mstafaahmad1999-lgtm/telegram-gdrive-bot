#!/data/data/com.termux/files/usr/bin/bash
# Start the Telegram bot AND the Flask dashboard together, with live logs.
# Usage:  bash run.sh
# Stop:   Ctrl+C   (kills both cleanly)

set -e
cd "$(dirname "$0")"

# Kill any previous instances so we never hit the "two bots" Conflict error.
pkill -9 -f bot.py 2>/dev/null || true
pkill -9 -f dashboard.py 2>/dev/null || true
sleep 5   # let Telegram release the old getUpdates session

mkdir -p logs

python bot.py       > logs/bot.log       2>&1 &
BOT_PID=$!
python dashboard.py > logs/dashboard.log 2>&1 &
DASH_PID=$!

echo "──────────────────────────────────────────────"
echo "  Bot       PID $BOT_PID   → logs/bot.log"
echo "  Dashboard PID $DASH_PID  → logs/dashboard.log"
echo "  Press Ctrl+C to stop both."
echo "──────────────────────────────────────────────"

trap 'echo; echo "Stopping..."; kill $BOT_PID $DASH_PID $TAIL_PID 2>/dev/null; wait 2>/dev/null; exit 0' INT TERM

tail -n 0 -F logs/bot.log logs/dashboard.log &
TAIL_PID=$!

wait -n $BOT_PID $DASH_PID
echo "One process exited — stopping the other..."
kill $BOT_PID $DASH_PID $TAIL_PID 2>/dev/null || true
wait 2>/dev/null || true
