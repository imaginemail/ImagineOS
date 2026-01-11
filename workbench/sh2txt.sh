#!/bin/bash
# copy_sh_to_txt.sh - Interactively select *.sh files and FORCE copy them to *.sh.txt (overwriting old versions)
# This ensures the .txt files exactly match the current .sh sources (git-managed)
# Requires: yad (for nice checkbox selection) — fallback to basic menu if missing
# Run in the directory containing your .sh files

set -euo pipefail

# Find all *.sh files (excluding *.sh.txt to avoid duplicates)
mapfile -t sh_files < <(find . -maxdepth 1 -type f -name '*.sh' ! -name '*.sh.txt' | sort)

if [[ ${#sh_files[@]} -eq 0 ]]; then
    echo "No *.sh files found in $(pwd)"
    exit 1
fi

# Preferred: Use yad for multi-select checkboxes
if command -v yad >/dev/null 2>&1; then
    yad_args=()
    for file in "${sh_files[@]}"; do
        basename=$(basename "$file")
        yad_args+=(FALSE "$basename" "$file")
    done

    selected=$(yad --list --checklist --width=600 --height=500 \
        --title="Select Scripts to Overwrite as .txt" \
        --text="Check the .sh files you want to FORCE copy to .sh.txt (old .txt will be overwritten):" \
        --column="Select" --column="File" --column="Path:HD" \
        "${yad_args[@]}" --button=OK:0 --button=Cancel:1 || echo "CANCEL")

    if [[ "$selected" == "CANCEL" || -z "$selected" ]]; then
        echo "Cancelled or nothing selected."
        exit 0
    fi

    mapfile -t to_copy < <(echo "$selected" | cut -d'|' -f3)

# Fallback: Basic terminal menu if no yad
else
    echo "yad not found — using basic menu selection."
    echo "Available *.sh files:"
    PS3="Select files (enter number, 0 when done): "
    to_copy=()
    select file in "${sh_files[@]}"; do
        [[ -n "$file" ]] && to_copy+=("$file")
        [[ "$REPLY" == "0" ]] && break
    done
    [[ ${#to_copy[@]} -eq 0 ]] && { echo "Nothing selected."; exit 0; }
fi

# Perform forced copies (overwrites existing .sh.txt files)
echo "Overwriting ${#to_copy[@]} file(s) as .sh.txt:"
for src in "${to_copy[@]}"; do
    dst="${src}.txt"
    cp -f -v -- "$src" "$dst"  # -f forces overwrite
done

echo "Done! The .sh.txt files now exactly match the current git-managed .sh sources."
echo "Upload the new/overwritten .txt files to the project files area."