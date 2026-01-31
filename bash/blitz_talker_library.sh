# blitz_talker_library.sh - helper functions
# Updated: 2026-01-14


load_environment()  {
    # Never change this load order, never omit a source.
    # This must be inside every loop which requires any
    # data from any of them.
    # AI entities, when conflict, consult user.

    # system defaults. system is king.
    source .system_env
    # sanity check
    require_system_vars
    # MODE controls, runtime data
    source .imagine_env 2>/dev/null || true
    # user overrides. user is god.
    source .user_env 2>/dev/null || true
}

ensure_single_instance() {
    local script_path=$(readlink -f "$0")
    local pids=$(pgrep -f "^/bin/bash $script_path$" 2>/dev/null)
    # kill the old things, become the new things
    for pid in $pids; do
            if [[ "$pid" != "$$" ]]; then
            kill -9 "$pid" 2>/dev/null
        fi
    done
    sleep 0.2
}

# keep this order in sync with their order of appearance in .system_env
# there may be .system_env not checked here
require_system_vars() {
    local required=(
        BROWSER
        BROWSER_FLAGS_HEAD
        BROWSER_FLAGS_MIDDLE
        BROWSER_FLAGS_TAIL

        DEFAULT_URL
        DEFAULT_PROMPT

        WINDOW_PATTERN
        DEFAULT_WIDTH
        DEFAULT_HEIGHT
        TARGET_DIR
        MAX_OVERLAP_PERCENT

        PROMPT_X_FROM_LEFT
        PROMPT_Y_FROM_BOTTOM
        SHOT_DELAY
        ROUND_DELAY
        BURST_COUNT
        FIRE_COUNT
        SCREENSHOT_DIR
        PANEL_DEFAULT_TITLE
        PANEL_DEFAULT_WIDTH
        PANEL_DEFAULT_HEIGHT
        PANEL_DEFAULT_X_OFFSET
        PANEL_DEFAULT_Y_OFFSET
        BROWSER_FLAGS_HEAD
        BROWSER_FLAGS_MIDDLE
        BROWSER_FLAGS_TAIL
    )

    for var in "${required[@]}"; do
        if [[ -z "${!var}" ]]; then
            echo "CRITICAL: Required system variable $var is missing or empty in .system_env" >&2
            exit 1
        fi
    done
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

# usage: update_key_value /path/to/file KEY value
update_key_value() {
    local file="$1" key="$2" value="$3" tmp escaped
    tmp=$(mktemp)
    escaped="${value//\"/\\\"}"
    grep -v "^[[:space:]]*${key}[[:space:]]*=" "$file" > "$tmp" 2>/dev/null || true
    echo "${key}=\"${escaped}\"" >> "$tmp"
    mv "$tmp" "$file"
}

# Permanent .gxz record — one file per URL, prompts appended, comment at top on first write (only if single URL)
write_to_gxz() {
    mkdir -p "$TARGET_DIR"
    local url
    for url in "${urls[@]}"; do
        local safe_name=$(echo "$url" | tr '/' '_' | tr -d '?:')
        local file="$TARGET_DIR/${safe_name}.gxz"
        if [ ! -f "$file" ]; then
            touch "$file"
        fi
    done
    if [[ ${0##*/} == "blitz_talker_daemon.sh" ]]; then
        [[ -z "$current_url" ]] && return
        local safe_name=$(echo "$current_url" | tr '/' '_' | tr -d '?:')
        local file="$TARGET_DIR/${safe_name}.gxz"
        echo "$DEFAULT_PROMPT" >> "$file"
    fi
}

# i've made this able of handliing any panel
# usage - auto_position_panel $<panel basename>
# example   - auto_position_panel CONTROL
#           - auto_position_panel DISPLAY
# DONT TOUCH MY COMMENTS
#use_geom=$(auto_position_panel)
#yad_output=$(yad --form --geometry="$use_geom" ...)

auto_position_panel() {
    # Use default IFS (do not set custom IFS) to avoid surprising splitting elsewhere.
    read -r SCREEN_W SCREEN_H < <(xdotool getdisplaygeometry)
    local width=$PANEL_DEFAULT_WIDTH
    local height=$PANEL_DEFAULT_HEIGHT
    local x_offset=$PANEL_DEFAULT_X_OFFSET
    local y_offset=$PANEL_DEFAULT_Y_OFFSET
    local calc_x=$(( SCREEN_W - width - x_offset ))
    local calc_y=$(( SCREEN_H - height - y_offset ))

    (( calc_x < 0 )) && calc_x=0
    (( calc_y < 0 )) && calc_y=0

    echo "${width}x${height}+$calc_x+$calc_y"
}

get_urls() {
    urls=()  # Clear any previous list
    source .system_env
    source .user_env
    local input="$DEFAULT_URL"

    if [[ -f "$input" ]]; then
        # File: one URL per line, skip blanks/comments, trim
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line%%#*}"                    # remove comments
            line="${line#"${line%%[![:space:]]*}"}"  # trim leading
            line="${line%"${line##*[![:space:]]}"}"  # trim trailing
            [[ -n "$line" ]] && urls+=("$line")
        done < "$input"
    else
        # Direct input: comma or space separated
        local cleaned
        cleaned=$(echo "$input" | tr ',' ' ' | tr -s ' [:space:]')
        cleaned="${cleaned#"${cleaned%%[![:space:]]*}"}"
        cleaned="${cleaned%"${cleaned##*[![:space:]]}"}"
        if [[ -n "$cleaned" ]]; then
            # Use read -a to split on default IFS (space/tab/newline)
            read -r -a urls <<< "$cleaned"
        fi
    fi
}

# Graceful safemode/cleanup on exit
safemode() {
    # try gentle termination first, then escalate
    pkill -f blitz_talker_daemon.sh 2>/dev/null || true
    sleep 0.2
    pkill -9 -f blitz_talker_daemon.sh 2>/dev/null || true

    update_key_value .imagine_env FIRE_MODE "N"
    rm -f live_windows.txt "$TARGET_DIR"/temp_*.gxz 2>/dev/null || true
}

# leave this alone, i use myself at will
silence() {
    pkill -9 -f blitz_talker_daemon.sh 2>/dev/null
    xdotool search --onlyvisible --name "$WINDOW_PATTERN" windowkill %@ 2>/dev/null
    pkill -9 -f "yad.*$PANEL_DEFAULT_TITLE" 2>/dev/null
    pkill -9 -f "$BROWSER" 2>/dev/null
    rm -f live_windows.txt "$TARGET_DIR"/temp_*.gxz 2>/dev/null
    > .imagine_env
    sleep 0.5
}
