#!/bin/bash

# File to store env vars
ENV_FILE="$HOME/.imagine_env"

# Initial strings (load from file if exists, or defaults)
if [ -f "$ENV_FILE" ]; then
  source "$ENV_FILE"
else
  STRING1="Default String One"
  STRING2="Default String Two"
fi

# Function to launch child gxmessage for editing a string
edit_string() {
  local var_name="$1"
  local current_val="$2"
  local new_val=$(gxmessage -entry -title "Edit $var_name" \
    -default "$current_val" "Enter new value for $var_name:")
  if [ -n "$new_val" ]; then
    echo "$var_name=\"$new_val\"" >> "$ENV_FILE"  # Write updated
  else
    echo "$var_name=\"$current_val\"" >> "$ENV_FILE"  # Write unchanged
  fi
}

# Main loop for the 'Blitz Talker' panel
while true; do
  # Read current env file for display
  if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
  fi

  # Main gxmessage with button
  choice=$(gxmessage -title "Blitz Talker" -buttons "Edit Strings:1,Quit:0" \
    "Current STRING1: $STRING1\nCurrent STRING2: $STRING2")

  if [ "$choice" = "1" ]; then
    # Launch two editors in background
    edit_string "STRING1" "$STRING1" &
    pid1=$!
    edit_string "STRING2" "$STRING2" &
    pid2=$!

    # Wait for both to finish writing before continuing
    wait $pid1 $pid2

    # Now safe to re-source the file (children have updated it)
    source "$ENV_FILE"
  elif [ "$choice" = "0" ]; then
    break
  fi
done
