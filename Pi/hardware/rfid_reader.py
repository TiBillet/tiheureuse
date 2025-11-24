import time
import serial
from typing import Optional, Callable, Union
from utils.logger import logger
from config.settings import RFID_DEVICE, RFID_TIMEOUT
from utils.exceptions import RFIDReadError, RFIDInitError
from mfrc522 import SimpleMFRC522  # Bibliothèque pour RC522 (à installer via pip)

class RFIDReader:
    """
    Classe unifiée pour gérer les lecteurs RFID :
    - **RC522** (SPI/GPIO) : Pour les tags MIFARE (13.56MHz)
    - **VMA405** (UART) : Pour les tags 125kHz (compatible avec les badges bas coût)

    Features :
    - Détection automatique du type de lecteur
    - Anti-rebond logiciel/matériel
    - Callback sur détection de tag
    - Gestion des erreurs robuste
    - Compatible avec les deux protocoles
    """

    def __init__(self, device: str = RFID_DEVICE, timeout: float = RFID_TIMEOUT):
        self.device = device
        self.timeout = timeout
        self.last_tag = None
        self.last_read_time = 0
        self.debounce_delay = 0.5  # Délai anti-rebond en secondes
        self.callback = None
        self.reader_type = None  # "RC522" ou "VMA405"
        self.serial_conn = None  # Pour VMA405
        self.rc522_reader = None  # Pour RC522

    def initialize(self) -> bool:
        """
        Initialise le lecteur RFID en détectant automatiquement le type.
        Returns:
            bool: True si succès, False sinon.
        """
        try:
            # --- Détection du type de lecteur ---
            if self.device.startswith("/dev/"):
                # Cas 1 : Device série (VMA405)
                self.reader_type = "VMA405"
                self.serial_conn = serial.Serial(
                    port=self.device,
                    baudrate=9600,
                    timeout=self.timeout
                )
                logger.info(f"Lecteur VMA405 initialisé sur {self.device}")
            else:
                # Cas 2 : RC522 (SPI/GPIO)
                self.reader_type = "RC522"
                self.rc522_reader = SimpleMFRC522()
                logger.info("Lecteur RC522 initialisé (SPI/GPIO)")

            return True
        except serial.SerialException as e:
            logger.error(f"Erreur initialisation VMA405: {e}")
            raise RFIDInitError(f"Impossible d'initialiser {self.device}") from e
        except Exception as e:
            logger.error(f"Erreur initialisation RC522: {e}")
            raise RFIDInitError("Impossible d'initialiser le RC522") from e

    def read_tag(self) -> Optional[str]:
        """
        Lit un tag RFID selon le type de lecteur.
        Returns:
            str: UID du tag (hex pour RC522, décimal pour VMA405), ou None.
        """
        current_time = time.time()
        if current_time - self.last_read_time < self.debounce_delay:
            return None  # Anti-rebond

        try:
            if self.reader_type == "VMA405":
                return self._read_vma405()
            elif self.reader_type == "RC522":
                return self._read_rc522()
            else:
                raise RFIDReadError("Type de lecteur non défini")
        except Exception as e:
            logger.error(f"Erreur lecture RFID ({self.reader_type}): {e}")
            raise RFIDReadError(f"Erreur avec le lecteur {self.reader_type}") from e

    def _read_vma405(self) -> Optional[str]:
        """Lit un tag avec le VMA405 (UART)."""
        if not self.serial_conn or not self.serial_conn.is_open:
            raise RFIDReadError("Connexion série non initialisée")

        try:
            line = self.serial_conn.readline().decode('utf-8').strip()
            if not line:
                return None

            # Format typique VMA405 : "12345678\n" (UID décimal)
            tag_id = line.split()[0] if line else None
            if tag_id and tag_id != self.last_tag:
                self.last_tag = tag_id
                self.last_read_time = time.time()
                logger.debug(f"Tag VMA405 détecté: {tag_id}")
                return tag_id
            return None
        except (serial.SerialException, UnicodeDecodeError) as e:
            raise RFIDReadError("Erreur de lecture VMA405") from e

    def _read_rc522(self) -> Optional[str]:
        """
        Lit un tag avec le RC522 (SPI/GPIO).
        Returns:
            str: UID en hexadécimal (ex: "12345678"), ou None.
        """
        if not self.rc522_reader:
            raise RFIDReadError("RC522 non initialisé")

        try:
            # SimpleMFRC522.read() bloque jusqu'à détection d'un tag
            # On utilise read_id_no_block() pour une lecture non-bloquante
            status, tag_id = self.rc522_reader.read_no_block()
            if not status:
                return None  # Aucun tag détecté

            # Convertit l'UID en string hexadécimal
            uid_hex = "".join(f"{byte:02x}" for byte in tag_id)
            if uid_hex != self.last_tag:
                self.last_tag = uid_hex
                self.last_read_time = time.time()
                logger.debug(f"Tag RC522 détecté: {uid_hex}")
                return uid_hex
            return None
        except Exception as e:
            raise RFIDReadError("Erreur de lecture RC522") from e

    def set_callback(self, callback: Callable[[str], None]):
        """Définit une fonction de callback pour les tags lus."""
        self.callback = callback

    def loop(self):
        """Boucle de lecture continue avec callback (pour VMA405)."""
        if self.reader_type != "VMA405":
            logger.warning("La boucle continue n'est utile que pour VMA405 (RC522 est bloquant)")
            return

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
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Arrêt du RFID Reader")
                break

    def close(self):
        """Fermeture propre des ressources."""
        if self.reader_type == "VMA405" and self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            logger.info("Connexion VMA405 fermée")
        elif self.reader_type == "RC522" and self.rc522_reader:
            # Pas de méthode close() pour SimpleMFRC522, mais on peut nettoyer le GPIO
            import RPi.GPIO as GPIO
            GPIO.cleanup()
            logger.info("GPIO RC522 nettoyé")

