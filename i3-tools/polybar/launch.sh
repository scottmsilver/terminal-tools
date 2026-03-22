#!/bin/bash
# Launch polybar for i3wm.
# Called by i3 config: exec_always --no-startup-id ~/.config/polybar/launch.sh

command -v polybar >/dev/null || { echo "polybar not found" >&2; exit 1; }

# Kill existing instances and wait
killall -q polybar
while pgrep -u "$UID" -x polybar >/dev/null; do sleep 0.2; done

# Launch
polybar main 2>&1 | tee -a /tmp/polybar.log &
disown
