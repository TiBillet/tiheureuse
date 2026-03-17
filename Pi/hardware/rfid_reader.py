import os
import time
from dotenv import load_dotenv
from mfrc522 import MFRC522
from utils.logger import logger
from utils.exceptions import RFIDInitError, RFIDReadError
from utils.serial_tools import SerialReader

load_dotenv()


class RFIDReader:
    def __init__(self):
        # Récupération du type de lecteur depuis le fichier .env
        self.reader_type = os.getenv("RFID_TYPE", "RC522").upper()
        self.reader = None
        self.serial = None

        logger.info(f"Initialisation du lecteur RFID type: {self.reader_type}")

        if self.reader_type == "RC522":
            self._init_rc522()
        elif self.reader_type == "VMA405":
            self._init_vma405()
        else:
            logger.error(
                f"Type RFID inconnu: {self.reader_type}. Utilisez RC522 ou VMA405."
            )

    def _init_rc522(self):
        """Initialise le lecteur SPI RC522."""
        try:
            # device=0, spd=1000000 correspond au SPI0
            self.reader = MFRC522(device=0, spd=1000000)
            logger.info("Lecteur RC522 prêt.")
        except Exception as e:
            logger.error(f"Erreur init RC522: {e}")
            raise RFIDInitError(f"Impossible d'initialiser le RC522 : {e}") from e

    def _init_vma405(self):
        """Initialise le lecteur Série VMA405."""
        port = os.getenv("RFID_SERIAL_PORT", "/dev/ttyS0")
        baud = int(os.getenv("RFID_BAUDRATE", 9600))
        try:
            self.serial = SerialReader(port, baud)
            logger.info(f"Lecteur VMA405 prêt sur {port}")
        except Exception as e:
            logger.error(f"Erreur init VMA405: {e}")
            raise RFIDInitError(
                f"Impossible d'initialiser le VMA405 sur {port} : {e}"
            ) from e

    def read_uid(self):
        """Méthode unifiée pour lire un tag selon le type configuré."""
        if self.reader_type == "RC522":
            return self._read_rc522()
        elif self.reader_type == "VMA405":
            return self._read_vma405()
        return None

    def _read_rc522(self):
        """Lecture spécifique RC522."""
        try:
            # 1. Requête
            (status, TagType) = self.reader.MFRC522_Request(self.reader.PICC_REQIDL)

            if status == self.reader.MI_OK:
                # 2. Anticollision
                (status, uid) = self.reader.MFRC522_Anticoll()

                if status == self.reader.MI_OK:
                    return self._uid_to_hex(uid)
        except Exception as e:
            # On évite de spammer les logs sur des erreurs de lecture VIDES
            pass
        return None

    def _read_vma405(self):
        """Lecture spécifique VMA405 (UART) via SerialReader."""
        if not self.serial:
            return None
        try:
            # SerialReader.read_line() retourne une str déjà décodée et strippée, ou None
            uid_str = self.serial.read_line()
            return uid_str if uid_str else None
        except Exception as e:
            logger.error(f"Erreur lecture VMA405: {e}")
            raise RFIDReadError(f"Erreur lecture VMA405 : {e}") from e

    def _uid_to_hex(self, uid):
        """
        Convertit la liste d'entiers [116, 30, 204, 42, 140] en String '741ECC2A'.
        Gère le retrait du Checksum (5ème octet).
        """
        if not uid:
            return None

        # RC522 renvoie souvent 5 octets (4 octets UID + 1 octet Checksum)
        if len(uid) == 5:
            # On vérifie si c'est bien le checksum (XOR des 4 premiers)
            uid = uid[:4]

        # Formatage Hex Majuscule
        return "".join([f"{x:02X}" for x in uid])

    def cleanup(self):
        """Nettoyage des ressources."""
        if self.reader_type == "VMA405" and self.serial:
            self.serial.close()
        # MFRC522 gère son propre SPI, pas de cleanup critique nécessaire ici
        logger.info("Lecteur RFID nettoyé")
