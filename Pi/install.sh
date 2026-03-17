#!/bin/bash
set -e

# ==========================================
#  INSTALLATION COMPLÈTE TIBEER (RPI BOOKWORM)
# ==========================================

# Vérif root
if [ "$EUID" -eq 0 ]; then
  echo "❌ Ne lance pas ce script en root/sudo."
  echo "👉 Lance-le avec : ./install.sh"
  exit 1
fi

SYSUSER="sysop"
TARGET_DIR="/home/$SYSUSER/tibeer"
VENV_DIR="$TARGET_DIR/.venv"
# ==========================================
# Valeurs exemple par defaut
# ==========================================
DEFAULT_DJANGO_SERVER="http://192.168.1.10:8000"
DEFAULT_GIT_REPO=git@github.com:TiBillet/tiheureuse.git
DEFAULT_GIT_BRANCH="master"
DEFAULT_TIREUSE_ID="b7100a7b-a1ff-4664-b2a0-e5b47d992d8a"


echo "🍻 INSTALLATION TIBEER "
echo "---------------------------------------"
# ==========================================
# ÉTAPE 1 : Système de base
# ==========================================

echo "[1/10] 📦 Installation dépendances système..."
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
# ÉTAPE 2 : Configuration SSH & Clonage
# ==========================================
echo "[2/10] 📝 Configuration..."

# 1.1 Variables

echo "🔹 Adresse IP du serveur Django"
read -p "   (Défaut: $DEFAULT_DJANGO_SERVER) : " DJANGO_SERVER
# Si la variable est vide, on prend la valeur par défaut
DJANGO_SERVER=${DJANGO_SERVER:-$DEFAULT_DJANGO_SERVER}
# Nettoyage du slash de fin
DJANGO_SERVER=${DJANGO_SERVER%/}
echo "   -> Utilisation de : $DJANGO_SERVER"

# --- Demande Nom Tireuse ---
read -p "🔹 Nom de la tireuse (UUID) [Défaut: $DEFAULT_TIREUSE_ID] : " TIREUSE_BEC
TIREUSE_BEC=${TIREUSE_BEC:-$DEFAULT_TIREUSE_ID}

# 2.2 SSH GitHub
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

# 2.3 Clonage
echo "--- Récupération du Code Source ---"
read -p "🔹 Voulez-vous cloner le dépôt maintenant ? (o/n) [o] : " DO_CLONE
DO_CLONE=${DO_CLONE:-o} # Par défaut 'o' si Entrée

if [[ "$DO_CLONE" =~ ^[oO]$ ]]; then

    # --- Saisie Repo avec défaut ---
    read -p "🔹 URL SSH du dépôt [Défaut: $DEFAULT_GIT_REPO] : " GIT_REPO
    GIT_REPO=${GIT_REPO:-$DEFAULT_GIT_REPO}

    # --- Saisie Branche avec défaut ---
    read -p "🔹 Quelle branche ? [Défaut: $DEFAULT_GIT_BRANCH] : " GIT_BRANCH
    GIT_BRANCH=${GIT_BRANCH:-$DEFAULT_GIT_BRANCH}

    echo "   -> Repo : $GIT_REPO"
    echo "   -> Branche : $GIT_BRANCH"

    # --- Procédure de clonage (via dossier temporaire pour extraire 'Pi') ---
    TEMP_DIR="/tmp/tibeer_temp_clone"

    # Nettoyage préalable
    rm -rf "$TEMP_DIR"

    # Backup si dossier cible non vide
    if [ "$(ls -A $TARGET_DIR 2>/dev/null)" ]; then
        echo "⚠️  Le dossier $TARGET_DIR n'est pas vide. Sauvegarde..."
        mv "$TARGET_DIR" "${TARGET_DIR}_bak_$(date +%s)"
        mkdir -p "$TARGET_DIR"
    fi

    echo "📥 Clonage temporaire..."
    git clone -b "$GIT_BRANCH" "$GIT_REPO" "$TEMP_DIR"

    if [ $? -ne 0 ]; then
        echo "❌ Échec du clonage. Vérifiez vos clés SSH ou l'URL."
        exit 1
    fi

    # Extraction du sous-dossier Pi
    SOURCE_SUBDIR="$TEMP_DIR/Pi"

    if [ -d "$SOURCE_SUBDIR" ]; then
        echo "📂 Déplacement du contenu de 'Pi' vers $TARGET_DIR..."
        cp -a "$SOURCE_SUBDIR/." "$TARGET_DIR/"
        rm -rf "$TEMP_DIR"
        echo "✅ Fichiers installés."
        
        # Rendre le script de mise à jour exécutable
        chmod +x "$TARGET_DIR/git-update.sh"
        echo "✅ Script de mise à jour rendu exécutable."
    else
        echo "❌ Erreur : Pas de dossier 'Pi' trouvé dans la branche $GIT_BRANCH."
        rm -rf "$TEMP_DIR"
        exit 1
    fi

