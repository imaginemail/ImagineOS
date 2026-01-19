#!/bin/bash
# blitz_talker_daemon.sh - Automate firing on staged targets
# Updated: 2026-01-17

[[ -f .system_env ]] || { echo "ERROR: Missing .system_env"; exit 1; }

source blitz_talker_library.sh
load_environment

[[ -s live_windows.txt ]] || { echo "No targets"; exit 1; }

bring_panel_front() {
    wmctrl -a "$PANEL_DEFAULT_TITLE" 2>/dev/null || true
}

set_panel_title() {
    local new_title="$1"
    wmctrl -r "$PANEL_DEFAULT_TITLE" -T "$new_title" 2>/dev/null || true
    bring_panel_front
}

total_shots=0

fire_count="${FIRE_COUNT:-1}"

for (( round=1; round <= fire_count; round++ )); do

    # Reload runtime changes
    source .imagine_env 2>/dev/null || true
    source .user_env 2>/dev/null || true

    set_panel_title "$PANEL_DEFAULT_TITLE - Round $round Shots $total_shots"

    # Refresh in case windows changed
    mapfile -t WINDOW_IDS < live_windows.txt

    for i in "${!WINDOW_IDS[@]}"; do
        id="${WINDOW_IDS[$i]}"

        eval "$(xdotool getwindowgeometry --shell "$id" 2>/dev/null)" || continue
        width=$WIDTH
        height=$HEIGHT
        calc_prompt_click

        xdotool windowactivate --sync "$id" 2>/dev/null

        eval "$(xdotool getmouselocation --shell)" 2>/dev/null
        saved_x=$X
        saved_y=$Y

        xdotool mousemove --window "$id" "$CLICK_X" "$CLICK_Y" 2>/dev/null
        for k in {1..3}; do
            xdotool click 4 2>/dev/null
        done

        echo -n "$DEFAULT_PROMPT" | (xclip -selection clipboard 2>/dev/null || wl-copy 2>/dev/null || true)

        xdotool mousemove --window "$id" "$CLICK_X" "$CLICK_Y" 2>/dev/null
        xdotool click 1 2>/dev/null

        for (( j=0; j < BURST_COUNT; j++ )); do
            xdotool key --window "$id" ctrl+a ctrl+v Return 2>/dev/null
            sleep "$SHOT_DELAY"
        done

        xdotool mousemove "$saved_x" "$saved_y" 2>/dev/null

        ((total_shots += BURST_COUNT))

        #screencap "shot_${round}_$((i+1))_$(date +%s)"
    done

    # Append prompt once per round
    write_to_gxz

    #sleep "$ROUND_DELAY"
    sleep 5
done

# done,tell commander
set_panel_title "$PANEL_DEFAULT_TITLE - COMPLETE"
update_key_value .imagine_env FIRE_MODE "N"
exit 0
