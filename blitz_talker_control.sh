#!/bin/bash
# blitz_talker_control.sh - Dual YAD: Persistent display + respawning control panel
# Updated: 2026-01-14

[[ -f .system_env ]] || { echo "ERROR: Missing .system_env"; exit 1; }
source blitz_talker_library.sh

ensure_single_instance

# First-run clean slate
if [ ! -f .setup_done ]; then
    rm -f .imagine_env
    touch .setup_done
    touch .imagine_env
fi

load_environment

# Enforce safe start.
update_key_value .imagine_env MODE safe

PIPE="/tmp/blitz_display_pipe_$$"
mkfifo "$PIPE"

refresh_display() {
	load_environment

    targets=$(xdotool search --onlyvisible --name "$WINDOW_PATTERN" 2>/dev/null | wc -l)
    daemon=$(pgrep -f blitz_talker_daemon.sh >/dev/null && echo "Running" || echo "Stopped")
    mode=${MODE:-safe}
    prompt=${DEFAULT_PROMPT:-system is king}
    burst=${BURST_COUNT:-4}
    urls=${DEFAULT_URL:-none}

    cat <<EOF
<big>$DISPLAY_TITLE</big>

Mode:       $mode
Targets:    $targets
Daemon:     $daemon
Burst:      $burst
Prompt:     $prompt
URLs:       $urls

$(date)
EOF
}

# Persistent display (initial content + live updates)
{
    refresh_display
    cat
} > "$PIPE" &
yad --text-info --listen --title="$DISPLAY_TITLE" \
    --geometry=$(auto_position_panel DISPLAY) \
    --no-buttons --always-on-top --margins=10 --fontname="Monospace 10" \
    < "$PIPE" &
DISPLAY_PID=$!

stage_targets() {
    safemode
    get_urls
    local num="${STAGE_COUNT:-24}"
    if (( ${#urls[@]} == 0 )); then
        yad --error --title="Stage Targets" --text="No URLs provided." --button=OK:0
        return
    fi
    local browser_pids=()
    for i in $(seq 1 "$num"); do
        $BROWSER $BROWSER_FLAGS_HEAD $BROWSER_FLAGS_MIDDLE $BROWSER_FLAGS_TAIL"${urls[$(( (i-1) % ${#urls[@]} ))]}" &
        sleep 1.5
        browser_pids+=($!)
    done
    mapfile -t WINDOW_IDS < <(for pid in "${browser_pids[@]}"; do xdotool search --onlyvisible --pid "$pid" 2>/dev/null; done | sort -u)
    (( ${#WINDOW_IDS[@]} == 0 )) && { echo "No windows found"; exit 1; }
    for window in "${WINDOW_IDS[@]}"; do
        xdotool windowactivate "$window"
        sleep 0.5
    done
    write_to_gxz
    local last_total=0
    local stable_start=0
    local stable_seconds=3

    while true; do
        local total=$(for pid in "${browser_pids[@]}"; do xdotool search --onlyvisible --pid "$pid" 2>/dev/null; done | wc -l)

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
    mapfile -t WINDOW_IDS < <(for pid in "${browser_pids[@]}"; do xdotool search --onlyvisible --pid "$pid" 2>/dev/null; done | sort -u)
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
    source .user_env 2>/dev/null || true

    targets=$(xdotool search --onlyvisible --name "$WINDOW_PATTERN" 2>/dev/null | wc -l)

    buttons=(--button="Edit Prompt":10 --button="Edit URLs":11 --button="Quit":1)

    (( targets == 0 )) && buttons+=(--button="Stage Targets":20) || buttons+=(--button="Safe Mode":30 --button="Start Auto":40)

    yad --form --title="$CONTROL_TITLE" \
        --geometry=$(auto_position_panel CONTROL) \
        --always-on-top --text="<big>Control Panel</big>" \
        "${buttons[@]}" >/dev/null

    ret=$?

    case $ret in
        1)  kill $DISPLAY_PID 2>/dev/null; rm -f "$PIPE"; exit 0 ;;
        10) new=$(yad --entry --title="Edit Prompt" --text="New prompt:" --entry-text="${DEFAULT_PROMPT:-}")
            [[ -n "$new" ]] && update_key_value .user_env DEFAULT_PROMPT "$new" ;;
        11) url_val=$(grep '^DEFAULT_URL=' .user_env 2>/dev/null | cut -d'"' -f2 || echo "")
            content="$url_val"
            [[ -f "$url_val" ]] && content=$(cat "$url_val")
            new=$(yad --text-info --editable --width=600 --height=400 --title="Edit URLs" --text="$content")
            [[ -n "$new" ]] && { [[ -f "$url_val" ]] && printf "%s\n" "$new" > "$url_val" || update_key_value .user_env DEFAULT_URL "$new"; } ;;
        20) stage_targets ;;
        30) safemode ;;
        40) update_key_value .imagine_env MODE auto; ./blitz_talker_daemon.sh & ;;
        *)  continue ;;
    esac

    refresh_display > "$PIPE"
    sleep 0.1
done
