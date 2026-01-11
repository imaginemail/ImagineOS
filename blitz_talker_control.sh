#!/bin/bash
# blitz_talker_control.sh - Persistent Yad dashboard
# Updated: 2026-01-10

[[ -f .system_env ]] || { echo "ERROR: Missing .system_env"; exit 1; }
source blitz_talker_library.sh

ensure_single_instance

# First-run clean slate
if [ ! -f .setup_done ]; then
    rm -f .imagine_env
    touch .setup_done
fi

# Load values — system defaults first, user overrides last
source .system_env
source .user_env 2>/dev/null || true
source .imagine_env 2>/dev/null || true

# Enforce safe start
echo "MODE=safe" > .imagine_env

stop_daemon() {
    pkill -9 -f blitz_talker_daemon.sh 2>/dev/null
    echo "MODE=safe" > .imagine_env
}

save_prompt_to_user_env() {
    # Gentle update — only touch DEFAULT_PROMPT line, preserve everything else
    local temp=$(mktemp)
    cp .user_env "$temp" 2>/dev/null || touch "$temp"

    if grep -q '^DEFAULT_PROMPT=' "$temp"; then
        sed -i "s/^DEFAULT_PROMPT=.*/DEFAULT_PROMPT=\"$DEFAULT_PROMPT\"/" "$temp"
    else
        echo "DEFAULT_PROMPT=\"$DEFAULT_PROMPT\"" >> "$temp"
    fi

    mv "$temp" .user_env
}

