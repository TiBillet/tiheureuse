#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, threading, atexit, requests, re
from functools import wraps
from flask import Flask, request, jsonify
from gpiozero import OutputDevice, Button
from threading import Lock
import RPi.GPIO as GPIO


# ========= CONFIG =========
PIN_VANNE = 18        # sortie vers MOSFET/relais (LED en test)
PIN_DEBIT = 23        # ( Entree debimetre)
FACTEUR_CALIBRATION = 10.0 # defaut 450.0-- 100.0 pour interrupteur 1 appui=10ml
BOUNCE_MS = 50 # defaut 2 --50 a 80 pour interrupteur
LISSAGE_SECONDES = 1.0
TIMEOUT_SEC = 120

## inutile
#def _clean_base(url: str) -> str:
#    # enlève toute query et le slash final
#    return (url or "").split("?", 1)[0].rstrip("/")
##
#DJANGO_BASE_URL = _clean_base(os.environ.get("DJANGO_BASE_URL", "http://192.168.1.10:8000"))
DJANGO_BASE_URL = os.environ.get("DJANGO_BASE_URL", "http://192.168.1.10:8000")
DJANGO_AUTH_URL = f"{DJANGO_BASE_URL}/api/rfid/authorize"
DJANGO_EVENT_URL = f"{DJANGO_BASE_URL}/api/rfid/event"

#DJANGO_BASE_URL = os.environ.get("DJANGO_BASE_URL", "http://192.168.1.10:8000")
#DJANGO_EVENT_URL = os.environ.get("DJANGO_EVENT_URL", f"{DJANGO_BASE_URL}/api/rfid/event")

TIREUSE_BEC_ID = os.environ.get("TIREUSE_BEC_ID",'Soft1')
LIQUID_LABEL = os.environ.get("LIQUID_LABEL", 'Limo')
AGENT_SHARED_KEY = os.environ.get("AGENT_SHARED_KEY", "changeme")

def push_event(payload: dict):
    base = {
        "tireuse_bec": TIREUSE_BEC_ID,
        "liquid_label": LIQUID_LABEL,
    }
    out = {**payload, **base}
    try:
#        import requests
        requests.post(
            DJANGO_EVENT_URL,
            json=out,
            headers={"X-API-Key": AGENT_SHARED_KEY},
            timeout=2.0
        )
    except Exception:
        pass

# RFID
RFID_ENABLE = True
RFID_PRESENCE_GRACE_MS = 300      # tolérance entre lectures avant de considérer la carte "partie"
RFID_MIN_OPEN_MS = 150            # anti-clignotement: garde ouvert min 150ms
RFID_AUTH_CACHE_SEC = 2.0         # cache résultat d'auth pour limiter les appels à Django


HOST="0.0.0.0"; PORT=5000
# ==========================


# ---- Helpers GPIO ----
#GPIO.setmode(GPIO.BCM)
#GPIO.setup(PIN_VANNE, GPIO.OUT, initial=GPIO.LOW)
#GPIO.setup(PIN_DEBIT, GPIO.IN, pull_up_down=GPIO.PUD_UP)
print("[AUTH URL]", DJANGO_BASE_URL)
print("[EVENT URL]", DJANGO_EVENT_URL)


# ---- Vanne ----
class Vanne:
    def __init__(self,
                 pin: int = PIN_VANNE,
                 active_high: bool = True,
                 min_open_ms: int = RFID_MIN_OPEN_MS):
        # active_high=True  -> .on() met la broche à 1 (relais actif au niveau haut)
        # active_high=False -> .on() met la broche à 0  (relais actif au niveau bas)
        self.relay = OutputDevice(pin, active_high=active_high, initial_value=False)
        self._o = False
        self._last_open_ts = 0.0
        self._min_open_s = min_open_ms / 1000.0
        self.lock = threading.Lock()

    def ouvrir(self):
        with self.lock:
            if not self._o:
                self.relay.on()                 # équivalent GPIO.HIGH si active_high=True
                self._o = True
                self._last_open_ts = time.monotonic()
        compteur.enable()

    def fermer(self):
        with self.lock:
            if self._o:
                # Respecte la durée minimale d'ouverture pour épargner le relais / la vanne
                elapsed = time.monotonic() - self._last_open_ts
                if elapsed < self._min_open_s:
                    time.sleep(self._min_open_s - elapsed)
                self.relay.off()                # équivalent GPIO.LOW si active_high=True
                self._o = False
        compteur.disable()

    def est_ouverte(self) -> bool:
        with self.lock:
            return self._o

    def close(self):
        # Arrêt propre : met à OFF et libère la ressource gpiozero
        with self.lock:
            self.relay.off()
            self.relay.close()

