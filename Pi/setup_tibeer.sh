#!/usr/bin/env bash
set -euo pipefail
# TODO TIREUSE_BEC_ID en parametre ou generer aleatoire
##############################################
#  PARAMÈTRES À ADAPTER
##############################################
AGENT_PORT="5000"
AGENT_BASE_URL=$(ip -4 addr show scope global | sed -n 's/.*inet \([0-9.]*\)\/.*/\1/p' | head -n1)
DJANGO_BASE_URL="http://192.168.1.10:8000"
TIREUSE_BEC_ID="narval"
LIQUID_LABEL="Leffe"
AGENT_SHARED_KEY="changeme"
SYSUSER="sysop"

##############################################
#  DÉTECTIONS DE FICHIERS BOOT (Legacy)
##############################################
CFG_BOOT_DIR="/boot/firmware"
[ -d /boot/firmware ] || CFG_BOOT_DIR="/boot"
CFG_CONFIG_TXT="${CFG_BOOT_DIR}/config.txt"
CFG_CMDLINE_TXT="${CFG_BOOT_DIR}/cmdline.txt"

echo "[1/10] MAJ paquets & install de base…"
apt-get update
apt-get install -y --no-install-recommends \
  sudo curl ca-certificates git nano locales \
  python3 python3-venv python3-pip \
  pigpio python3-pigpio \
  xserver-xorg xinit openbox unclutter x11-apps \
  chromium-browser chromium \
  fonts-dejavu-core xfonts-base \
  upower \
  xserver-xorg-input-libinput \


echo "[2/10] Locale système en FR…"
sed -i 's/^# *fr_FR.UTF-8 UTF-8/fr_FR.UTF-8 UTF-8/' /etc/locale.gen
locale-gen || true

echo "[3/10] Création utilisateur ${SYSUSER} (si besoin)…"
if ! id -u "${SYSUSER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${SYSUSER}"
  usermod -aG sudo,video,input,render,gpio,spi "${SYSUSER}"
fi

echo "[4/10] GPU/HDMI Legacy (FKMS) + SPI…"
# Nettoie les lignes existantes puis force un 1080p60 stable et FKMS
sed -i '/^dtoverlay=vc4/d;/^hdmi_force_hotplug=/d;/^hdmi_group=/d;/^hdmi_mode=/d' "${CFG_CONFIG_TXT}"
{
  echo 'dtoverlay=vc4-fkms-v3d'
  echo 'hdmi_force_hotplug=1'
  echo 'hdmi_group=2'
  echo 'hdmi_mode=82'   # 1080p60
} >> "${CFG_CONFIG_TXT}"

# Active SPI (non interactif)
raspi-config nonint do_spi 0 || true

echo "[5/10] Désactiver TOUTE veille console (cmdline consoleblank=0)…"
if [ -f "${CFG_CMDLINE_TXT}" ]; then
  sed -i 's/ consoleblank=[0-9]\+//g' "${CFG_CMDLINE_TXT}"
  grep -q 'consoleblank=0' "${CFG_CMDLINE_TXT}" || sed -i 's/$/ consoleblank=0/' "${CFG_CMDLINE_TXT}"
else
  echo "⚠️  ${CFG_CMDLINE_TXT} introuvable — vérifie l’emplacement (Legacy = /boot/firmware)."
fi

echo "[6/10] Venv Python pour ${SYSUSER} + paquets agent…"
su - "${SYSUSER}" -s /bin/bash <<'EOSU'
set -euo pipefail
cd ~
[ -d .venv ] || python3 -m venv .venv
. ~/.venv/bin/activate
pip install --upgrade pip
pip install flask requests pigpio mfrc522 RPi.GPIO spidev
deactivate
EOSU

echo "[7/10] Fichiers d’environnement & policies Chromium…"
# Environnement tibeer (lu par systemd)
tee /etc/default/tibeer >/dev/null <<EOF
AGENT_PORT=${AGENT_PORT}
AGENT_BASE_URL=http://${AGENT_BASE_URL}:${AGENT_PORT}
DJANGO_BASE_URL=${DJANGO_BASE_URL}
AGENT_SHARED_KEY=${AGENT_SHARED_KEY}
TIREUSE_BEC_ID=${TIREUSE_BEC_ID}
LIQUID_LABEL=${LIQUID_LABEL}
EOF
chmod 644 /etc/default/tibeer

# URL du kiosk (lu par ~/.xinitrc)
tee "/home/${SYSUSER}/kiosk.env" >/dev/null <<EOF
KIOSK_URL=${DJANGO_BASE_URL}/?tireuse_bec=${TIREUSE_BEC_ID}
EOF
chown "${SYSUSER}:${SYSUSER}" "/home/${SYSUSER}/kiosk.env"
chmod 644 "/home/${SYSUSER}/kiosk.env"

