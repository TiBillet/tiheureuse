from time import time

def _bytes_to_hex(bseq):
    return ''.join(f'{b:02X}' for b in bseq)

def _clean_uid_bytes(uid_bytes):
    """
    Enleve les BCC et les cascade tags (0x88).
    - Cas le plus courant (4B UID): uid_bytes = [b0,b1,b2,b3,BCC] -> renvoie b0..b3
    - Cas 7B/10B si on a plusieurs blocs: enleve cchaque BCC et tous les 0x88.
    """
    if not uid_bytes:
        return ''
    data = list(uid_bytes)

    # 1) Si on a des blocs de 5 octets (4 UID + 1 BCC), supprime chaque 5e
    cleaned = []
    if len(data) >= 5 and (len(data) % 5 == 0 or len(data) == 5):
        for i in range(0, len(data), 5):
            block = data[i:i+5]
            if len(block) >= 4:
                cleaned.extend(block[:4])  # garde 4 UID, jette BCC
            else:
                cleaned.extend(block)
    else:
        # sinon, si on a exactement 5 octets, enleve le dernier (probable BCC)
        cleaned = data[:-1] if len(data) == 5 else data

    # 2) Supprime tous les cascade tags (0x88)
    cleaned = [b for b in cleaned if b != 0x88]

    return _bytes_to_hex(cleaned)

class RFIDReader:
    """
    Lit l'UID (hex uppercase) SANS BCC, non-bloquant si possible.
    - Préfère mfrc522.SimpleMFRC522.read_id_no_block() si dispo (retourne int),
      sinon utilise MFRC522 bas-niveau et reconstitue l'UID en filtrant BCC/0x88.
    - Tente les anticollisions de niveaux supérieurs si la lib les propose.
    """
    def __init__(self):
        self.mode = None
        self.reader = None
        # 1) SimpleMFRC522
        try:
            from mfrc522 import SimpleMFRC522
            self.reader = SimpleMFRC522()
            # read_id_no_block présent sur plusieurs versions
            if hasattr(self.reader, 'read_id_no_block'):
                self.mode = 'simple_nb'
            else:
                self.mode = 'simple_block'  # on évitera, pour rester non-bloquant
            print("RFID: SimpleMFRC522, mode =", self.mode)
            return
        except Exception as e:
            print("RFID: SimpleMFRC522 non dispo:", e)

        # 2) Bas-niveau MFRC522
        try:
            import MFRC522  # type: ignore
            self.reader = MFRC522.MFRC522()
            self.mode = 'lowlevel'
            print("RFID: MFRC522 bas-niveau")
        except Exception as e:
            print("RFID init failed:", e)
            self.reader = None
            self.mode = None

    # ---------- PUBLIC ----------
    def read_uid_hex_nonblock(self):
        """
        Retourne l'UID 'HEX_SANS_BCC' ou None si pas de carte.
        Non-bloquant dans tous les modes supportés.
        """
        if not self.reader:
            return None

        if self.mode == 'simple_nb':
            # Retourne un entier (UID sans BCC) selon la lib SimpleMFRC522
            try:
                uid_int = self.reader.read_id_no_block()
                if not uid_int:
                    return None
                # Convertit l'entier en hex (padding pair), uppercase
                # (SimpleMFRC522 renvoie déjà un UID utile, sans BCC)
                hx = f'{uid_int:X}'
                if len(hx) % 2 == 1:
                    hx = '0' + hx
                bs = list(bytes.fromhex(hx))
                return _clean_uid_bytes(bs)
            except Exception:
                return None

        if self.mode == 'simple_block':
            # Pas de méthode non bloquante => on ne lit pas pour ne pas bloquer la boucle
            return None

        # --- Basse-niveau non bloquant ---
        try:
            rdr = self.reader
            (status, tag_type) = rdr.MFRC522_Request(rdr.PICC_REQIDL)
            if status != rdr.MI_OK:
                return None

            # Anticollision niveau 1
            (status, uid1) = rdr.MFRC522_Anticoll()
            if status != rdr.MI_OK or not uid1:
                return None

            # Sélectionner la carte (peut renseigner si cascade continue)
            try:
                (status_sel1, sak1) = rdr.MFRC522_SelectTag(uid1)
            except Exception:
                status_sel1, sak1 = (rdr.MI_OK, 0)  # si non dispo, on suppose OK

            ubytes = list(uid1)  # souvent 5 octets (4 UID + BCC) ; parfois 4
            # Si cascade bit (SAK & 0x04) => essayer CL2 si la lib l'expose
            more = False
            try:
                more = bool(sak1 & 0x04)
            except Exception:
                more = False

            if more:
                # certaines libs proposent MFRC522_Anticoll2 / SelectTag2
                if hasattr(rdr, 'MFRC522_Anticoll2'):
                    (status2, uid2) = rdr.MFRC522_Anticoll2()
                    if status2 == rdr.MI_OK and uid2:
                        ubytes.extend(list(uid2))
                        try:
                            if hasattr(rdr, 'MFRC522_SelectTag2'):
                                rdr.MFRC522_SelectTag2(uid2)
                        except Exception:
                            pass

                        # possible troisième niveau
                        try:
                            # si la lib donne SAK2, on peut vérifier un second cascade bit
                            (status_sel2, sak2) = (rdr.MI_OK, 0)
                            if hasattr(rdr, 'MFRC522_SelectTag2'):
                                status_sel2, sak2 = rdr.MFRC522_SelectTag2(uid2)
                            if (sak2 & 0x04) and hasattr(rdr, 'MFRC522_Anticoll3'):
                                (status3, uid3) = rdr.MFRC522_Anticoll3()
                                if status3 == rdr.MI_OK and uid3:
                                    ubytes.extend(list(uid3))
                        except Exception:
                            pass
                # si pas de méthodes *2/*3, on se contente du niveau 1

            # Nettoie BCC et cascade tags, renvoie HEX uppercase
            return _clean_uid_bytes(ubytes)

        except Exception:
            return None

    def read_uid_hex_block(self, timeout_s=5.0, poll_period_s=0.05):
        """
        Bloquant avec timeout: poll en non-bloquant jusqu'à avoir un UID.
        """
        t0 = time.monotonic()
        while (time.monotonic() - t0) < timeout_s:
            hx = self.read_uid_hex_nonblock()
            if hx:
                return hx
            time.sleep(poll_period_s)
        return None