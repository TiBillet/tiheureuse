#!/bin/bash
set -e

# ==========================================
#         INSTALLATION TIBEER
# ==========================================

# Vérification que le script n'est PAS lancé en root
if [ "$EUID" -eq 0 ]; then
  echo "❌ Ne lancez pas ce script en root."
  echo "👉 Lancez-le avec : ./install_ultimate.sh"
  exit 1
fi

SYSUSER="sysop"
TARGET_DIR="/home/$SYSUSER/tibeer"

echo "🍻 BIENVENUE DANS L'INSTALLATEUR TIBEER"
echo "---------------------------------------"

# ==========================================
# ÉTAPE 1 : Configuration initiale
# ==========================================
echo "[1/10] 📝 Configuration des variables"
# ==========================================
# CONFIGURATION PAR DÉFAUT (POUR TESTS)
# ==========================================
DEFAULT_DJANGO_SERVER="http://192.168.1.10:8000"
DEFAULT_GIT_REPO="git@github.com:TiBillet/tiheureuse.git"
DEFAULT_GIT_BRANCH="master"
DEFAULT_TIREUSE_ID="b7100a7b-a1ff-4664-b2a0-e5b47d992d8a"

# --- Demande Django ---
echo "🔹 Adresse IP du serveur Django"
read -p "   (Défaut: $DEFAULT_DJANGO_SERVER) : " DJANGO_SERVER
# Si la variable est vide, on prend la valeur par défaut
DJANGO_SERVER=${DJANGO_SERVER:-$DEFAULT_DJANGO_SERVER}
# Nettoyage du slash de fin
DJANGO_SERVER=${DJANGO_SERVER%/}
echo "   -> Utilisation de : $DJANGO_SERVER"

# --- Demande Nom Tireuse ---
read -p "🔹 Nom de la tireuse (slug) [Défaut: $DEFAULT_TIREUSE_ID] : " TIREUSE_BEC
TIREUSE_BEC=${TIREUSE_BEC:-$DEFAULT_TIREUSE_ID}

# ==========================================
# ÉTAPE 1 : Système de base
# ==========================================
echo ""
echo "[2/10] 📦 Installation des paquets système..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git nano locales fontconfig \
  python3 python3-venv python3-pip python3-dev \
  pigpio python3-pigpio \
  xserver-xorg xinit openbox unclutter x11-apps \
  chromium-browser chromium-chromedriver \
  fonts-dejavu-core xfonts-base \
  upower xserver-xorg-input-libinput

# Configuration Locale FR
echo "   -> Configuration Locale FR..."
sudo sed -i 's/^# *fr_FR.UTF-8 UTF-8/fr_FR.UTF-8 UTF-8/' /etc/locale.gen
sudo locale-gen || true
# ==========================================
# ÉTAPE 2 : Preparation Github et clonnage
# ==========================================
echo ""
echo "--- Gestion Clé SSH pour GitHub ---"
if [ ! -f ~/.ssh/id_rsa.pub ]; then
    echo "Génération de la clé SSH..."
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N "" -q
fi

echo "⚠️  AJOUTEZ CETTE CLÉ À VOTRE COMPTE GITHUB (Settings > SSH Keys) :"
echo "---------------------------------------------------------------"
cat ~/.ssh/id_rsa.pub
echo "---------------------------------------------------------------"
read -p "Appuyez sur [Entrée] une fois la clé ajoutée sur GitHub..." DUMMY

echo ""
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
    else
        echo "❌ Erreur : Pas de dossier 'Pi' trouvé dans la branche $GIT_BRANCH."
        rm -rf "$TEMP_DIR"
        exit 1
    fi

else
    mkdir -p "$TARGET_DIR"
    echo "⚠️  Installation manuelle choisie."
    echo "👉 Copiez vos fichiers dans $TARGET_DIR maintenant."
    read -p "Appuyez sur [Entrée] pour continuer..."
fi

# ==========================================
# ÉTAPE 3 : Configuration Boot & GPU (Méthode Legacy)
# ==========================================
echo ""
echo "[3/10] 📺 Configuration GPU/HDMI Legacy (FKMS)..."

