#!/bin/bash
# FILE: mini_vm_launcher.sh
# Launches Xephyr nested X server with a full desktop environment inside
# Active: KDE Plasma 6 (X11) — full session with kwin decorations, taskbar/panel, tray, etc.
# Other MX Linux/Debian-available DEs commented out for easy switching
# Everything (DE + your blitz scripts + browsers) runs contained in DISPLAY :1 — no leaks to host

XEPHYR_TITLE="Mini VM - KDE Plasma"
DISPLAY_NUM=:1
SCREEN_SIZE="1900x960"  # Adjust to fit your host screen/resolution needs

# Start Xephyr (nested X11 server)
#Xephyr -ac -br -noreset -screen "$SCREEN_SIZE" -title "$XEPHYR_TITLE" "$DISPLAY_NUM" &
Xephyr -glamor -ac -br -noreset -screen "$SCREEN_SIZE" -title "$XEPHYR_TITLE" "$DISPLAY_NUM" &
sleep 3  # Wait for Xephyr to initialize

# Force all subsequent commands to run inside the nested display
export DISPLAY="$DISPLAY_NUM"

# Fix Plasma panel screen assignment (critical for Xephyr)
sed -i 's/lastScreen=[0-9]\+/lastScreen=0/g' ~/.config/plasma-org.kde.plasma.desktop-appletsrc || true

# Launch full desktop environment (uncomment exactly one)

# KDE Plasma 6 (X11) — full session like your real desktop (kwin, panel, tray, effects, etc.)
dbus-launch --exit-with-session startplasma-x11 &

# # XFCE (MX default)
# dbus-launch --exit-with-session startxfce4 &

# # GNOME
# dbus-launch --exit-with-session gnome-session &

# # MATE
# dbus-launch --exit-with-session mate-session &

# # Cinnamon
# dbus-launch --exit-with-session cinnamon-session &

# # LXQt
# dbus-launch --exit-with-session lxqt-session &

# # LXDE
# dbus-launch --exit-with-session startlxde &

# # Fluxbox (lightweight, MX Fluxbox edition style)
# startfluxbox &

# Give the DE time to fully load (kwin decorations + panel/taskbar + tray)
sleep 5

# Optional: move the Xephyr window to the next virtual desktop on the host
NUM_DESKTOPS=$(wmctrl -d | wc -l)
if [ $? -eq 0 ] && [ $NUM_DESKTOPS -gt 1 ]; then
    CURRENT_DESKTOP=$(wmctrl -d | grep '\*' | awk '{print $1}')
    TARGET_DESKTOP=$(( (CURRENT_DESKTOP + 1) % NUM_DESKTOPS ))
    wmctrl -r "$XEPHYR_TITLE" -t $TARGET_DESKTOP
else
    echo "Only one desktop found (or wmctrl missing) — staying on current desktop."
fi

# Launch your blitz_talker control panel inside the nested Plasma desktop
./blitz_talker_control_gtk.py
