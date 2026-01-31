#!/bin/bash
# blitz_talker_control.sh - Single panel control (normalized yad handling)
# Patched: 2026-01-17 (updated normalized yad handling)
# - Centralized yad output handling
# - Two-stage parse/update flow for all buttons
# - Easy to add new buttons

[[ -f .system_env ]] || { echo "ERROR: Missing .system_env"; exit 1; }
source blitz_talker_library.sh
ensure_single_instance

# First-run initialization and single allowed safemode
if [ ! -f .setup_done ]; then
    rm -f .imagine_env
    touch .imagine_env
    update_key_value .imagine_env FIRE_MODE "N"
    echo "DEBUG: initial safemode at first-run"
    safemode
    touch .setup_done
fi

# Preserve runtime env between panel redraws; only create if missing
[[ -f .imagine_env ]] || { touch .imagine_env; update_key_value .imagine_env FIRE_MODE "N"; }

load_environment
trap 'echo "DEBUG: trapped signal, exiting"; exit' INT TERM

firemode() {
    echo "DEBUG: entering firemode (daemon start)" >&2
    update_key_value .imagine_env FIRE_MODE "Y"
    ./blitz_talker_daemon.sh &
    local pid=$!
    echo "$pid" > .daemon_pid 2>/dev/null || true
    echo "DEBUG: daemon started pid=$pid" >&2
}

wait_for_daemon_and_cleanup() {
    if [[ -f .daemon_pid ]]; then
        read -r daemon_pid < .daemon_pid
        if kill -0 "$daemon_pid" 2>/dev/null; then
            echo "DEBUG: waiting for daemon pid=$daemon_pid to finish" >&2
            wait "$daemon_pid" || true
            echo "DEBUG: daemon pid=$daemon_pid finished; invoking safemode" >&2
            update_key_value .imagine_env FIRE_MODE "N"
            safemode
            rm -f .daemon_pid
        else
            rm -f .daemon_pid 2>/dev/null || true
        fi
    fi
}

