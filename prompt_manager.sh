#!/bin/bash
# want-manifest-20251217_003-sha256:<compute_this_after_saving>
# ImagineOS Token GRUB - Pure Reflection
# Mission: Compose prompts from staged tokens OR quick-fire arbitrary (including blank)
# Blank prompt displayed as visible "~" in all UI, but clipboard always gets true empty string

TOKENS_FILE="tokens.txt"
LAST_STAGE_FILE="$HOME/.imagineos_last_stage"
BLANK_DISPLAY="~"  # Single-key typable (~ via Shift+`), visible, never used in your prompts

declare -a CATEGORIES=(
    "Body" "Motion" "Texture" "Light" "Voice" "Intimacy" "Camera" "Atmosphere" "Space" "Negative"
)

selected_stage=""
[[ -f "$LAST_STAGE_FILE" ]] && selected_stage=$(cat "$LAST_STAGE_FILE")

declare -a stage_keys=()
declare -A token_lines=()
declare -A allowed_stages=()

current_section="Uncategorized"
current_allowed="all"

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line=$(echo "$raw_line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^# ]] && [[ "$line" != \#stage:* ]] && continue

    if [[ "$line" =~ \#stage:[[:space:]]*(.*)$ ]]; then
        tags="${BASH_REMATCH[1]}"
        current_allowed=$(echo "$tags" | tr '[:upper:]' '[:lower:]' | tr ',' ' ')
        [[ -z "$current_allowed" ]] && current_allowed="all"
        line=$(echo "$line" | sed 's/[[:space:]]*#stage:.*$//')
        [[ -z "$line" ]] && continue
    fi

    if [[ "$line" =~ ^==(.*)==$ ]]; then
        current_section="${BASH_REMATCH[1]}"
        continue
    fi

    if [[ "$current_section" =~ [Ss]tage ]]; then
        if [[ "$line" =~ ^([^:]+): ]]; then
            key=$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]' | sed 's/[[:space:]]*$//')
            [[ -n "$key" ]] && stage_keys+=("$key")
        fi
        continue
    fi

    [[ -z "$line" ]] && continue
    token_lines["$current_section"]+="$line"$'\n'
    allowed_stages["$current_section"]="$current_allowed"
done < "$TOKENS_FILE"

has_stages=false
((${#stage_keys[@]} > 0)) && has_stages=true

while true; do
    if $has_stages; then
        stage_args=()
        for s in "${stage_keys[@]}"; do
            [[ "$s" == "$selected_stage" ]] && stage_args+=(TRUE "$s") || stage_args+=(FALSE "$s")
        done

        stage_choice=$(zenity --title="Select Stage" --list --radiolist \
            --column="Select" --column="Stage" "${stage_args[@]}" \
            --width=400 --height=300 --ok-label="Proceed" --cancel-label="Exit Tool")

        case $? in
            0) [[ -n "$stage_choice" ]] && selected_stage=$(echo "$stage_choice" | tr '[:upper:]' '[:lower:]') ;;
            1|255) exit 0 ;;
        esac
        echo "$selected_stage" > "$LAST_STAGE_FILE"
    else
        selected_stage="all"
    fi

    while true; do
        cat_args=()
        for cat in "${CATEGORIES[@]}"; do
            if [[ "$cat" == "Negative" ]]; then
                [[ -n "${token_lines[$cat]}" ]] && cat_args+=(FALSE "$cat") || cat_args+=(FALSE "*$cat* (no tokens)")
            else
                allowed="${allowed_stages[$cat]:-all}"
                if [[ "$allowed" == "all" || "$allowed" == *"$selected_stage"* ]]; then
                    [[ -n "${token_lines[$cat]}" ]] && cat_args+=(FALSE "$cat") || cat_args+=(FALSE "*$cat* (no tokens)")
                else
                    cat_args+=(FALSE "*$cat* (not available)")
                fi
            fi
        done

        chosen_cats=$(zenity --list --checklist \
            --title="Step 1: Choose Categories - Stage: $selected_stage" \
            --width=700 --height=500 \
            --text="<b>Select categories OR use quick options below</b>\n(Leave all unchecked + no quick = pure blank)" \
            --column="Use" --column="Category" "${cat_args[@]}" \
            --ok-label="Next → Tokens" --cancel-label="Exit Tool" \
            --extra-button="Fire Blank" \
            --extra-button="Quick Arbitrary Prompt" \
            ${has_stages:+--extra-button="Return to Stage Selector"} \
            --separator="|")

        ret=$?

        case $ret in
            0) ;; # proceed to token forms
            1)
                if [[ "$chosen_cats" == "Fire Blank" ]]; then
                    final_prompt=""
                    run_mode="once"
                    copy_note="\n\n<i>Pure blank fired — gun will just hit Enter</i>"
                elif [[ "$chosen_cats" == "Quick Arbitrary Prompt" ]]; then
                    arbitrary=$(zenity --entry --title="Quick Arbitrary Prompt" \
                        --text="Enter any prompt (or leave blank for pure blank):" --entry-text="")
                    [[ $? -ne 0 ]] && continue
                    final_prompt="$arbitrary"
                    run_mode="once"
                    copy_note="\n\n<i>Arbitrary prompt copied — ready for gun</i>"
                elif [[ "$chosen_cats" == "Return to Stage Selector" ]]; then
                    break
                else
                    exit 0
                fi
                ;;
            *) exit 0 ;;
        esac

        if [[ $ret -eq 0 && -n "$chosen_cats" ]]; then
            IFS='|' read -ra active_cats <<< "$chosen_cats"

            forms_cmd=(zenity --forms --title="Step 2: Tokens for Chosen Categories" \
                --width=900 --height=600 \
                --text="<b>Chosen:</b> $chosen_cats\n\nLeave blank to skip axis" \
                --ok-label="Want." --cancel-label="Back" --separator="|")

            for cat in "${active_cats[@]}"; do
                options="${token_lines[$cat]%"$'\n'"}"
                if [[ "$options" == *$'\n'* ]]; then
                    forms_cmd+=(--add-combo="<b>$cat</b>")
                    forms_cmd+=("--combo-values=|${options//$'\n'/|}")
                else
                    forms_cmd+=(--add-entry="<b>$cat</b>")
                    forms_cmd+=("--entry-text=$options")
                fi
            done

            token_result=$("${forms_cmd[@]}")
            token_ret=$?

            if [[ $token_ret -ne 0 ]]; then
                continue
            fi

            IFS='|' read -ra token_fields <<< "$token_result"
            final_prompt=""
            idx=0
            for cat in "${active_cats[@]}"; do
                field="${token_fields[$idx]}"
                ((idx++))
                [[ -n "$field" ]] && final_prompt+="$field "
            done
            final_prompt=$(echo "$final_prompt" | sed 's/[[:space:]]*$//')
            run_mode="once"
            copy_note="\n\n<i>Composed prompt copied to clipboard</i>"
        fi

        if [[ $ret -eq 0 && -n "$chosen_cats" ]]; then
            custom=$(zenity --entry --title="Additional free text" --text="Append anything extra (optional, blank ok):")
            if [[ $? -eq 0 && -n "$custom" ]]; then
                final_prompt+="${final_prompt:+ }$custom"
            fi
        fi

        # Display uses ~ only when truly blank; clipboard always real (empty if blank)
        display_prompt="${final_prompt:-$BLANK_DISPLAY}"

        echo -n "$final_prompt" | (xclip -selection clipboard 2>/dev/null || wl-copy)

        (
            while :; do
                zenity --info --title="WANT MADE MANIFEST" --width=850 --height=500 \
                    --text="<tt>$display_prompt</tt>$copy_note\n\nReady for your gun." \
                    --ok-label="Done → Categories" --extra-button="Done → Stage Selector" --extra-button="Close Tool" || exit 0
                choice=$?
                [[ $choice -eq 0 ]] && exit 10
                [[ $choice -eq 1 ]] && exit 11
            done
        ) &
        MONITOR_PID=$!

        wait $MONITOR_PID
    done
