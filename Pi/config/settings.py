import os
from dotenv import load_dotenv
from pathlib import Path

# --- Chargement des variables d'environnement ---
load_dotenv()  # Charge le fichier .env

# --- Chemins et répertoires ---
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path("/var/log/tibeer")
LOG_DIR.mkdir(parents=True, exist_ok=True)  # Crée le répertoire si inexistant

# --- Configuration RFID ---
RFID_DEVICE = os.getenv("RFID_DEVICE", "/dev/ttyS0")  # Port série pour VMA405
RFID_TIMEOUT = float(os.getenv("RFID_TIMEOUT", "1.0"))  # Timeout en secondes

# --- Configuration Vanne ---
VALVE_GPIO_PIN = int(os.getenv("VALVE_GPIO_PIN", "18"))  # Pin GPIO pour la vanne
VALVE_ACTIVE_HIGH = os.getenv("VALVE_ACTIVE_HIGH", "False").lower() == "true"

# --- Configuration Débitmètre ---
FLOW_CALIBRATION_FACTOR = float(os.getenv("FLOW_CALIBRATION_FACTOR", "6.5"))  # Impulsions/L
FLOW_GPIO_PIN = int(os.getenv("FLOW_GPIO_PIN", "23"))  # Pin GPIO pour le débitmètre

# --- Configuration Backend ---
BACKEND_HOST = os.getenv("BACKEND_HOST", "localhost")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
BACKEND_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/rfid/event/"
NETWORK_TIMEOUT = float(os.getenv("NETWORK_TIMEOUT", "5.0"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# --- Configuration Systemd ---
SYSTEMD_NOTIFY = os.getenv("SYSTEMD_NOTIFY", "False").lower() == "true"