stage_targets() {
    echo "DEBUG: stage_targets starting: STAGE_COUNT=${STAGE_COUNT:-unset} BURST_COUNT=${BURST_COUNT:-unset}" >&2
    get_urls
    local num="${STAGE_COUNT:-1}"
    for i in $(seq 1 "$num"); do
        local target_url="${urls[$(( (i-1) % ${#urls[@]} ))]}"
        $BROWSER $BROWSER_FLAGS_HEAD $BROWSER_FLAGS_MIDDLE $BROWSER_FLAGS_TAIL"${urls[$(( (i-1) % ${#urls[@]} ))]}" &
        sleep 1.5
    done
    timeout_seconds=15
    start_time=$(date +%s)
    local targets_found=0
    local WINDOW_IDS=()
    until (( $(date +%s) - start_time >= timeout_seconds )); do
        mapfile -t WINDOW_IDS < <(xdotool search --onlyvisible --name "$WINDOW_PATTERN" 2>/dev/null | sort -u)
        (( ${#WINDOW_IDS[@]} > targets_found )) && targets_found=${#WINDOW_IDS[@]}
        sleep 0.3
    done
    echo "Targets acquired: $targets_found/$num" >&2
    if (( targets_found == 0 )); then
        echo "No targets found — aborting stage" >&2
        return 1
    fi
    write_to_gxz
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
    (( max_cols > 4 )) && max_cols=4
    local num_rows=$(( (num_windows + max_cols - 1) / max_cols ))
    local min_v_gap=10
    local v_step=$(( window_height + min_v_gap ))
    local total_grid_h=$(( window_height + (num_rows - 1) * v_step ))
    local grid_start_y=$(( margin + (available_h - total_grid_h) / 2 ))
    (( grid_start_y < margin )) && grid_start_y=$margin
    local step_x=0
    (( max_cols > 1 )) && step_x=$(( (available_w - window_width) / (max_cols - 1) ))
    rm -f live_windows.txt
    local idx=0
    for (( row=0; row < num_rows; row++ )); do
        local remaining=$(( num_windows - idx ))
        local windows_this_row=$(( remaining < max_cols ? remaining : max_cols ))
        local total_row_span=$window_width
        (( windows_this_row > 1 )) && total_row_span=$(( window_width + (windows_this_row - 1) * step_x ))
        local row_start_x=$(( margin + (available_w - total_row_span) / 2 ))
        local pos_y=$(( grid_start_y + row * v_step ))
        for (( col=0; col < windows_this_row; col++ )); do
            local id="${WINDOW_IDS[$((idx++))]}"
            local pos_x=$(( row_start_x + col * step_x ))
            xdotool windowsize "$id" "$window_width" "$window_height" 2>/dev/null || true
            xdotool windowmove "$id" "$pos_x" "$pos_y" 2>/dev/null || true
            echo "$id" >> live_windows.txt
        done
    done
    echo "DEBUG: stage_targets completed grid placement; live_windows.txt populated with ${num_windows} entries" >&2
}

#
# New: centralized yad parsing and button dispatch
#
# Parse yad form output into variables (safe, simple parse)
parse_yad_output() {
    local output="$1"
    # default empty
    new_url=""
    new_prompt=""
    new_burst=""
    new_fire_count=""
    new_stage=""
    # Use IFS read to split on '|' which is yad form delimiter
    IFS='|' read -r new_url new_prompt new_burst new_fire_count new_stage <<< "$output"
    # sanitize numeric fields (strip decimal part if yad returns floats)
    new_burst="${new_burst%%.*}"
    new_fire_count="${new_fire_count%%.*}"
    new_stage="${new_stage%%.*}"
}
# Apply parsed values to env (direct parse mode)
apply_parsed_values() {
    # new_* variables must be set by parse_yad_output
    [[ -n "${new_url:-}" ]] && { DEFAULT_URL="$new_url"; update_key_value .user_env DEFAULT_URL "$DEFAULT_URL"; }
    [[ -n "${new_prompt:-}" ]] && { DEFAULT_PROMPT="$new_prompt"; update_key_value .user_env DEFAULT_PROMPT "$DEFAULT_PROMPT"; }
    [[ -n "${new_burst:-}" ]] && { BURST_COUNT="$new_burst"; update_key_value .imagine_env BURST_COUNT "$BURST_COUNT"; }
    [[ -n "${new_fire_count:-}" ]] && { FIRE_COUNT="$new_fire_count"; update_key_value .imagine_env FIRE_COUNT "$FIRE_COUNT"; }
    [[ -n "${new_stage:-}" ]] && { STAGE_COUNT="$new_stage"; update_key_value .user_env STAGE_COUNT "$STAGE_COUNT"; }
}
# Manual compare-and-apply mode: source env, compare, and if changed, treat as STAGE
manual_compare_and_apply() {
    # load current env values for comparison
    source .imagine_env 2>/dev/null || true
    source .user_env 2>/dev/null || true
    prev_combined="${DEFAULT_URL:-}|${DEFAULT_PROMPT:-}|${BURST_COUNT:-}|${FIRE_COUNT:-}|${STAGE_COUNT:-}"
    # apply parsed values to env (but keep them in variables for comparison)
    apply_parsed_values
    new_combined="${DEFAULT_URL:-}|${DEFAULT_PROMPT:-}|${BURST_COUNT:-}|${FIRE_COUNT:-}|${STAGE_COUNT:-}"
    if [[ "$new_combined" != "$prev_combined" ]]; then
        echo "DEBUG: form values changed (manual compare); treating as STAGE" >&2
        return 0 # indicate changed
    fi
    return 1 # indicate not changed
}
# Button action dispatcher: map return codes to actions
handle_button_action() {
    local ret="$1"
    local yad_output="$2"
    # Always attempt to parse output if present
    if [[ -n "${yad_output:-}" ]]; then
        parse_yad_output "$yad_output"
    fi
    # Two-stage handling for all buttons:
    # 1) Direct parse & apply (keeps behavior consistent)
    # 2) Manual compare-and-apply (detects implicit STAGE-like changes)
    if [[ -n "${yad_output:-}" ]]; then
        apply_parsed_values
    fi
    # Manual compare: if it returns success (changed), we treat as STAGE
    if manual_compare_and_apply; then
        # perform STAGE action
        echo "DEBUG: STAGE triggered by changed form values" >&2
        stage_targets
        return 0
    fi
    # Now handle explicit button codes
    case "$ret" in
        0)
            # 0 is ambiguous: user pressed default button (STAGE) or closed with OK.
            # If we already handled changed values above, we returned. Otherwise, treat as no-op.
            echo "DEBUG: yad returned 0 with no changes; no action" >&2
            return 0
            ;;
        1)
            rm -f .setup_done 2>/dev/null || true
            echo "DEBUG: QUIT pressed; exiting" >&2
            exit 0
            ;;
        2)
            echo "DEBUG: STAGE button pressed — launching stage_targets" >&2
            stage_targets
            return 0
            ;;
        100) # FIRE
            echo "DEBUG: FIRE button pressed — requesting FIRE_COUNT" >&2
            firemode
            return 0
            ;;
        404) # STOP
            echo "DEBUG: STOP button pressed — stopping fire mode" >&2
            update_key_value .imagine_env FIRE_MODE "N"
            if [[ -f .daemon_pid ]]; then
                read -r daemon_pid < .daemon_pid
                kill "$daemon_pid" 2>/dev/null || true
                rm -f .daemon_pid 2>/dev/null || true
            fi
            return 0
            ;;
        4) # APPLY
            echo "DEBUG: APPLY pressed — saved settings" >&2
            # apply_parsed_values already ran above; nothing else to do
            return 0
            ;;
        *)
            echo "DEBUG: Unexpected yad return: $ret" >&2
            if yad --question --title="Exception" --text="Unexpected return: $ret\nRelaunch panel?" --button=Yes:0 --button=No:1; then
                return 0
            else
                rm -f .setup_done 2>/dev/null || true
                echo "DEBUG: Exiting due to unexpected return" >&2
                exit 0
            fi
            ;;
    esac
}
draw_panel() {
    load_environment
    local use_geom
    use_geom=$(auto_position_panel)
    echo "DEBUG: Fire mode without brackets:" "$FIRE_MODE"
    echo "DEBUG: Fire mode with brackets:" "${FIRE_MODE}"
    local -a fire_button_args=()
    if [[ "${FIRE_MODE:-N}" == "N" && -s live_windows.txt ]]; then
        fire_button_args+=( '--button=FIRE:100' )
    elif [[ "${FIRE_MODE:-N}" == "Y" ]]; then
        fire_button_args+=( '--button=STOP:404' )
    fi
    echo "DEBUG: Fire button args:" "${fire_button_args[*]}"
    # Run yad in form mode, capture combined stdout+stderr into a variable,
    # and preserve the exit code. No temporary files, no extra debugging output.
    yad --form \
        --width="$PANEL_DEFAULT_WIDTH" --height="$PANEL_DEFAULT_HEIGHT" \
        --geometry="$use_geom" --title="$PANEL_DEFAULT_TITLE" \
        --field="Target":TXT "$DEFAULT_URL" \
        --field="Prompt":TXT "$DEFAULT_PROMPT" \
        --field="Shots per window (burst)":NUM "${BURST_COUNT:-4}!1..20!1" \
        --field="Number fire rounds":NUM "${FIRE_COUNT:-1}!1..99!1" \
        --field="Number of windows":NUM "${STAGE_COUNT:-24}!1..48!1" \
        --button="APPLY":4 \
        "${fire_button_args[@]}" \
        --button="STAGE":2 \
        --button="QUIT":1 \
        --default=STAGE 2>&1 &
    local yad_pid=$!

    # Violent watcher — only when firing
    if [[ "${FIRE_MODE:-N}" == "Y" ]]; then
        (
            while kill -0 "$yad_pid" 2>/dev/null; do
                current_title=$(wmctrl -l | grep "$PANEL_DEFAULT_TITLE" | awk '{print substr($0, index($0,$4))}' | xargs)
                echo "Watcher sees: [$current_title]" >> .watcher_debug.txt
                if [[ "$current_title" == *COMPLETE* ]]; then
                    echo "Watcher: COMPLETE detected — resetting" >> .watcher_debug.txt
                    update_key_value .imagine_env FIRE_MODE "N"
                    rm -f .daemon_pid

                    # Nice kill (active)
                    kill "$yad_pid" 2>/dev/null || true

                    # Nasty kill (commented — uncomment to switch)
                    # kill -9 "$yad_pid" 2>/dev/null || true

                    exit 0
                fi
                sleep 0.8
            done
        ) &
    fi

    wait "$yad_pid"
    yad_ret=$?

    ret=$yad_ret

    handle_button_action "$ret" "$yad_output"
}
while true; do
    draw_panel
done
