#!/bin/bash
exec > /home/sysop/.xinitrc.log 2>&1
set -x

# Locale FR
export LANG=fr_FR.UTF-8
export LANGUAGE=fr_FR:fr
export LC_ALL=fr_FR.UTF-8

# URL kiosque
set -a; [ -f /home/sysop/kiosk.env ] && . /home/sysop/kiosk.env; set +a
URL="${KIOSK_URL:-https://tibillet.org/fr/docs/commentappairer}"

# Trouver Chromium
CHROMIUM_BIN="$(command -v chromium-browser || command -v chromium || true)"
[ -n "$CHROMIUM_BIN" ] || { echo "Chromium introuvable — on affiche xclock"; exec xclock; }

# Anti-veille X11 + watchdog
xset -dpms
xset s off
xset s noblank
( while true; do xset s reset; sleep 50; done ) &

# Curseur caché (après 1s)
(unclutter -idle 1 -root || true) &

# WM minimal
(openbox --startup "/bin/true" || true) & sleep 1

PROFILE_DIR="/home/sysop/.config/chromium-kiosk"
mkdir -p "$PROFILE_DIR/Default"
touch "$PROFILE_DIR/First Run"

# Boucle de relance Chromium (X reste actif si Chromium crash)
while true; do
  "$CHROMIUM_BIN" \
    --user-data-dir="$PROFILE_DIR" \
    --force-device-scale-factor=2.0 \
    --lang=fr --accept-lang=fr-FR,fr \
    --no-first-run --no-default-browser-check \
    --kiosk "$URL" --incognito --start-fullscreen \
    --overscroll-history-navigation=0 \
    --autoplay-policy=no-user-gesture-required \
    --disable-gpu --use-gl=swiftshader --disable-dev-shm-usage \
    --noerrdialogs --disable-session-crashed-bubble --disable-translate \
    --enable-features=UseOzonePlatform --ozone-platform=x11
  rc=$?
  echo "[KIOSK] Chromium terminé (rc=$rc), relance dans 2s…"
  sleep 2
done
