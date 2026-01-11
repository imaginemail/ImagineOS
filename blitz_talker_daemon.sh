#!/bin/bash
# blitz_talker_daemon.sh - Automate firing on staged targets
# Updated: 2026-01-10

[[ -f .system_env ]] || { echo "ERROR: Missing .system_env"; exit 1; }
source blitz_talker_library.sh

ensure_single_instance

source .system_env
source .imagine_env 2>/dev/null || true
source .user_env 2>/dev/null || true

MODE="${MODE:-safe}"

[[ -s live_windows.txt ]] || { echo "No targets"; exit 1; }
mapfile -t WINDOW_IDS < live_windows.txt

set_panel_title() {
    local new_title="$1"
    wmctrl -r "$PANEL_TITLE" -T "$new_title" 2>/dev/null || true
}

set_panel_title "$PANEL_TITLE - READY"

round=0
total_shots=0
set_panel_title "Blitz Talker - STARTING"

while true; do
    source .imagine_env 2>/dev/null || true

    if [[ "$MODE" == "safe" ]]; then
        set_panel_title "Blitz Talker - PAUSED"
        while [[ "$MODE" == "safe" ]]; do
            sleep 1
            source .imagine_env 2>/dev/null || true
        done
        set_panel_title "Blitz Talker - RESUMING"
    fi

    ((round++))
    set_panel_title "Blitz Talker - Round $round Shots $total_shots FIRING"

    for i in "${!WINDOW_IDS[@]}"; do
        id=${WINDOW_IDS[i]}

        eval "$(xdotool getwindowgeometry --shell "$id" 2>/dev/null)"
        width=$WIDTH
        height=$HEIGHT
        calc_prompt_click

        xdotool windowactivate --sync "$id"
        eval $(xdotool getmouselocation --shell)
        saved_x=$X
        saved_y=$Y

        xdotool mousemove --window "$id" "$CLICK_X" "$CLICK_Y"
        for k in {1..3}; do
            xdotool click 4
        done

        echo -n "$DEFAULT_PROMPT" | (xclip -selection clipboard 2>/dev/null || wl-copy 2>/dev/null || true)

        xdotool mousemove --window "$id" "$CLICK_X" "$CLICK_Y"
        xdotool click 1

        for (( j=0; j < $BURST_COUNT; j++ )); do
            xdotool key --window "$id" ctrl+a ctrl+v Return
            sleep "$SHOT_DELAY"
        done

        xdotool mousemove $saved_x $saved_y

        ((total_shots += $BURST_COUNT))

        set_panel_title "Blitz Talker - Round $round Shots $total_shots FIRING"

        screencap "shot_${round}_$((i+1))_$(date +%s)"
    done

    if [[ "$MODE" == "semi" ]]; then
        set_panel_title "Blitz Talker - COMPLETE (semi)"
        set_panel_title "$PANEL_TITLE - READY"
        echo "MODE=safe" > .imagine_env
        exit 0
    fi

    # Auto mode cap
    if [[ "$MODE" == "auto" && "$AUTO_ROUNDS" -gt 0 && round >= "$AUTO_ROUNDS" ]]; then
        set_panel_title "Blitz Talker - COMPLETE (auto)"
        set_panel_title "$PANEL_TITLE - READY"
        echo "MODE=safe" > .imagine_env
        exit 0
    fi

    sleep "$ROUND_DELAY"
done
