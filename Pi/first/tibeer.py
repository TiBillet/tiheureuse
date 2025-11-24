#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent pour tireuse à bière connectée (TiBeer).
Fonctionnalités :
- Contrôle GPIO (vanne + débitmètre à effet Hall)
- Authentification RFID (RC522 ou VMA405)
- Envoi d'événements vers un backend Django
- API Flask pour le contrôle local
- Logs détaillés et gestion des erreurs
"""

import os
import time
import logging
from logging.handlers import RotatingFileHandler
import sys
import json
import threading
import requests
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
import pigpio
import serial
from enum import Enum, auto
from flask import Flask, jsonify, request, abort

# ======================
# CONFIGURATION DES LOGS
# ======================
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            '/var/log/tibeer.log',
            maxBytes=5*1024*1024,
            backupCount=3
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ======================
# CONFIGURATION GLOBALE
# ======================
# Variables d'environnement (à définir dans /etc/environment ou un fichier .env)
TIREUSE_BEC_ID = os.getenv("TIREUSE_BEC_ID", "tireuse1")
LIQUID_LABEL = os.getenv("LIQUID_LABEL", "Bière Blonde")
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:5000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000/api/rfid/event/")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "changeme")
GPIO_VANNE = int(os.getenv("GPIO_VANNE", "18"))  # GPIO pour la vanne
GPIO_FLOW_SENSOR = int(os.getenv("GPIO_FLOW_SENSOR", "23"))  # GPIO pour le débitmètre

try:
    FLOW_CALIBRATION_FACTOR = float(os.getenv("FLOW_CALIBRATION_FACTOR", "6.5"))
except ValueError as e:
    logger.error(f"Erreur de conversion pour FLOW_CALIBRATION_FACTOR: {e}")
    logger.error("Utilisation de la valeur par défaut (6.5)")
    FLOW_CALIBRATION_FACTOR = 6.5  # Valeur par défaut

PUSH_PERIOD_S = int(os.getenv("PUSH_PERIOD_S", "10"))  # Période d'envoi des événements (secondes)
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "50"))  # Nombre max d'événements par batch

# Configuration du lecteur RFID (à adapter matériel)
RFID_TYPE = os.getenv("RFID_TYPE", "RC522")  # "RC522" ou "VMA405"
RFID_CONFIG = {
    "RC522": {
        "spi_channel": 0,
        "spi_speed": 1000000,
        "gpio_rst": 25,
        "anti_bounce_ms": 500,
    },
    "VMA405": {
        "port": "/dev/ttyS0",
        "baudrate": 9600,
        "timeout": 0.5,
        "anti_bounce_ms": 500,
    }
}[RFID_TYPE]

# ======================
# CONFIGURATION DES LOGS
# ======================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler('tibeer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ======================
# STRUCTURES DE DONNÉES
# ======================
@dataclass
class Event:
    """Représente un événement de la tireuse."""
    uid: Optional[str]  # UID de la carte RFID (ou None)
    present: bool       # Carte présente ?
    authorized: bool     # Accès autorisé ?
    vanne_ouverte: bool  # Vanne ouverte ?
    volume_ml: float    # Volume cumulé (ml)
    debit_l_min: float   # Débit instantané (L/min)
    message: str         # Message associées
    session_done: bool = False  # Session terminée ?
    session_volume_ml: float = 0.0  # Volume total de la session

    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'événement en dictionnaire pour JSON."""
        return asdict(self)

# ======================
# LECTEUR RFID (Intégré)
# ======================
class RFIDType(Enum):
    RC522 = auto()
    VMA405 = auto()

class RFIDError(Exception):
    pass

class BaseRFIDReader:
    def __init__(self, config: dict):
        self.config = config
        self.last_uid = None
        self.last_uid_time = 0
        self._initialized = False

    def initialize(self) -> bool:
        raise NotImplementedError

    def read_uid_hex(self) -> Optional[str]:
        raise NotImplementedError

    def read_uid_hex_nonblock(self) -> Optional[str]:
        now = time.monotonic()
        try:
            uid = self.read_uid_hex()
            if uid is None:
                return None

            if uid == self.last_uid and (now - self.last_uid_time) < (self.config["anti_bounce_ms"] / 1000.0):
                return None

            self.last_uid = uid
            self.last_uid_time = now
            return uid
        except Exception as e:
            logger.warning(f"Erreur RFID: {e}")
            return None

    def close(self):
        self._initialized = False

