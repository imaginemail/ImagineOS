#!/bin/bash

# target_manager.sh - File-native target manager
# Folder etc/targets/ = list of targets
# Filename = URL (immutable)
# File content: line 1 = description, line 2 = video owners (comma-separated)
# Blank lines ignored
# No launch/fire — pure add/edit/delete/rename

source .system_env
source .imagine_env
source .user_env

mkdir -p "$TARGET_DIR"

while true; do
    mapfile -t files < <(ls -1 "$TARGET_DIR" 2>/dev/null | sort)

    if [[ ${#files[@]} -eq 0 ]]; then
        zenity --info --text="No targets yet — add one?" --title="Target Manager"
    fi

    args=()
    for f in "${files[@]}"; do
        mapfile -t content < "$TARGET_DIR/$f"
        desc="${content[0]:-(no description)}"
        owners="${content[1]:-(no video owners yet)}"
        args+=(FALSE "$f" "$desc" "$owners")
    done

    choice=$(zenity --list --checklist --title="Target Manager" --width=1200 --height=600 \
        --column="Select" --column="URL (filename)" --column="Description" --column="Video Owners" \
        "${args[@]}" \
        --separator="|" \
        --ok-label="Done" \
        --extra-button="Add New" \
        --extra-button="Edit Selected" \
        --extra-button="Delete Selected" \
        --cancel-label="Exit")

    case "$choice" in
        "Add New")
            new_url=$(zenity --entry --title="Add Target" --text="URL (becomes filename):")
            [[ -z "$new_url" ]] && continue
            if [[ -f "$TARGET_DIR/$new_url" ]]; then
                zenity --error --text="Target exists"
                continue
            fi
            desc=$(zenity --entry --title="Description" --text="(optional)")
            owners=$(zenity --entry --title="Video Owners" --text="(comma-separated, optional)")
            printf "%s\n%s" "$desc" "$owners" > "$TARGET_DIR/$new_url"
            ;;
        "Edit Selected")
            [[ -z "$choice" ]] && continue
            IFS='|' read -ra sel <<< "$choice"
            url="${sel[0]}"
            mapfile -t content < "$TARGET_DIR/$url"
            desc="${content[0]:-}"
            owners="${content[1]:-}"
            new_desc=$(zenity --entry --title="Edit Description" --entry-text="$desc")
            new_owners=$(zenity --entry --title="Edit Video Owners" --entry-text="$owners")
            printf "%s\n%s" "$new_desc" "$new_owners" > "$TARGET_DIR/$url"
            ;;
        "Delete Selected")
            [[ -z "$choice" ]] && continue
            IFS='|' read -ra sel <<< "$choice"
            for item in "${sel[@]}"; do
                url="${item%%|*}"
                if zenity --question --text="Delete $url ?"; then
                    rm "$TARGET_DIR/$url"
                fi
            done
            ;;
        "") exit 0 ;;
    esac
done