done

: << 'OLD_CODE'
# want-manifest-20251216_010-sha256:5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b

# ImagineOS Token GRUB - Pure Reflection
# Two-step workflow with fixed --forms (combo + entry)

TOKENS_FILE="tokens.txt"
LAST_STAGE_FILE="$HOME/.imagineos_last_stage"

declare -a CATEGORIES=(
    "Body" "Motion" "Texture" "Light" "Voice" "Intimacy" "Camera" "Atmosphere" "Space"
)

selected_stage=""
[[ -f "$LAST_STAGE_FILE" ]] && selected_stage=$(cat "$LAST_STAGE_FILE")

declare -a stage_keys=()
declare -A token_lines=()
declare -A allowed_stages=()

current_section="Uncategorized"
current_allowed="all"

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line=$(echo "$raw_line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [[ -z "$line" ]] && continue
    [[ "$line" =~ ^# ]] && [[ "$line" != \#stage:* ]] && continue

    if [[ "$line" =~ \#stage:[[:space:]]*(.*)$ ]]; then
        tags="${BASH_REMATCH[1]}"
        current_allowed=$(echo "$tags" | tr '[:upper:]' '[:lower:]' | tr ',' ' ')
        [[ -z "$current_allowed" ]] && current_allowed="all"
        line=$(echo "$line" | sed 's/[[:space:]]*#stage:.*$//')
        [[ -z "$line" ]] && continue
    fi

    if [[ "$line" =~ ^==(.*)==$ ]]; then
        current_section="${BASH_REMATCH[1]}"
        continue
    fi

    if [[ "$current_section" =~ [Ss]tage ]]; then
        if [[ "$line" =~ ^([^:]+): ]]; then
            key=$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]' | sed 's/[[:space:]]*$//')
            [[ -n "$key" ]] && stage_keys+=("$key")
        fi
        continue
    fi

    [[ -z "$line" ]] && continue
    token_lines["$current_section"]+="$line"$'\n'
    allowed_stages["$current_section"]="$current_allowed"
done < "$TOKENS_FILE"

has_stages=false
((${#stage_keys[@]} > 0)) && has_stages=true

while true; do
    if $has_stages; then
        stage_args=()
        for s in "${stage_keys[@]}"; do
            [[ "$s" == "$selected_stage" ]] && stage_args+=(TRUE "$s") || stage_args+=(FALSE "$s")
        done

        stage_choice=$(zenity --title="Select Stage" --list --radiolist \
            --column="Select" --column="Stage" "${stage_args[@]}" \
            --width=400 --height=300 --ok-label="Proceed" --cancel-label="Exit Tool")

        case $? in
            0) [[ -n "$stage_choice" ]] && selected_stage=$(echo "$stage_choice" | tr '[:upper:]' '[:lower:]') ;;
            1|255) exit 0 ;;
        esac
        echo "$selected_stage" > "$LAST_STAGE_FILE"
    else
        selected_stage="all"
    fi

    while true; do
        # Step 1: Choose categories
        cat_args=()
        for cat in "${CATEGORIES[@]}"; do
            allowed="${allowed_stages[$cat]:-all}"
            if [[ "$allowed" == "all" || "$allowed" == *"$selected_stage"* ]]; then
                [[ -n "${token_lines[$cat]}" ]] && cat_args+=(FALSE "$cat") || cat_args+=(FALSE "*$cat* (no tokens)")
            else
                cat_args+=(FALSE "*$cat* (not available)")
            fi
        done

        chosen_cats=$(zenity --list --checklist \
            --title="Step 1: Choose Categories - Stage: $selected_stage" \
            --width=600 --height=500 \
            --text="<b>Select categories to configure</b>\n(Leave all unchecked = pure blank)" \
            --column="Use" --column="Category" "${cat_args[@]}" \
            --ok-label="Next → Tokens" --cancel-label="Exit Tool" \
            --extra-button="Fire Blank" \
            ${has_stages:+--extra-button="Return to Stage Selector"} \
            --separator="|")

        ret=$?

        case $ret in
            0) ;; # proceed
            1)
                if [[ "$chosen_cats" == "Fire Blank" ]]; then
                    final_prompt=""
                    run_mode="once"
                    copy_note="\n\n<i>Pure blank — press Enter anywhere</i>"
                    chosen_cats=""
                elif [[ "$chosen_cats" == "Return to Stage Selector" ]]; then
                    break
                else
                    exit 0
                fi
                ;;
            *) exit 0 ;;
        esac

        if [[ -z "$chosen_cats" ]]; then
            final_prompt=""
            run_mode="once"
            copy_note="\n\n<i>Prompt copied to clipboard</i>"
        else
            IFS='|' read -ra active_cats <<< "$chosen_cats"

            # Build --forms command
            forms_cmd=(zenity --forms --title="Step 2: Tokens for Chosen Categories" \
                --width=900 --height=600 \
                --text="<b>Chosen:</b> $chosen_cats\n\nLeave blank to skip axis" \
                --ok-label="Want." --cancel-label="Back" --separator="|")

            for cat in "${active_cats[@]}"; do
                options="${token_lines[$cat]%"$'\n'"}"  # strip trailing newline
                if [[ "$options" == *$'\n'* ]]; then
                    # Multi-token → combo with blank first
                    forms_cmd+=(--add-combo="<b>$cat</b>")
                    forms_cmd+=("--combo-values=|${options//$'\n'/|}")
                else
                    # Single token → entry (pre-filled, clearable)
                    forms_cmd+=(--add-entry="<b>$cat</b>")
                    forms_cmd+=("--entry-text=$options")
                fi
            done

            token_result=$("${forms_cmd[@]}")
            token_ret=$?

            if [[ $token_ret -ne 0 ]]; then
                continue
            fi

            IFS='|' read -ra token_fields <<< "$token_result"
            final_prompt=""
            idx=0
            for cat in "${active_cats[@]}"; do
                field="${token_fields[$idx]}"
                ((idx++))
                [[ -n "$field" ]] && final_prompt+="$field "
            done
            final_prompt=$(echo "$final_prompt" | sed 's/[[:space:]]*$//')
            run_mode="once"
            copy_note="\n\n<i>Prompt copied to clipboard</i>"
        fi

        # Repeat modes
        if [[ "$chosen_cats" == "Run Forever" ]]; then
            run_mode="forever"
            final_prompt=""
            copy_note="\n\n<i>Prompt copied to clipboard</i>"
        elif [[ "$chosen_cats" == "Run N times..." ]]; then
            n=$(zenity --entry --text="How many times?" --entry-text="10")
            [[ "$n" =~ ^[0-9]+$ && "$n" -gt 0 ]] && run_mode="$n" || continue
            final_prompt=""
            copy_note="\n\n<i>Prompt copied to clipboard</i>"
        fi

        # Free text + safety
        if [[ "$result" != "Fire Blank" ]]; then
            custom=$(zenity --entry --title="Additional free text" --text="Append anything extra (optional, blank ok):")
            if [[ $? -eq 0 ]]; then
                if [[ -n "$custom" ]]; then
                    if [[ "$run_mode" != "once" && -z "$final_prompt" ]]; then
                        if ! zenity --question --title="Confirm Repeat" --text="No axes selected.\nRepeated prompt will be only this free text:\n\n<tt>$custom</tt>\n\nContinue?" --ok-label="Yes" --cancel-label="No"; then
                            continue
                        fi
                    fi
                    final_prompt+="${final_prompt:+ }$custom"
                fi
            fi
        fi

        display_prompt="${final_prompt:-<pure blank>}"

        echo -n "$final_prompt" | (xclip -selection clipboard 2>/dev/null || wl-copy)

        (
            while :; do
                zenity --info --title="WANT MADE MANIFEST" --width=850 --height=500 \
                    --text="<tt>$display_prompt</tt>$copy_note\n\nReady for your gun." \
                    --ok-label="Done → Categories" --extra-button="Done → Stage Selector" --extra-button="Close Tool" || exit 0
                choice=$?
                [[ $choice -eq 0 ]] && exit 10
                [[ $choice -eq 1 ]] && exit 11
            done
        ) &
        MONITOR_PID=$!

        count=1
        while :; do
            [[ "$run_mode" == "once" ]] && break
            [[ "$run_mode" == "forever" ]] && sleep 3
            [[ "$run_mode" != "forever" ]] && ((count++)) && [[ $count -gt "$run_mode" ]] && break
            kill -0 $MONITOR_PID 2>/dev/null || { wait $MONITOR_PID; ec=$?; [[ $ec -eq 10 ]] && break; [[ $ec -eq 11 ]] && break 2; exit 0; }
        done

        kill $MONITOR_PID 2>/dev/null
        wait $MONITOR_PID 2>/dev/null
    done
done
OLD_CODE
