#!/bin/bash
xset -dpms; xset s off; xset s noblank
unclutter -idle 1 &
# trouver la sortie connectée (HDMI-1, eDP-1, etc.)
OUTPUT=$(xrandr -q | awk '/ connected/{print $1; exit}')

# rotation 180° (inverted). équivalent à reflect X + reflect Y
xrandr --output "$OUTPUT" --rotate inverted || {
  # fallback si --rotate échoue : double réflexion = 180°
  xrandr --output "$OUTPUT" --reflect x --reflect y
}


openbox-session & sleep 1
URL="http://192.168.1.10:8000/" 
exec chromium-browser \
  --no-first-run --no-default-browser-check \
  --disable-translate --disable-session-crashed-bubble \
  --check-for-update-interval=31536000 \
  --kiosk "$URL" --incognito --overscroll-history-navigation=0 \
  --autoplay-policy=no-user-gesture-required --start-fullscreen
EOF