class RC522Reader(BaseRFIDReader):
    def __init__(self, config: dict):
        super().__init__(config)
        self.pi = None
        self.spi_handle = None
        self.gpio_rst = config["gpio_rst"]

    def initialize(self) -> bool:
        try:
            self.pi = pigpio.pi()
            if not self.pi.connected:
                raise RFIDError("pigpiod non connecté. Lancez-le avec `sudo pigpiod`.")

            self.pi.set_mode(self.gpio_rst, pigpio.OUTPUT)
            self.pi.write(self.gpio_rst, 0)
            time.sleep(0.1)
            self.pi.write(self.gpio_rst, 1)
            time.sleep(0.1)

            self.spi_handle = self.pi.spi_open(self.config["spi_channel"], self.config["spi_speed"])
            if self.spi_handle < 0:
                raise RFIDError("Échec SPI")

            # Initialisation basique du RC522
            self._write_register(0x11, 0x3D)  # ModeReg: CRC + no force TX
            self._initialized = True
            logger.info("RC522 initialisé")
            return True
        except Exception as e:
            logger.error(f"Init RC522 échouée: {e}")
            self.close()
            return False

    def _write_register(self, reg: int, value: int):
        self.pi.spi_xfer(self.spi_handle, [(reg << 1) & 0x7E, value])

    def _read_register(self, reg: int) -> int:
        (_, data) = self.pi.spi_xfer(self.spi_handle, [((reg << 1) & 0x7E) | 0x80, 0])
        return data[0]

    def read_uid_hex(self) -> Optional[str]:
        try:
            # Réveil de la carte (simplifié)
            self._write_register(0x0D, 0x07)  # BitFramingReg pour REQA
            self.pi.spi_xfer(self.spi_handle, [0x26])  # REQA
            time.sleep(0.05)

            # Anti-collision (simplifié)
            self._write_register(0x0D, 0x00)
            success, uid = self._to_card(0x0C, b'\x93\x20')
            if success and len(uid) == 5:
                uid_hex = "".join(f"{b:02x}" for b in uid[:4])
                return uid_hex.upper()
            return None
        except Exception as e:
            logger.warning(f"Erreur lecture RC522: {e}")
            return None

    def _to_card(self, command: int, data: bytes) -> tuple:
        # Implémentation simplifiée (version complète dans le fichier rfid_reader.py)
        try:
            self._write_register(0x01, command)
            self.pi.spi_xfer(self.spi_handle, list(data))
            time.sleep(0.02)
            n = self._read_register(0x0A)  # FIFOLevelReg
            if n > 0:
                response = self.pi.spi_xfer(self.spi_handle, [0x09 | 0x80] + [0] * n)[1]
                return True, bytes(response)
            return False, b""
        except Exception:
            return False, b""

    def close(self):
        if self.spi_handle is not None:
            self.pi.spi_close(self.spi_handle)
        if self.pi is not None:
            self.pi.stop()

class VMA405Reader(BaseRFIDReader):
    def __init__(self, config: dict):
        super().__init__(config)
        self.serial_port = None

    def initialize(self) -> bool:
        try:
            self.serial_port = serial.Serial(
                port=self.config["port"],
                baudrate=self.config["baudrate"],
                timeout=self.config["timeout"]
            )
            self._initialized = True
            logger.info(f"VMA405 initialisé sur {self.config['port']}")
            return True
        except Exception as e:
            logger.error(f"Init VMA405 échouée: {e}")
            return False

    def read_uid_hex(self) -> Optional[str]:
        try:
            self.serial_port.write(b'\xAA\x00\x01\x00\x00\xBB')  # Commande "lire UID"
            time.sleep(0.1)
            response = self.serial_port.read_all()
            if len(response) >= 10 and response[0] == 0xAA and response[-1] == 0xBB:
                uid_data = response[3:7]  # Exemple: 4 bytes d'UID
                uid_hex = "".join(f"{b:02x}" for b in uid_data)
                return uid_hex.upper()
            return None
        except Exception as e:
            logger.warning(f"Erreur lecture VMA405: {e}")
            return None

    def close(self):
        if self.serial_port:
            self.serial_port.close()

