🍺 TiBeer - Client Connecté pour Tireuse à boissons

TiBeer Client est le logiciel embarqué (basé sur Python) pour Raspberry Pi permettant de transformer une tireuse à boissons standard en une tireuse connectée et intelligente.

Il gère l'authentification RFID, le contrôle des électrovannes, le comptage de débit en temps réel et l'affichage (mode Kiosk), tout en communiquant via WebSockets avec un serveur central (Django).

🚀 Fonctionnalités

    Authentification RFID : Lecture de badges (Mifare RC522).
    Contrôle de Vanne : Ouverture/Fermeture via GPIO (Relais) si la carte est autorisée, à du crédit et que le volume restant est suffisant.
    Débitmétrie : Comptage précis des impulsions pour mesurer le volume servi.
    Communication Temps Réel : Utilisation de Socket.IO pour synchroniser l'état avec le serveur et mettre à jour le solde de la carte.
    Affichage Kiosk : Lancement automatique d'un navigateur en plein écran pour l'interface utilisateur.
    Installation Automatisée : Script Bash complet pour le déploiement.
    Multi tireuses : l'interface d'admin Django permet de gérer plusieurs tireuses. 

🛠 Matériel et logiciels Requis

    1/ Matériel :
    Raspberry Pi : (Testé sur Pi 3B+ ) mais un autre Pi peut etre utilisé.
    Hat de terminaison GPIO : Pour permettre une connexion facile avec borniers aux GPIO.
    Lecteur RFID : Module RC522 (SPI).
    Débitmètre : Capteur à effet Hall (3 fils).
    Électrovanne : 12V ou 24V (pilotée via relais).
    Relais : Pour l'isolation du circuit de puissance.
    Écran : HDMI ou tactile pour l'interface visuelle.
    
    2/ Logiciel : 
    Clé SSH publique sur le poste local : Pour se connecter au Pi en SSH (à copier dans Raspberry Pi Imager au moment de la création de l'image)
    Raspberry Pi Imager : Pour créer l'image sur la SD du Pi .
    OS de l'image : Bookworm Lite Legacy ( choisir "Raspberry Pi OS (Other) => Raspberry Pi Os ( Legacy,32-bit) Lite")


📂 Structure du Projet

Le code est modulaire pour faciliter la maintenance :

/Pi
├── main.py                    # Point d'entrée principal (Orchestrateur)
├── controllers/
│   ├── tibeer_controller.py   # Gestion de la detection des events carte (presence,retrait ...)
├── hardware/
│   ├── rfid_reader.py         # Gestion du lecteur RC522
│   ├── valve.py               # Classe de gestion de l'électrovanne (sécurité intégrée)
│   └── flow_meter.py          # Gestion des interruptions du débitmètre
├── network/
│   ├── backend_client.py      # Gestion de la communication avec le backend
├── ui/
│   ├── ui_server.py           # Gestion de l'affichage sur l'ecran
├── utils/
│   ├── exceptions.py          # Gestion des exceptions
    ├── loger.py               # Gestion des logs
    ├── exceptions.py          # Gestion des exceptions
    ├── serial_tools.py        # Pour utilisation du port série (si utilisée VMA405)
├── install.sh                 # Script d'installation automatique
├── requirements.txt           # Dépendances Python

    Note : Le dépôt GitHub contient un dossier Pi. Le script d'installation se charge d'extraire ce contenu vers /home/sysop/tibeer sur la machine cible.

⚙️ Installation
1. Préparation du Raspberry Pi

Installez Raspberry Pi OS Lite (Legacy) via Raspberry Pi Imager en activant SSH et en copiant votre clé publique.
et en Configurant l'utilisateur par défaut (sysop).

2. Lancement du script d'installation

Connectez-vous en SSH au Raspberry Pi :

`# sur votre poste en local recupérez le ficjier install.sh puis copiez le sur le Pi

ou plus simple créez le directement sur le Pi :
 # Connecté en SSH sur le Pi :
nano install.sh
# coupiez le contenu du fichier install.sh depuis github
# collez le contenu puis CTRL+X et Y

# Rendre le script exécutable
chmod +x install.sh

# Lancer l'installation ( SANS sudo ! )
./install.sh
`
3. Durant l'installation

Le script interactif vous demandera :

    L'adresse du serveur Django (Backend).
    L'identifiant de la tireuse (ex: tireuse_gauche).
    L'URL du dépôt Git et la branche à cloner (master).
    De confirmer la création des clés SSH pour le déploiement sur GitHub.

Le script s'occupe automatiquement :

    Des mises à jour système (apt update/upgrade).
    De l'installation des dépendances système (python3-venv, spi-tools, etc.).
    De la création de l'environnement virtuel Python.
    De la configuration des droits GPIO.
    De l'installation et l'activation des services systemd (tibeer.service et kiosk.service).

🔌 Câblage (GPIO par défaut)

Les broches peuvent être modifiées dans le fichier .env généré, mais voici la configuration standard (BCM) :

Composant 	Pin RPi (BCM) 	Description
Vanne 	GPIO 18 	Contrôle du Relais
Débitmètre 	GPIO 23 	Entrée impulsion
RFID SDA 	GPIO 8 (CE0) 	SPI Chip Select
RFID SCK 	GPIO 11 	SPI Clock
RFID MOSI 	GPIO 10 	SPI MOSI
RFID MISO 	GPIO 9 	SPI MISO
RFID RST 	GPIO 25 	Reset du RC522

📝 Configuration (.env)

Une fois installé, la configuration se trouve dans /home/sysop/tibeer/.env.
Exemple :

DJANGO_SERVER=http://192.168.1.50:8000
TIREUSE_BEC=blonde_01
# GPIO Settings
PIN_VANNE=18
PIN_COMPTEUR=23
PIN_RFID_RST=25

Coté Admin de Django :


🖥 Commandes Utiles

Sur le Pi :

Pour gérer le service une fois installé :

# Entrer dans l'environnement virtuel
source tibeer/venv/bin/activate

# Voir les logs en temps réel
sudo journalctl -u tibeer -f

# Redémarrer le service
sudo systemctl restart kiosk.service tibeer.service

# Arrêter le service
sudo systemctl stop kiosk.service tibeer.service

Sur le serveur Django :
# Lancer le serveur 
uvicorn vanneweb.asgi:application --host 0.0.0.0 --port 8000

# TODO detailler la partie Django

🛠 Hardware connexion Pi :
![Cnx Pi.png](Pi/asset/Cnx%20Pi.png)
