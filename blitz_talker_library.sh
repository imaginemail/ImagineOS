# FILE: blitz_talker_library.sh - helper functions

ensure_single_instance() {
    local script_path=$(readlink -f "$0")
    local pids=$(pgrep -f "^/bin/bash $script_path$" 2>/dev/null)
    for pid in $pids; do
        if [[ "$pid" != "$$" ]]; then
            kill -9 "$pid" 2>/dev/null
        fi
    done
    sleep 0.2
}

screencap() {
    mkdir -p "$SCREENSHOT_DIR"
    local name="${1:-panel_$(date +%s)}"
    ksnip --active --save "$SCREENSHOT_DIR/${name}.png" 2>/dev/null || true
}

calc_prompt_click() {
    # X from left — raw pixels or percentage
    if [[ "$PROMPT_X_FROM_LEFT" == *% ]]; then
        CLICK_X=$(( width * ${PROMPT_X_FROM_LEFT%\%} / 100 ))
    else
        CLICK_X=$PROMPT_X_FROM_LEFT
    fi

    # Y from bottom — raw pixels or percentage
    if [[ "$PROMPT_Y_FROM_BOTTOM" == *% ]]; then
        local pixels_from_bottom=$(( height * ${PROMPT_Y_FROM_BOTTOM%\%} / 100 ))
    else
        local pixels_from_bottom=$PROMPT_Y_FROM_BOTTOM
    fi

    CLICK_Y=$(( height - pixels_from_bottom ))
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

silence() {
    pkill -9 -f blitz_talker_daemon.sh 2>/dev/null
    xdotool search --onlyvisible --name "$WINDOW_PATTERN" windowkill %@ 2>/dev/null
    pkill -9 -f "yad.*$PANEL_TITLE" 2>/dev/null
    pkill -9 -f "$BROWSER" 2>/dev/null
    rm -f live_windows.txt "$TARGET_DIR"/temp_*.gxz 2>/dev/null
    > .imagine_env
    sleep 0.5
}