else
    # Cas installation manuelle
    mkdir -p "$TARGET_DIR"
    echo ""
    echo "⚠️  INSTALLATION MANUELLE"
    echo "👉 Copiez le contenu de votre dossier 'Pi' dans $TARGET_DIR maintenant."
    read -p "Appuyez sur [Entrée] une fois fait..."
fi


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
# Extraction BACKEND_HOST et BACKEND_PORT depuis l'URL saisie
BACKEND_HOST_PARSED=$(echo "$DJANGO_SERVER" | sed 's~https\?://~~' | sed 's~:[0-9]*$~~')
BACKEND_PORT_PARSED=$(echo "$DJANGO_SERVER" | grep -oE ':[0-9]+$' | tr -d ':')
BACKEND_PORT_PARSED=${BACKEND_PORT_PARSED:-8000}
cat << EOF > "$TARGET_DIR/.env"
# Généré par le script d'installation
TIREUSE_BEC=$TIREUSE_BEC
API_URL=$DJANGO_SERVER
BACKEND_HOST=$BACKEND_HOST_PARSED
BACKEND_PORT=$BACKEND_PORT_PARSED
BACKEND_API_KEY=changeme
DEBUG=False
# GPIO Settings
GPIO_VANNE=12
GPIO_FLOW_SENSOR=16
RC522_RST_PIN=22
RC522_SDA_PIN=24
# Git Settings (pour mise à jour auto au démarrage)
GIT_REPO=${GIT_REPO:-$DEFAULT_GIT_REPO}
GIT_BRANCH=${GIT_BRANCH:-$DEFAULT_GIT_BRANCH}
EOF
chmod 600 "$TARGET_DIR/.env"

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

# Locale FR
export LANG=fr_FR.UTF-8
export LANGUAGE=fr_FR:fr
export LC_ALL=fr_FR.UTF-8

# URL kiosque - lit directement depuis .env
set -a; [ -f /home/sysop/tibeer/.env ] && . /home/sysop/tibeer/.env; set +a
DJANGO_SERVER="${API_URL:-http://192.168.1.10:8000}"
TIREUSE_ID="${TIREUSE_BEC:-}"
if [ -n "$TIREUSE_ID" ]; then
    URL="${DJANGO_SERVER}/?tireuse_bec=${TIREUSE_ID}"
else
    URL="${DJANGO_SERVER}/"
fi

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
Description==Agent RFID + Vanne (tibeer)
After=network-online.target pigpiod.service
Wants=network-online.target pigpiod.service
Requires=pigpiod.service

[Service]
Type=simple
User=sysop
WorkingDirectory=$TARGET_DIR
EnvironmentFile=$TARGET_DIR/.env
# Mise à jour git au démarrage (si configuré)
ExecStartPre=$TARGET_DIR/git-update.sh
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
