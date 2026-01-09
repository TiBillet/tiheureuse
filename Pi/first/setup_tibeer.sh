#!/bin/bash
set -e

# ==========================================
#  INSTALLATION COMPLÈTE TIBEER (RPI BOOKWORM)
# ==========================================

# Vérif root
if [ "$EUID" -eq 0 ]; then
  echo "❌ Ne lance pas ce script en root/sudo."
  echo "👉 Lance-le avec : ./install_final.sh"
  exit 1
fi

SYSUSER="sysop"
TARGET_DIR="/home/$SYSUSER/tibeer"
VENV_DIR="$TARGET_DIR/.venv"

echo "🍻 INSTALLATION TIBEER - VERSION FINALE"
echo "---------------------------------------"

# ==========================================
# ÉTAPE 1 : Configuration & SSH
# ==========================================
echo "[1/10] 📝 Configuration..."

# 1.1 Variables
read -p "🔹 Adresse du serveur Django (ex: http://192.168.1.10:8000) : " DJANGO_SERVER
DJANGO_SERVER=${DJANGO_SERVER%/} # Retrait slash fin

read -p "🔹 ID Tireuse (slug, ex: narval) : " TIREUSE_BEC

# 1.2 SSH GitHub
echo ""
echo "--- 🔑 Configuration SSH pour GitHub ---"
if [ ! -f ~/.ssh/id_rsa.pub ]; then
    echo "Génération de la clé SSH..."
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -q
fi

echo "⚠️  COPIE CETTE CLÉ DANS GITHUB (Settings > SSH Keys) :"
echo "---------------------------------------------------------------"
cat ~/.ssh/id_rsa.pub
echo "---------------------------------------------------------------"
read -p "Une fois la clé ajoutée sur GitHub, appuie sur [Entrée]..." DUMMY

# 1.3 Clonage
read -p "🔹 Adresse du dépôt (ex: git@github.com:ton-user/tibeer.git) : " GIT_REPO

# ==========================================
# ÉTAPE 2 : Système de base
# ==========================================
echo ""
echo "[2/10] 📦 Installation dépendances système..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git nano locales fontconfig curl ca-certificates \
  python3 python3-venv python3-pip python3-dev \
  pigpio python3-pigpio \
  xserver-xorg xinit openbox unclutter x11-apps \
  chromium-browser chromium-chromedriver \
  fonts-dejavu-core xfonts-base \
  upower xserver-xorg-input-libinput

# Locale FR
sudo sed -i 's/^# *fr_FR.UTF-8 UTF-8/fr_FR.UTF-8 UTF-8/' /etc/locale.gen
sudo locale-gen || true

# ==========================================
# ÉTAPE 3 : Boot & Display (Mode Legacy)
# ==========================================
echo ""
echo "[3/10] 📺 Configuration Vidéo (FKMS/Legacy)..."
CFG_BOOT_DIR="/boot/firmware"
[ -d /boot/firmware ] || CFG_BOOT_DIR="/boot"
CFG_CONFIG_TXT="${CFG_BOOT_DIR}/config.txt"
CFG_CMDLINE_TXT="${CFG_BOOT_DIR}/cmdline.txt"

# Force FKMS
sudo sed -i '/^dtoverlay=vc4/d;/^hdmi_force_hotplug=/d' "${CFG_CONFIG_TXT}"
echo "dtoverlay=vc4-fkms-v3d" | sudo tee -a "${CFG_CONFIG_TXT}" >/dev/null
echo "hdmi_force_hotplug=1" | sudo tee -a "${CFG_CONFIG_TXT}" >/dev/null

# Consoleblank=0
if [ -f "${CFG_CMDLINE_TXT}" ]; then
  sudo sed -i 's/ consoleblank=[0-9]\+//g' "${CFG_CMDLINE_TXT}"
  grep -q 'consoleblank=0' "${CFG_CMDLINE_TXT}" || sudo sed -i 's/$/ consoleblank=0/' "${CFG_CMDLINE_TXT}"
fi

# SPI ON
sudo raspi-config nonint do_spi 0 || true

# ==========================================
# ÉTAPE 4 : Permissions
# ==========================================
echo ""
echo "[4/10] 🔐 Permissions Utilisateur & Xorg..."
sudo usermod -aG sudo,video,input,render,gpio,spi,dialout,tty "$SYSUSER"

# Xwrapper (Autoriser n'importe qui à lancer X)
echo "allowed_users=anybody" | sudo tee /etc/X11/Xwrapper.config >/dev/null
echo "needs_root_rights=yes" | sudo tee -a /etc/X11/Xwrapper.config >/dev/null

# ==========================================
# ÉTAPE 5 : Projet Python & Dépendances
# ==========================================
echo ""
echo "[5/10] 🐍 Clonage et Installation Python..."

# Clonage
if [ -d "$TARGET_DIR" ]; then
    echo "Dossier $TARGET_DIR existant, sauvegarde..."
    mv "$TARGET_DIR" "${TARGET_DIR}_bak_$(date +%s)"
fi
echo "Clonage depuis $GIT_REPO..."
git clone "$GIT_REPO" "$TARGET_DIR"

# Création Venv
echo "Création de l'environnement virtuel dans $VENV_DIR..."
cd "$TARGET_DIR"
python3 -m venv "$VENV_DIR"