stage_targets() {
    stop_daemon
    rm -f live_windows.txt "$TARGET_DIR"/temp_*.gxz 2>/dev/null

    if [[ "$WIPE_ON_STAGE" == "Yes" ]]; then
        pkill "$BROWSER" 2>/dev/null
        sleep 1
    fi

    local num="$STAGE_COUNT"

    local urls=("$DEFAULT_URL")

    mkdir -p "$TARGET_DIR"
    local browser_pids=()
    for i in $(seq 1 "$num"); do
        $BROWSER $BROWSER_FLAGS_HEAD $BROWSER_FLAGS_MIDDLE $BROWSER_FLAGS_TAIL"${urls[$(( (i-1) % ${#urls[@]} ))]}" &
        sleep 0.5   # give the last one a breath of air before we pile another one on
        browser_pids+=($!)
    done

    # Wait for the last 4 launched browsers to have windows with the pattern
    local num_to_wait=$(( num < 4 ? num : 4 ))
    local last_pids=("${browser_pids[@]: -$num_to_wait}")

    local attempts=0
    local ready=0
    until (( ready >= num_to_wait || attempts >= 120 )); do
        ready=$(xdotool search --onlyvisible --pid "${last_pids[@]}" --name "$WINDOW_PATTERN" 2>/dev/null | wc -l)
        sleep 0.1
        ((attempts++))
    done

    if (( ready < num_to_wait )); then
        echo "Warning: Only $ready of last $num_to_wait windows ready" >&2
    fi

    # Stabilization loop for extra safety
    local last_total=0
    local stable_start=0
    local stable_seconds=3

    while true; do
        local total=$(xdotool search --onlyvisible --name "$WINDOW_PATTERN" 2>/dev/null | wc -l)

        if (( total == last_total )); then
            [[ $stable_start == 0 ]] && stable_start=$(date +%s)
            if (( $(date +%s) - stable_start >= stable_seconds )); then
                break
            fi
        else
            stable_start=0
        fi

        last_total=$total
        sleep 0.1
    done

    local WINDOW_IDS=($(xdotool search --onlyvisible --name "$WINDOW_PATTERN" 2>/dev/null))

    local SCREEN_W SCREEN_H
    read SCREEN_W SCREEN_H < <(xdotool getdisplaygeometry)
    local margin=10
    local max_overlap_percent=${MAX_OVERLAP_PERCENT:-25}
    local window_width=$DEFAULT_WIDTH
    local window_height=$DEFAULT_HEIGHT
    local num_windows=${#WINDOW_IDS[@]}
    local available_w=$((SCREEN_W - 2 * margin))
    local available_h=$((SCREEN_H - 2 * margin))
    local min_shift=$(( window_width * (100 - max_overlap_percent) / 100 ))
    (( min_shift < 50 )) && min_shift=50
    local max_cols=1
    local accumulated=$window_width
    while (( accumulated + min_shift <= available_w )); do
        ((accumulated += min_shift))
        ((max_cols++))
    done
    local num_rows=$(( (num_windows + max_cols - 1) / max_cols ))
    local min_v_gap=10
    local v_step=$(( window_height + min_v_gap ))
    local total_grid_h=$(( window_height + (num_rows - 1) * v_step ))
    local grid_start_y=$(( margin + (available_h - total_grid_h) / 2 ))
    (( grid_start_y < margin )) && grid_start_y=$margin
    local step_x=0
    if (( max_cols > 1 )); then
        step_x=$(( (available_w - window_width) / (max_cols - 1) ))
    fi

    rm -f live_windows.txt
    local idx=0
    for (( row=0; row < num_rows; row++ )); do
        local remaining=$(( num_windows - idx ))
        local windows_this_row=$(( remaining < max_cols ? remaining : max_cols ))
        local total_row_span=$window_width
        if (( windows_this_row > 1 )); then
            total_row_span=$(( window_width + (windows_this_row - 1) * step_x ))
        fi
        local row_start_x=$(( margin + (available_w - total_row_span) / 2 ))
        local pos_y=$(( grid_start_y + row * v_step ))
        for (( col=0; col < windows_this_row; col++ )); do
            local id="${WINDOW_IDS[$((idx++))]}"
            local pos_x=$(( row_start_x + col * step_x ))
            xdotool windowsize "$id" "$window_width" "$window_height" 2>/dev/null
            xdotool windowmove "$id" "$pos_x" "$pos_y" 2>/dev/null
            echo "$id" >> live_windows.txt
        done
    done
}

# Persistent Yad panel
while true; do
    source .imagine_env 2>/dev/null || true

    local fire_auto_buttons=""
    if [[ "${MODE:-safe}" == "safe" && -s live_windows.txt ]]; then
        fire_auto_buttons="--button=FIRE:3 --button=AUTO:4"
    elif [[ "${MODE:-safe}" != "safe" ]]; then
        fire_auto_buttons="--button=STOP:5"
    fi

    read SCREEN_W SCREEN_H < <(xdotool getdisplaygeometry)
    calc_x=$(( SCREEN_W - PANEL_DEFAULT_WIDTH - PANEL_DEFAULT_X_OFFSET ))
    calc_y=$(( SCREEN_H - PANEL_DEFAULT_HEIGHT - PANEL_DEFAULT_Y_OFFSET ))
    (( calc_x < 0 )) && calc_x=0
    (( calc_y < 0 )) && calc_y=0
    use_geom="${PANEL_DEFAULT_WIDTH}x${PANEL_DEFAULT_HEIGHT}+$calc_x+$calc_y"

    yad_output=$(yad --form --width="$PANEL_DEFAULT_WIDTH" --height="$PANEL_DEFAULT_HEIGHT" \
        --geometry="$use_geom" --title="$PANEL_TITLE" --on-top \
        --field="URL":TXT "$DEFAULT_URL" \
        --field="Prompt":TXT "$DEFAULT_PROMPT" \
        --field="Shots per window (burst)":NUM "${BURST_COUNT:-4}!1..20!1" \
        --field="Number of windows":NUM "${STAGE_COUNT:-24}!1..48!1" \
        --field="Wipe windows on stage?":CB "No!Yes" \
        --button="STAGE TARGETS":2 \
        $fire_auto_buttons \
        --button="SAVE PROMPT":6 \
        --button="EXIT":0)

    ret=$?

    DEFAULT_URL=$(echo "$yad_output" | cut -d'|' -f1)
    DEFAULT_PROMPT=$(echo "$yad_output" | cut -d'|' -f2)
    BURST_COUNT=$(echo "$yad_output" | cut -d'|' -f3 | cut -d'.' -f1)
    STAGE_COUNT=$(echo "$yad_output" | cut -d'|' -f4 | cut -d'.' -f1)
    WIPE_ON_STAGE=$(echo "$yad_output" | cut -d'|' -f5)

    case $ret in
        0)  # EXIT
            stop_daemon
            if yad --question --title="$PANEL_TITLE" --text="Kill all browser windows on exit?" --button=Yes:0 --button=No:1; then
                pkill "$BROWSER" 2>/dev/null
            fi
            exit 0
            ;;
        2) stage_targets ;;
        3)  # FIRE (semi)
            echo "MODE=semi" > .imagine_env
            ./blitz_talker_daemon.sh &
            ;;
        4)  # AUTO
            echo "MODE=auto" > .imagine_env
            ./blitz_talker_daemon.sh &
            ;;
        5) stop_daemon ;;
        6)  # SAVE PROMPT
            save_prompt_to_user_env
            ;;
        *) stop_daemon; exit 0 ;;
    esac
done