CFG_BOOT_DIR="/boot/firmware"
[ -d /boot/firmware ] || CFG_BOOT_DIR="/boot"
CFG_CONFIG_TXT="${CFG_BOOT_DIR}/config.txt"
CFG_CMDLINE_TXT="${CFG_BOOT_DIR}/cmdline.txt"

# Force le mode FKMS (Legacy) pour X11 stable
sudo sed -i '/^dtoverlay=vc4/d;/^hdmi_force_hotplug=/d;/^hdmi_group=/d;/^hdmi_mode=/d' "${CFG_CONFIG_TXT}"
echo "dtoverlay=vc4-fkms-v3d" | sudo tee -a "${CFG_CONFIG_TXT}" >/dev/null
echo "hdmi_force_hotplug=1" | sudo tee -a "${CFG_CONFIG_TXT}" >/dev/null

# Désactiver veille console
if [ -f "${CFG_CMDLINE_TXT}" ]; then
  sudo sed -i 's/ consoleblank=[0-9]\+//g' "${CFG_CMDLINE_TXT}"
  # Ajoute consoleblank=0 à la fin de la ligne si pas présent
  grep -q 'consoleblank=0' "${CFG_CMDLINE_TXT}" || sudo sed -i 's/$/ consoleblank=0/' "${CFG_CMDLINE_TXT}"
fi

# Activation SPI
sudo raspi-config nonint do_spi 0 || true

# ==========================================
# ÉTAPE 4 : Permissions Utilisateur
# ==========================================
echo ""
echo "[4/10] 🔐 Gestion des groupes utilisateur..."
sudo usermod -aG sudo,video,input,render,gpio,spi,dialout,tty "$SYSUSER"

# Autoriser Xorg pour utilisateur normal
echo "allowed_users=anybody" | sudo tee /etc/X11/Xwrapper.config >/dev/null
echo "needs_root_rights=yes" | sudo tee -a /etc/X11/Xwrapper.config >/dev/null

# ==========================================
# ÉTAPE 5 : Installation Application (Python)
# ==========================================
echo ""
echo "[5/10] 🐍 Installation de Tibeer (Python)..."
echo "   -> Configuration Environnement Virtuel..."
cd "$TARGET_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "⚠️ Pas de requirements.txt, installation par défaut..."
 pip install pyserial flask requests pigpio mfrc522 RPi.GPIO spidev python-dotenv channels daphne
fi
deactivate

# ==========================================
# ÉTAPE 6 : Création du .env
# ==========================================
echo ""
echo "[6/10] ⚙️ Génération du fichier .env..."
cat << EOF > "$TARGET_DIR/.env"
# Configuration Tibeer
TIREUSE_BEC=$TIREUSE_BEC
API_URL=$DJANGO_SERVER
DEBUG=False
EOF
chmod 600 "$TARGET_DIR/.env"

# ==========================================
# ÉTAPE 7 : Configuration KIOSK (Chromium + X11)
# ==========================================
echo ""
echo "[7/10] 🖥️ Configuration de l'affichage (X11/OpenBox)..."

# Configuration Xorg pour empêcher la veille (DPMS off)
sudo mkdir -p /etc/X11/xorg.conf.d
cat << 'EOF' | sudo tee /etc/X11/xorg.conf.d/10-dpms.conf >/dev/null
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
# Desactivation de la traduction google
sudo mkdir -p /etc/chromium/policies/managed
sudo tee /etc/chromium/policies/managed/kiosk.json >/dev/null <<'JSON'
{
  "TranslateEnabled": false,
  "DefaultBrowserSettingEnabled": false,
  "BrowserAddPersonEnabled": false,
  "SpellCheckEnabled": true
}
JSON

# URL pour le Kiosk
KIOSK_URL="$DJANGO_SERVER/?tireuse_bec=$TIREUSE_BEC"

# Création du .xinitrc
cat << EOF > /home/$SYSUSER/.xinitrc
#!/bin/bash
exec > /home/$SYSUSER/.xinitrc.log 2>&1
set -x

# Locale FR
export LANG=fr_FR.UTF-8

