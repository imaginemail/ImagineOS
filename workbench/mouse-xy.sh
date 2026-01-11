#!/bin/bash
# click_debugger.sh - Live mouse/window monitor with left-click capture

debug_file="click_debug.txt"

echo "Mouse/window debugger running."
echo "Left click anywhere to capture current window data to $debug_file"
echo "Data appends — last capture is at the bottom."
echo "Ctrl+C to stop."

# Background listener for left click (button 1)
( xinput test-xi2 --root 2>/dev/null | grep --line-buffered "ButtonPress detail:1" ) &
listener_pid=$!

trap "kill $listener_pid 2>/dev/null; echo; echo 'Stopped.'; exit" INT TERM

while true; do
  eval "$(xdotool getmouselocation --shell 2>/dev/null)"
  [ -z "$WINDOW" ] && {
    clear
    echo "No window under mouse"
    #sleep 0.2
    continue
  }

  eval "$(xdotool getwindowgeometry --shell "$WINDOW" 2>/dev/null)" || continue

  rx=$((X - X))  # wait, X is mouse absolute X, window X is window pos X
  ry=$((Y - Y))

  from_top=$ry
  from_bottom=$((HEIGHT - ry))
  from_left=$rx
  from_right=$((WIDTH - rx))

  clear
  echo "=== LIVE MONITOR ==="
  echo "Window ID: $WINDOW"
  echo "From top: $from_top px"
  echo "From bottom: $from_bottom px"
  echo "From left: $from_left px"
  echo "From right: $from_right px"
  echo
  echo "xdotool:"
  echo "  Name: $(xdotool getwindowname "$WINDOW" 2>/dev/null)"
  echo "  Geometry: ${WIDTH}x${HEIGHT}+${X}+${Y}"
  echo
  echo "xprop:"
  echo "  WM_NAME: $(xprop -id "$WINDOW" WM_NAME 2>/dev/null | sed -n 's/^WM_NAME(STRING) = "\(.*\)"/\1/p')"
  echo "  WM_CLASS: $(xprop -id "$WINDOW" WM_CLASS 2>/dev/null | sed -n 's/^WM_CLASS(STRING) = "\(.*\)", "\(.*\)"/Instance: \1  Class: \2/p')"
  echo "  PID: $(xprop -id "$WINDOW" _NET_WM_PID 2>/dev/null | awk '{print $3}')"
  echo "=== END LIVE ==="
  echo "Left click to capture"

  # Check if left click happened
  if read -t 0.1 line 2>/dev/null; then
    clear
    echo "=== CLICK CAPTURED at $(date) ==="
    echo "Window ID: $WINDOW"
    echo "From top: $from_top px"
    echo "From bottom: $from_bottom px"
    echo "From left: $from_left px"
    echo "From right: $from_right px"
    echo
    echo "xdotool:"
    echo "  Name: $(xdotool getwindowname "$WINDOW" 2>/dev/null)"
    echo "  Geometry: ${WIDTH}x${HEIGHT}+${X}+${Y}"
    echo
    echo "xprop:"
    echo "  WM_NAME: $(xprop -id "$WINDOW" WM_NAME 2>/dev/null | sed -n 's/^WM_NAME(STRING) = "\(.*\)"/\1/p')"
    echo "  WM_CLASS: $(xprop -id "$WINDOW" WM_CLASS 2>/dev/null | sed -n 's/^WM_CLASS(STRING) = "\(.*\)", "\(.*\)"/Instance: \1  Class: \2/p')"
    echo "  PID: $(xprop -id "$WINDOW" _NET_WM_PID 2>/dev/null | awk '{print $3}')"
    echo "=== END CAPTURE ==="

    # Append to file
    {
      echo "=== CLICK CAPTURED at $(date) ==="
      echo "Window ID: $WINDOW"
      echo "From top: $from_top px"
      echo "From bottom: $from_bottom px"
      echo "From left: $from_left px"
      echo "From right: $from_right px"
      echo
      echo "xdotool:"
      echo "  Name: $(xdotool getwindowname "$WINDOW" 2>/dev/null)"
      echo "  Geometry: ${WIDTH}x${HEIGHT}+${X}+${Y}"
      echo
      echo "xprop:"
      echo "  WM_NAME: $(xprop -id "$WINDOW" WM_NAME 2>/dev/null | sed -n 's/^WM_NAME(STRING) = "\(.*\)"/\1/p')"
      echo "  WM_CLASS: $(xprop -id "$WINDOW" WM_CLASS 2>/dev/null | sed -n 's/^WM_CLASS(STRING) = "\(.*\)", "\(.*\)"/Instance: \1  Class: \2/p')"
      echo "  PID: $(xprop -id "$WINDOW" _NET_WM_PID 2>/dev/null | awk '{print $3}')"
      echo "=== END CAPTURE ==="
      echo
    } >> "$debug_file"

    # Restart listener
    ( xinput test-xi2 --root 2>/dev/null | grep --line-buffered "ButtonPress detail:1" ) &
    listener_pid=$!
  fi

  sleep 0.1
done