vanne = Vanne()
atexit.register(vanne.close)

# ---- Debit ----
class CompteurDebit:
#    def __init__(self, k, facteur_calibration: float):
    def __init__(self, k: float, facteur_calibration: float, pull_up: bool = True, bounce_ms: int = BOUNCE_MS):
        self.k=k
        self.facteur = facteur_calibration
        self._count = 0
        self.lock = Lock()
        self.t=[]
        self.n=0
        self._sensor = Button(PIN_DEBIT, pull_up=True, bounce_time=BOUNCE_MS/1000)

        # callback sur impulsion
        self._sensor.when_pressed = self._pulse


    def enable(self):
        with self.lock:
            self.enabled = True

    def disable(self):
        with self.lock:
            self.enabled = False

    def _pulse(self, ch):
       # ignore si la vanne est fermee
        if not self.enabled:
            return
        now=time.monotonic()
        with self.lock:
            self.n+=1; self.t.append(now)
            seuil=now - max(1.5*LISSAGE_SECONDES,1.0)
            while self.t and self.t[0]<seuil: self.t.pop(0)
    def reset(self):
        with self.lock: self.n=0; self.t.clear()
    def volume_l(self):
        with self.lock: return self.n/self.k
    def debit_l_min(self):
        now=time.monotonic()
        with self.lock:
            seuil=now - max(1.5*LISSAGE_SECONDES,1.0)
            while self.t and self.t[0]<seuil: self.t.pop(0)
            if len(self.t)<2: return 0.0
            dt=(self.t[-1]-self.t[0]) or 1e-6
            freq=(len(self.t)-1)/dt
        return (freq/self.k)*60.0

compteur = CompteurDebit(k=0.1,facteur_calibration=FACTEUR_CALIBRATION)

# ---- RFID Reader (MFRC522) ----
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

rfid = RFIDReader()

# ---- Auth cache vers Django ----
_auth_cache = {}  # uid -> (expires_monotonic, bool)
def is_authorized(uid_hex: str) -> bool:
    now = time.monotonic()
    ent = _auth_cache.get(uid_hex)
    if ent and ent[0] > now:
        _, cached_ok, cached_allowed = ent
        return (cached_ok and cached_allowed > 0.0), cached_allowed
    try:
        r = requests.get(
            DJANGO_AUTH_URL,
            params={"uid": uid_hex, "tireuse_bec": TIREUSE_BEC_ID},
            headers={"X-API-Key": AGENT_SHARED_KEY},
            timeout=2.0,
        )

## log de UID et reponse
#        ok = r.ok and bool(r.json().get("authorized", False))
#        reason = (r.json().get("reason") if r.ok else r.status_code)
#        print(f"[AUTH] uid={uid_hex} -> authorized={ok} reason={reason} tireuse={TIREUSE_BEC_ID}")
        if not r.ok:
            print(f"[AUTH] HTTP {r.status_code} pour uid={uid_hex}")
            ok = False
            allowed_ml = 0.0
        else:
            j = r.json()
            ok = bool(j.get("authorized", False))
        # allowed_ml = balance * unit_ml côté serveur (peut être 0)
            allowed_ml = float(j.get("allowed_ml") or 0.0)
            enough = bool(j.get("enough_funds", allowed_ml > 0.0))
            print(
                f"[AUTH] uid={uid_hex} -> authorized={ok} "
                f"allowed_ml={allowed_ml:.1f} {j.get('unit_label', '') or ''}"
            )
            ok = ok and enough  # on exige aussi des fonds suffisant

    except Exception as e:
        print(f"[AUTH] uid={uid_hex} ERROR {e}")
        ok = False
        allowed_ml = 0.0
    _auth_cache[uid_hex] = (now + RFID_AUTH_CACHE_SEC, ok, allowed_ml)
    return ok and allowed_ml > 0.0, allowed_ml

# ---- RFID loop: ouvre tant que carte autorisee presente ----
last_seen_ts = 0.0
_message = ""
authorized_current = False
current_uid = None
session_allowed_ml = 0.0
session_base_ml = 0.0


def _short(uid_hex: str, keep=8):
    # pour logs/UI: garde les 8 premiers hex 
    return uid_hex[:keep] + ("..." if len(uid_hex) > keep else "")



