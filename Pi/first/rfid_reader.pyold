# rfid_reader.py
# -*- coding: utf-8 -*-
"""
Lecteur MFRC522 unifié.
- Tente d'abord SimpleMFRC522 (non-bloquant si disponible).
- Sinon bas niveau MFRC522 avec anticollision et nettoyage UID (sans BCC ni 0x88).
Retourne des UIDs en HEX UPPERCASE (str) ou None si aucune carte.
"""

from __future__ import annotations
from typing import Optional, List
import time
import logging

log = logging.getLogger(__name__)


# --- Exceptions dédiées -------------------------------------------------------
class RFIDInitError(RuntimeError):
    """Impossible d'initialiser un backend MFRC522."""


class RFIDReadError(RuntimeError):
    """Erreur inattendue lors de la lecture du lecteur."""

# --- Helpers internes (non exportés) ------------------------------------------
def _bytes_to_hex(bseq: List[int]) -> str:
    """Transforme une séquence d'octets (0..255) en HEX uppercase sans séparateur."""
    return ''.join(f'{b:02X}' for b in bseq)

def _clean_uid_bytes(uid_bytes: List[int]) -> str:
    """
    Enlève les BCC (chaque 5ᵉ octet d'un bloc 4 UID + 1 BCC) et les cascade tags (0x88).
    Conserve l'ordre des blocs (CL1 → CL2 → CL3).
    Renvoie une chaîne HEX uppercase.
    """
    if not uid_bytes:
        return ''

    data = list(uid_bytes)  # copie
    cleaned: List[int] = []

    # cas classique : blocs de 5 (4 UID + 1 BCC)
    if len(data) >= 5 and (len(data) % 5 == 0 or len(data) == 5):
        for i in range(0, len(data), 5):
            block = data[i:i + 5]
            if len(block) >= 4:
                cleaned.extend(block[:4])  # jette BCC
            else:
                cleaned.extend(block)
    else:
        # fallback : si exactement 5 octets supposés (4 UID + BCC), on jette le dernier
        cleaned = data[:-1] if len(data) == 5 else data

    # retire tous les cascade tags
    cleaned = [b for b in cleaned if b != 0x88]
    return _bytes_to_hex(cleaned)

# --- Implémentation -----------------------------------------------------------
class RFIDReader:
    """
    Wrapper unifié pour MFRC522.
    Attributs:
      - mode: 'simple_nb' | 'simple_block' | 'lowlevel' | None
      - is_ready: bool (lecteur présent)
    Méthodes:
      - read_uid_hex_nonblock() -> Optional[str]
      - read_uid_hex_block(timeout_s=5.0, poll_period_s=0.05) -> Optional[str]
      - close() -> None
    """

    def __init__(self) -> None:
        self.mode: Optional[str] = None
        self.reader = None

        # SimpleMFRC522
        try:
            from mfrc522 import SimpleMFRC522
            self.reader = SimpleMFRC522()
            self.mode = 'simple_nb' if hasattr(self.reader, 'read_id_no_block') else 'simple_block'
            log.info("RFID backend: SimpleMFRC522 [%s]", self.mode)
            return
        except Exception as e:
            log.debug("SimpleMFRC522 indisponible: %s", e, exc_info=False)

        # Bas-niveau MFRC522
        try:
            import MFRC522
            self.reader = MFRC522.MFRC522()
            self.mode = 'lowlevel'
            log.info("RFID backend: MFRC522 (bas-niveau)")
        except Exception as e:
            self.reader = None
            self.mode = None
            raise RFIDInitError(
                "Aucun backend MFRC522 disponible. Vérifiez SPI, le câblage et les dépendances "
                "(mfrc522 / MFRC522)."
            ) from e

    # ---------- API publique ----------
    @property
    def is_ready(self) -> bool:
        return self.reader is not None

    def read_uid_hex_nonblock(self) -> Optional[str]:
        """
        Lecture non bloquante.
        Retourne UID HEX uppercase (sans BCC/0x88) ou None si pas de carte.
        """
        if not self.reader:
            return None

        # Simple (non-bloquant fourni par la lib)
        if self.mode == 'simple_nb':
            try:
                uid_int = self.reader.read_id_no_block()
                if not uid_int:
                    return None
                hx = f'{uid_int:X}'
                if len(hx) % 2 == 1:
                    hx = '0' + hx
                bs = list(bytes.fromhex(hx))
                return _clean_uid_bytes(bs)
            except Exception:
                return None

        # SimpleMFRC522 sans non-blocking : on n'essaie pas de lire pour ne pas bloquer
        if self.mode == 'simple_block':
            return None

        # Bas-niveau MFRC522
        try:
            rdr = self.reader
            (status, _tag_type) = rdr.MFRC522_Request(rdr.PICC_REQIDL)
            if status != rdr.MI_OK:
                return None

            # anticollision niveau 1
            (status, uid1) = rdr.MFRC522_Anticoll()
            if status != rdr.MI_OK or not uid1:
                return None

            # select 1
            try:
                (status_sel1, sak1) = rdr.MFRC522_SelectTag(uid1)
            except Exception:
                status_sel1, sak1 = (rdr.MI_OK, 0)

            ubytes: List[int] = list(uid1)

            # cascade suivant si possible
            more = False
            try:
                more = bool(sak1 & 0x04)
            except Exception:
                more = False

            if more and hasattr(rdr, 'MFRC522_Anticoll2'):
                (st2, uid2) = rdr.MFRC522_Anticoll2()
                if st2 == rdr.MI_OK and uid2:
                    ubytes.extend(list(uid2))
                    # éventuel troisième niveau
                    if hasattr(rdr, 'MFRC522_SelectTag2'):
                        try:
                            _stsel2, sak2 = rdr.MFRC522_SelectTag2(uid2)
                            if (sak2 & 0x04) and hasattr(rdr, 'MFRC522_Anticoll3'):
                                (st3, uid3) = rdr.MFRC522_Anticoll3()
                                if st3 == rdr.MI_OK and uid3:
                                    ubytes.extend(list(uid3))
                        except Exception:
                            pass

            return _clean_uid_bytes(ubytes)

        except Exception:
            # Sur toute erreur inattendue, on renvoie None
            return None

    def read_uid_hex_block(self, timeout_s: float = 5.0, poll_period_s: float = 0.05) -> Optional[str]:
        """
        Poll non-bloquant jusqu'à 'timeout_s'. Renvoie UID HEX ou None.
        """
        t0 = time.monotonic()
        while (time.monotonic() - t0) < timeout_s:
            uid = self.read_uid_hex_nonblock()
            if uid:
                return uid
            time.sleep(poll_period_s)
        return None

    def close(self) -> None:
        """
        Libère les ressources du backend si celui-ci le propose.
        (SimpleMFRC522/MFRC522 n'ont pas toujours d'API close explicite.)
        """
        try:
            if self.reader and hasattr(self.reader, 'GPIO'):
                # certaines implémentations exposent GPIO.cleanup
                try:
                    self.reader.GPIO.cleanup()  # type: ignore[attr-defined]
                except Exception:
                    pass
        finally:
            self.reader = None
            self.mode = None