# Nettoyage lock chromium si crash
rm -rf ~/.config/chromium/Singleton*

# Gestion énergie X11
xset -dpms
xset s off
xset s noblank

# Gestionnaire de fenêtre minimal (obligatoire sinon chromium s'affiche mal)
openbox --startup "/bin/true" &

# Cacher la souris
unclutter -idle 0.5 -root &

# Boucle de lancement Chromium
while true; do
  chromium-browser \\
    --no-first-run \\
    --kiosk \\
    --incognito \\
    --disable-restore-session-state \\
    --disable-infobars \\
    --start-maximized \\
    --noerrdialogs \\
    --disable-translate \\
    --autoplay-policy=no-user-gesture-required \\
    --check-for-update-interval=31536000 \\
    --enable-features=UseOzonePlatform --ozone-platform=x11 \\
    "$KIOSK_URL"

  echo "Chromium crashé ou fermé, relance dans 2s..."
  sleep 2
done
EOF

chmod +x /home/$SYSUSER/.xinitrc
chown $SYSUSER:$SYSUSER /home/$SYSUSER/.xinitrc

# ==========================================
# ÉTAPE 8 : Services Systemd
# ==========================================
echo ""
echo "[8/10] 🔧 Création des Services Systemd..."

# 1. Pigpiod (GPIO)
sudo systemctl enable pigpiod
sudo systemctl start pigpiod

# 2. Service Tibeer Python
cat << EOF | sudo tee /etc/systemd/system/tibeer.service
[Unit]
Description=Tibeer Logic (RFID+Vanne)
After=network.target pigpiod.service
Requires=pigpiod.service

[Service]
User=$SYSUSER
WorkingDirectory=$TARGET_DIR
EnvironmentFile=$TARGET_DIR/.env
ExecStart=$TARGET_DIR/venv/bin/python $TARGET_DIR/main.py
Restart=always
RestartSec=3
StandardOutput=syslog
SyslogIdentifier=tibeer

[Install]
WantedBy=multi-user.target
EOF

# 3. Service Kiosk (X11)
# Copie exacte de ton infrastructure
cat << EOF | sudo tee /etc/systemd/system/kiosk.service
[Unit]
Description=Chromium Kiosk
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=$SYSUSER
WorkingDirectory=/home/$SYSUSER/tibeer
StandardInput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
PAMName=login
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$SYSUSER/.Xauthority
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStartPre=/bin/sh -c 'setterm -blank 0 -powersave off -powerdown 0 </dev/tty1; \\
                         mkdir -p /run/user/1000; chown 1000:1000 /run/user/1000; \\
                         chvt 1 || true; sleep 0.2'
# Log Xorg dédié
ExecStart=/usr/bin/xinit /home/$SYSUSER/.xinitrc -- /usr/lib/xorg/Xorg :0 -nolisten tcp -logverbose 6 -verbose 6 -logfile /home/$SYSUSER/Xorg.kiosk.log vt1 -keeptty
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ==========================================
# ÉTAPE 9 : Activation finale
# ==========================================
echo ""
echo "[9/10] 🚀 Activation..."
sudo systemctl daemon-reload
sudo systemctl enable tibeer
sudo systemctl enable kiosk

# Désactivation du getty sur tty1 (pour laisser la place au Kiosk)
sudo systemctl disable --now getty@tty1.service || true

# ==========================================
# ÉTAPE 10 : Fin
# ==========================================
echo ""
echo "---------------------------------------"
echo "✅ INSTALLATION ULTIME TERMINÉE !"
echo "---------------------------------------"
echo "👉 URL Cible : $KIOSK_URL"
echo "👉 La clé SSH est dans : ~/.ssh/id_rsa.pub"
echo ""
echo "⚠️  REDÉMARRAGE IMPÉRATIF NÉCESSAIRE"
echo "    (Pour basculer le GPU en mode Legacy FKMS)"
echo ""
read -p "Redémarrer maintenant ? (o/n) " REBOOT_NOW
if [[ "$REBOOT_NOW" =~ ^[oO]$ ]]; then
    sudo reboot
fi