# Policies Chromium pour désactiver la traduction
mkdir -p /etc/chromium/policies/managed
tee /etc/chromium/policies/managed/kiosk.json >/dev/null <<'JSON'
{
  "TranslateEnabled": false,
  "DefaultBrowserSettingEnabled": false,
  "BrowserAddPersonEnabled": false,
  "SpellCheckEnabled": true
}
JSON

echo "[8/10] Xwrapper + profil Chromium + .xinitrc…"
# Autoriser Xorg à avoir les droits requis
tee /etc/X11/Xwrapper.config >/dev/null <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF

# Préseed du profil Chromium (pas d’assistant 1er lancement)
su - "${SYSUSER}" -s /bin/bash <<'EOSU'
mkdir -p ~/.config/chromium-kiosk/Default
touch ~/.config/chromium-kiosk/"First Run"
cat > ~/.config/chromium-kiosk/Default/Preferences <<'JSON'
{
  "translate": {"enabled": false, "recent_target": "fr", "blocked_languages": ["fr"], "blocked_sites": []},
  "intl": {"accept_languages": "fr-FR,fr"}
}
JSON
EOSU

# ~/.xinitrc : kiosk robuste (FR, pas de veille, curseur masqué, relance Chromium en boucle)
tee "/home/${SYSUSER}/.xinitrc" >/dev/null <<'SH'
#!/bin/bash
exec > /home/sysop/.xinitrc.log 2>&1
set -x

# Locale FR
export LANG=fr_FR.UTF-8
export LANGUAGE=fr_FR:fr
export LC_ALL=fr_FR.UTF-8

# URL kiosque
set -a; [ -f /home/sysop/kiosk.env ] && . /home/sysop/kiosk.env; set +a
URL="${KIOSK_URL:-http://neverssl.com}"

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
SH
chown "${SYSUSER}:${SYSUSER}" "/home/${SYSUSER}/.xinitrc"
chmod 644 "/home/${SYSUSER}/.xinitrc"

echo "[9/10] Services systemd : kiosk, pigpiod, tibeer…"
# Service kiosk
tee /etc/systemd/system/kiosk.service >/dev/null <<'EOF'
[Unit]
Description=Chromium Kiosk
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=sysop
WorkingDirectory=/home/sysop
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
PAMName=login
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/sysop/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStartPre=/bin/sh -c 'setterm -blank 0 -powersave off -powerdown 0 </dev/tty1; \
                         mkdir -p /run/user/1000; chown 1000:1000 /run/user/1000; \
                         chvt 1 || true; sleep 0.2'
# Log Xorg dédié verbeux
ExecStart=/usr/bin/xinit /home/sysop/.xinitrc -- /usr/lib/xorg/Xorg :0 -nolisten tcp -logverbose 6 -verbose 6 -logfile /home/sysop/Xorg.kiosk.log vt1 -keeptty
Restart=on-failure
RestartSec=8

[Install]
WantedBy=multi-user.target
EOF

# Ne pas laisser le getty prendre tty1
systemctl disable --now getty@tty1.service || true

# Service pigpio daemon
systemctl enable --now pigpiod

# Forcer absence economiseur ecran
sudo mkdir -p /etc/X11/xorg.conf.d
sudo tee /etc/X11/xorg.conf.d/10-dpms.conf >/dev/null <<'EOF'
Section "Monitor"
    Identifier "HDMI-1"
    Option "DPMS" "false"
EndSection

Section "ServerFlags"
    Option "BlankTime"   "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
EndSection
EOF


# Service tibeer (dépend de pigpiod)
tee /etc/systemd/system/tibeer.service >/dev/null <<'EOF'
[Unit]
Description=Agent RFID + Vanne (tibeer)
After=network-online.target pigpiod.service
Wants=network-online.target pigpiod.service
Requires=pigpiod.service

[Service]
Type=simple
User=sysop
WorkingDirectory=/home/sysop
EnvironmentFile=-/etc/default/tibeer
ExecStart=/home/sysop/.venv/bin/python /home/sysop/tibeer.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

echo "[10/10] Enable services + reload…"
systemctl daemon-reload
systemctl enable --now kiosk
systemctl enable --now tibeer

echo "✅ Installation terminée.

➡️  Recommandé : redémarrer pour appliquer FKMS & cmdline (consoleblank=0).
Infos utiles :
- URL kiosk : $(cat /home/${SYSUSER}/kiosk.env)
- Logs kiosk : /home/${SYSUSER}/.xinitrc.log  et  /home/${SYSUSER}/Xorg.kiosk.log
- État services : journalctl -u kiosk -b -n 150 --no-pager
                  journalctl -u tibeer -n 100 --no-pager
"
sudo shutdown -r "now + 20 seconds"