def rfid_loop():
    global current_uid, authorized_current, last_seen_ts, _message
    if not RFID_ENABLE or not rfid.reader:
        print("RFID désactivé ou non initialisé.")
        return

    while True:
        uid_hex = rfid.read_uid_hex_nonblock()
        now = time.monotonic()

        if uid_hex:
            # Nouvelle carte ou carte changée
            if current_uid is None or uid_hex != current_uid:
                current_uid = uid_hex
                last_seen_ts = now
                authorized_current, session_allowed_ml = is_authorized(uid_hex)

#                if authorized_current:
#                    if not vanne.est_ouverte():
#                       vanne.ouvrir()
#                    _message = f"Carte {uid_hex[:8]}… autorisée (maintenir la carte)"
#                else:
#                    _message = f"Carte {uid_hex[:8]}… NON autorisée"
#                    if vanne.est_ouverte():
#                        vanne.fermer()

                if authorized_current and session_allowed_ml > 0.0:
                    session_base_ml = compteur.volume_l() * 1000.0
                    if not vanne.est_ouverte():
                        vanne.ouvrir()
                    _message = f"Carte {uid_hex[:8]}… OK — quota {session_allowed_ml:.0f} ml"
                elif authorized_current and session_allowed_ml <= 0.0:
                    _message = f"Solde insuffisant ({uid_hex[:8]}…)"
                    if vanne.est_ouverte():
                        vanne.fermer()
                else:
                    _message = f"Carte {uid_hex[:8]}… NON autorisée"
                    if vanne.est_ouverte():
                        vanne.fermer()

                # PUSH vers Django (événement)
                push_event({
                    "uid": current_uid,
                    "present": True,
                    "authorized": authorized_current,
                    "vanne_ouverte": vanne.est_ouverte(),
                    "volume_ml": compteur.volume_l()*1000.0,
                    "debit_l_min": compteur.debit_l_min(),
                    "message": _message,
                })
            else:
                # même carte toujours présente
                last_seen_ts = now
                if vanne.est_ouverte() and session_allowed_ml > 0.0:
                    cur_ml = compteur.volume_l() * 1000.0
                    if (cur_ml - session_base_ml) >= (session_allowed_ml - 0.5):  # petite marge anti-jitter
                        vanne.fermer()
                        _message = "Solde épuisé — vanne fermée"
                        push_event({
                            "uid": current_uid, "present": True, "authorized": authorized_current,
                            "vanne_ouverte": False, "volume_ml": cur_ml,
                            "debit_l_min": compteur.debit_l_min(), "message": _message,
                        })
        else:
            # Carte retirée (après délai de grâce)
            if current_uid is not None and (now - last_seen_ts)*1000 > RFID_PRESENCE_GRACE_MS:
                current_uid = None
                authorized_current = False
                if vanne.est_ouverte():
                    vanne.fermer()
                _message = "Aucune carte"
                push_event({
                    "uid": None,
                    "present": False,
                    "authorized": False,
                    "vanne_ouverte": False,
                    "volume_ml": compteur.volume_l()*1000.0,
                    "debit_l_min": compteur.debit_l_min(),
                    "message": _message,
                })

        time.sleep(0.05)

threading.Thread(target=rfid_loop, daemon=True).start()

# ---- API Flask (status, override, etc.) ----
app = Flask(__name__)

def require_key(f):
    @wraps(f)
    def w(*a, **k):
        key = request.headers.get("X-API-Key") or request.args.get("key")
        if AGENT_SHARED_KEY and key != AGENT_SHARED_KEY:
            return jsonify(ok=False, error="unauthorized"), 401
        return f(*a, **k)
    return w

@app.route("/status")
@require_key
def status():
    with _rf_lock:
        last_uid = _last_uid
        last_seen = _last_seen_ts
    return jsonify(
        ok=True,
        vanne_ouverte=vanne.est_ouverte(),
        debit_l_min=compteur.debit_l_min(),
        volume_ml=compteur.volume_l()*1000.0,
        rfid_last_uid=last_uid,
        rfid_last_seen_ms=(time.monotonic()-last_seen)*1000 if last_seen else None,
        message=_message
    )

# Overrides manuels (si tu veux quand même piloter depuis UI)
@app.route("/on", methods=["POST"])
@require_key
def on(): vanne.ouvrir(); return jsonify(ok=True)
@app.route("/off", methods=["POST"])
@require_key
def off(): vanne.fermer(); return jsonify(ok=True)

#@atexit.register
#def _cleanup():
#    try: vanne.fermer()
#    except: pass
#    GPIO.cleanup()

if __name__ == "__main__":
    print(f"Agent RFID+GPIO sur http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
