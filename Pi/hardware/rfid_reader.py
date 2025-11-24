import time
import serial
from typing import Optional, Callable
from utils.logger import logger
from config.settings import RFID_DEVICE, RFID_TIMEOUT
from utils.exceptions import RFIDReadError, RFIDInitError

class RFIDReader:
    """
    Classe pour gérer la lecture des tags RFID via :
    - RC522 (SPI) ou VMA405 (série).
    Supports :
    - Anti-rebond (débounce)
    - Callback sur détection de tag
    - Gestion des erreurs
    """

    def __init__(self, device: str = RFID_DEVICE, timeout: float = RFID_TIMEOUT):
        self.device = device
        self.timeout = timeout
        self.last_tag = None
        self.last_read_time = 0
        self.debounce_delay = 0.5  # Délai anti-rebond en secondes
        self.callback = None
        self.serial_conn = None

    def initialize(self) -> bool:
        """Initialise la connexion série pour le VMA405."""
        try:
            self.serial_conn = serial.Serial(
                port=self.device,
                baudrate=9600,
                timeout=self.timeout
            )
            logger.info(f"RFID Reader initialisé sur {self.device}")
            return True
        except serial.SerialException as e:
            logger.error(f"Erreur initialisation RFID: {e}")
            raise RFIDInitError(f"Impossible d'initialiser {self.device}") from e

    def read_tag(self) -> Optional[str]:
        """Lit un tag RFID avec gestion d'erreur et anti-rebond."""
        if not self.serial_conn or not self.serial_conn.is_open:
            raise RFIDInitError("Connexion RFID non initialisée")

        try:
            current_time = time.time()
            if current_time - self.last_read_time < self.debounce_delay:
                return None  # Anti-rebond

            line = self.serial_conn.readline().decode('utf-8').strip()
            if not line:
                return None

            tag_id = line.split()[0] if line else None  # Extrait l'UID
            if tag_id and tag_id != self.last_tag:
                self.last_tag = tag_id
                self.last_read_time = current_time
                logger.info(f"Tag RFID détecté: {tag_id}")
                return tag_id
            return None
        except (serial.SerialException, UnicodeDecodeError) as e:
            logger.error(f"Erreur lecture RFID: {e}")
            raise RFIDReadError("Erreur de lecture du tag RFID") from e

    def set_callback(self, callback: Callable[[str], None]):
        """Définit une fonction de callback pour les tags lus."""
        self.callback = callback

    def loop(self):
        """Boucle de lecture continue avec callback."""
        if not self.callback:
            logger.warning("Aucun callback défini pour le RFID Reader")
            return

        while True:
            try:
                tag = self.read_tag()
                if tag:
                    self.callback(tag)
            except RFIDReadError as e:
                logger.error(f"Erreur dans la boucle RFID: {e}")
                time.sleep(1)  # Attend avant de réessayer
            except KeyboardInterrupt:
                logger.info("Arrêt du RFID Reader")
                break

    def close(self):
        """Fermeture propre de la connexion."""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            logger.info("Connexion RFID fermée")