# Installation packages
echo "Installation des dépendances Python..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
# LES LIBS DEMANDÉES EXPLICITEMENT :
pip install pyserial flask requests pigpio mfrc522 RPi.GPIO spidev python-dotenv channels daphne

# Si requirements.txt existe, on l'installe aussi pour être sûr
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi
deactivate

# ==========================================
# ÉTAPE 6 : Variables d'environnement
# ==========================================
echo ""
echo "[6/10] ⚙️ Création fichier .env..."
cat << EOF > "$TARGET_DIR/.env"
# Généré par le script d'installation
TIREUSE_BEC=$TIREUSE_BEC
API_URL=$DJANGO_SERVER
DEBUG=False
EOF
chmod 600 "$TARGET_DIR/.env"

# Fichier pour Kiosk (url)
echo "KIOSK_URL=${DJANGO_SERVER}/?tireuse_bec=${TIREUSE_BEC}" > "/home/$SYSUSER/kiosk.env"

# ==========================================
# ÉTAPE 7 : Configuration Affichage (Xinitrc)
# ==========================================
echo ""
echo "[7/10] 🖥️ Configuration .xinitrc (Openbox)..."

# Configuration Ant-Veille X11
sudo mkdir -p /etc/X11/xorg.conf.d
cat << 'EOF' | sudo tee /etc/X11/xorg.conf.d/10-dpms.conf >/dev/null
Section "Monitor"
    Identifier "HDMI-1"
    Option "DPMS" "false"
EndSection
Section "ServerFlags"
    Option "BlankTime"   "0"
    Option "OffTime"     "0"
EndSection
EOF

# .xinitrc
cat << 'EOF' > "/home/$SYSUSER/.xinitrc"
#!/bin/bash
exec > /home/sysop/.xinitrc.log 2>&1
set -x

# Charge URL
source /home/sysop/kiosk.env

export LANG=fr_FR.UTF-8
xset -dpms; xset s off; xset s noblank
( while true; do xset s reset; sleep 50; done ) &

# Cacher souris & Gestionnaire fenetre
(unclutter -idle 1 -root || true) &
(openbox --startup "/bin/true" || true) & sleep 1

# Boucle Chromium
while true; do
  chromium-browser \
    --kiosk "$KIOSK_URL" \
    --no-first-run --incognito --start-fullscreen \
    --check-for-update-interval=31536000 \
    --disable-translate \
    --enable-features=UseOzonePlatform --ozone-platform=x11 \
    --user-data-dir="/home/sysop/.config/chromium-kiosk"
  sleep 2
done
EOF
chmod +x "/home/$SYSUSER/.xinitrc"
chown "$SYSUSER:$SYSUSER" "/home/$SYSUSER/.xinitrc"

# ==========================================
# ÉTAPE 8 : Services Systemd
# ==========================================
echo ""
echo "[8/10] 🔧 Création des Services..."

# Pigpiod
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

# Service Kiosk (EXACTEMENT comme fourni)
cat << EOF | sudo tee /etc/systemd/system/kiosk.service
[Unit]
Description=Chromium Kiosk
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=sysop
WorkingDirectory=/home/sysop/tibeer
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
PAMName=login
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/sysop/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStartPre=/bin/sh -c 'setterm -blank 0 -powersave off -powerdown 0 </dev/tty1; \\
                         mkdir -p /run/user/1000; chown 1000:1000 /run/user/1000; \\
                         chvt 1 || true; sleep 0.2'
# Log Xorg dédié verbeux (utile au debug)
ExecStart=/usr/bin/xinit /home/sysop/.xinitrc -- /usr/lib/xorg/Xorg :0 -nolisten tcp -logverbose 6 -verbose 6 -logfile /home/sysop/Xorg.kiosk.log vt1 -keeptty
Restart=on-failure
RestartSec=8

[Install]
WantedBy=multi-user.target
EOF

# Service Tibeer (Adapté aux chemins créés)
cat << EOF | sudo tee /etc/systemd/system/tibeer.service
[Unit]
Description=Tibeer Logic
After=network-online.target pigpiod.service
Requires=pigpiod.service

[Service]
Type=simple
User=sysop
WorkingDirectory=$TARGET_DIR
EnvironmentFile=$TARGET_DIR/.env
# Utilisation du python dans le .venv qu'on vient de créer
ExecStart=$VENV_DIR/bin/python $TARGET_DIR/main.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

# ==========================================
# ÉTAPE 9 : Activation
# ==========================================
echo ""
echo "[9/10] 🚀 Activation des services..."
sudo systemctl daemon-reload
sudo systemctl enable kiosk
sudo systemctl enable tibeer
sudo systemctl disable --now getty@tty1.service || true

# ==========================================
# ÉTAPE 10 : Fin
# ==========================================
echo ""
echo "---------------------------------------"
echo "✅ INSTALLATION TERMINÉE"
echo "---------------------------------------"
echo "👉 Kiosk URL : $DJANGO_SERVER/?tireuse_bec=$TIREUSE_BEC"
echo "⚠️  REDÉMARRAGE NÉCESSAIRE (Prise en compte GPU Legacy)"
echo ""
read -p "Redémarrer maintenant ? (o/n) " REBOOT_NOW
if [[ "$REBOOT_NOW" =~ ^[oO]$ ]]; then
    sudo reboot
fi
