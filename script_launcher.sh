#!/bin/bash

# script_launcher.sh - Minimal console launcher
# Lists all .sh files in its own folder
# Select one → launches it (no args, no output capture)
# Nothing else — pure selection and fire

MY_DIR="$(dirname "$(realpath "$0")")"

while true; do
    mapfile -t scripts < <(ls -1 "$MY_DIR"/*.sh 2>/dev/null | xargs -n1 basename | sort)

    if [[ ${#scripts[@]} -eq 0 ]]; then
        zenity --info --text="No .sh scripts found in $MY_DIR" --title="Launcher"
        exit 0
    fi

    args=()
    for s in "${scripts[@]}"; do
        args+=(FALSE "$s")
    done

    choice=$(zenity --list --radiolist --title="Launch Script" --width=600 --height=500 \
        --column="Select" --column="Script" \
        "${args[@]}" \
        --ok-label="Launch" \
        --cancel-label="Exit")

    [[ -z "$choice" ]] && exit 0

    "$MY_DIR/$choice" &
done