def create_rfid_reader() -> BaseRFIDReader:
    reader_class = RC522Reader if RFID_TYPE == "RC522" else VMA405Reader
    reader = reader_class(RFID_CONFIG)
    if not reader.initialize():
        raise RFIDError("Initialisation du lecteur RFID échouée")
    return reader

# ======================
# GESTION DE LA TIREUSE
# ======================
class TireuseController:
    def __init__(self):
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("pigpiod non connecté")

        # Configuration GPIO
        self.pi.set_mode(GPIO_VANNE, pigpio.OUTPUT)
        self.pi.set_mode(GPIO_FLOW_SENSOR, pigpio.INPUT)
        self.pi.set_pull_up_down(GPIO_FLOW_SENSOR, pigpio.PUD_UP)

        # Variables d'état
        self.vanne_ouverte = False
        self.volume_total_ml = 0.0
        self.debit_actuel_l_min = 0.0
        self.last_flow_time = time.monotonic()
        self.flow_count = 0
        self.current_uid = None
        self.session_volume_ml = 0.0
        self.events_batch: List[Event] = []
        self.lock = threading.Lock()
        self.rfid_reader = create_rfid_reader()
        self.running = False
        self.thread = None

        # Démarre le thread de monitoring
        self.start()

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()
            logger.info("Démarrage du contrôleur de tireuse")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        self._close_vanne()
        self.rfid_reader.close()
        self.pi.stop()
        logger.info("Arrêt du contrôleur de tireuse")

    def _close_vanne(self):
        with self.lock:
            if self.vanne_ouverte:
                self.pi.write(GPIO_VANNE, 0)
                self.vanne_ouverte = False
                logger.info("Vanne fermée")

    def _open_vanne(self):
        with self.lock:
            if not self.vanne_ouverte:
                self.pi.write(GPIO_VANNE, 1)
                self.vanne_ouverte = True
                logger.info("Vanne ouverte")

    def _flow_sensor_callback(self, gpio, level, tick):
        """Callback pour le débitmètre (compte les impulsions)."""
        if level == 0:  # Front descendant
            with self.lock:
                self.flow_count += 1

    def _calculate_debit(self):
        """Calcule le débit instantané en L/min."""
        now = time.monotonic()
        delta_t = now - self.last_flow_time
        if delta_t > 1.0:  # Met à jour toutes les secondes
            with self.lock:
                frequency = self.flow_count / delta_t  # Impulsions par seconde
                self.debit_actuel_l_min = (frequency / FLOW_CALIBRATION_FACTOR) * 60  # Conversion en L/min
                self.flow_count = 0
                self.last_flow_time = now

    def _monitor_loop(self):
        """Boucle principale de monitoring."""
        # Configure le callback pour le débitmètre
        self.cb = self.pi.callback(GPIO_FLOW_SENSOR, pigpio.FALLING_EDGE, self._flow_sensor_callback)

        last_rfid_check = 0
        last_event_flush = 0
        last_debit_calc = 0

        while self.running:
            try:
                now = time.monotonic()

                # 1. Vérifie le lecteur RFID (toutes les 200ms)
                if now - last_rfid_check > 0.2:
                    uid = self.rfid_reader.read_uid_hex_nonblock()
                    if uid:
                        if uid != self.current_uid:
                            logger.info(f"Nouvelle carte détectée: {uid}")
                            self.current_uid = uid
                            # Ici, vous pourriez vérifier l'autorisation via le backend
                            # Pour l'exemple, on autorise toujours
                            self._open_vanne()
                            self.session_volume_ml = 0.0
                        else:
                            # Même carte : met à jour la session
                            pass
                    else:
                        if self.current_uid is not None:
                            logger.info(f"Carte {self.current_uid} retirée")
                            self._close_vanne()
                            self.current_uid = None
                    last_rfid_check = now

                # 2. Calcule le débit (toutes les secondes)
                if now - last_debit_calc > 1.0:
                    self._calculate_debit()
                    last_debit_calc = now

                # 3. Génère un événement périodique
                with self.lock:
                    event = Event(
                        uid=self.current_uid,
                        present=self.current_uid is not None,
                        authorized=True,  # À remplacer par une vraie vérif
                        vanne_ouverte=self.vanne_ouverte,
                        volume_ml=self.volume_total_ml,
                        debit_l_min=self.debit_actuel_l_min,
                        message=f"Débit actuel: {self.debit_actuel_l_min:.2f} L/min",
                        session_volume_ml=self.session_volume_ml
                    )
                    self.events_batch.append(event)

                # 4. Envoie les événements au backend (périodiquement)
                if now - last_event_flush > PUSH_PERIOD_S or len(self.events_batch) >= MAX_BATCH_SIZE:
                    self._flush_events()
                    last_event_flush = now

                time.sleep(0.05)  # Réduit la charge CPU

            except Exception as e:
                logger.error(f"Erreur dans la boucle de monitoring: {e}")
                time.sleep(1)

    def _flush_events(self):
        """Envoie les événements en attente au backend."""
        if not self.events_batch:
            return

        with self.lock:
            batch = self.events_batch.copy()
            self.events_batch.clear()

        try:
            payload = {
                "tireuse_bec": TIREUSE_BEC_ID,
                "liquid_label": LIQUID_LABEL,
                "agent_base_url": AGENT_BASE_URL,
                "events": [e.to_dict() for e in batch]
            }

            # Log du payload envoyé (pour débogage)
            logger.debug(f"Envoi de {len(batch)} événements au backend:")
            logger.debug(json.dumps(payload, indent=2))

            headers = {"X-API-Key": BACKEND_API_KEY}
            response = requests.post(
                BACKEND_URL,
                json=payload,
                headers=headers,
                timeout=5.0
            )

            # Log de la réponse
            logger.debug(f"Réponse du backend: {response.status_code} {response.reason}")
            if response.status_code != 200:
                logger.error(f"Échec de l'envoi des événements: {response.text}")
                # Re-met les événements dans la file en cas d'échec
                with self.lock:
                    self.events_batch.extend(batch)
            else:
                logger.info(f"{len(batch)} événements envoyés avec succès")

        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau lors de l'envoi des événements: {e}")
            # Re-met les événements dans la file
            with self.lock:
                self.events_batch.extend(batch)

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état actuel de la tireuse."""
        with self.lock:
            return {
                "vanne_ouverte": self.vanne_ouverte,
                "volume_total_ml": self.volume_total_ml,
                "debit_l_min": self.debit_actuel_l_min,
                "current_uid": self.current_uid,
                "session_volume_ml": self.session_volume_ml,
                "events_pending": len(self.events_batch)
            }

# ======================
# API FLASK (Contrôle local)
# ======================
app = Flask(__name__)
tireuse = None

@app.route('/status', methods=['GET'])
def get_status():
    if not tireuse:
        abort(503, description="Tireuse non initialisée")
    return jsonify(tireuse.get_status())

@app.route('/open', methods=['POST'])
def open_valve():
    if not tireuse:
        abort(503, description="Tireuse non initialisée")
    tireuse._open_vanne()
    return jsonify({"status": "success", "vanne_ouverte": True})

@app.route('/close', methods=['POST'])
def close_valve():
    if not tireuse:
        abort(503, description="Tireuse non initialisée")
    tireuse._close_vanne()
    return jsonify({"status": "success", "vanne_ouverte": False})

@app.route('/flush', methods=['POST'])
def flush_events():
    if not tireuse:
        abort(503, description="Tireuse non initialisée")
    tireuse._flush_events()
    return jsonify({"status": "success"})

# ======================
# POINT D'ENTRÉE
# ======================
def main():
    global tireuse
    try:
        logger.info("Démarrage de TiBeer Agent...")
        tireuse = TireuseController()

        # Démarre l'API Flask dans un thread séparé
        def run_flask():
            app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("API Flask démarrée sur http://0.0.0.0:5000")

        # Boucle principale (pour un éventuel futur code)
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Arrêt demandé par l'utilisateur...")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
    finally:
        if tireuse:
            tireuse.stop()
        logger.info("TiBeer Agent arrêté.")

if __name__ == "__main__":
    main()
