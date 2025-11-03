#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, time, threading, atexit, subprocess
from functools import wraps
import requests, pigpio
from flask import Flask, request, jsonify
from rfid_reader import RFIDReader, RFIDInitError

PIN_VANNE=int(os.environ.get("PIN_VANNE","18"))
PIN_DEBIT=int(os.environ.get("PIN_DEBIT","23"))
FACTEUR_CALIBRATION=float(os.environ.get("K_FACTOR","450.0"))
BOUNCE_MS=int(os.environ.get("BOUNCE_MS","50"))
LISSAGE_SECONDES=float(os.environ.get("SMOOTH_SEC","1.0"))
RFID_AUTH_CACHE_SEC=float(os.environ.get("RFID_AUTH_CACHE_SEC","2.0"))
RFID_PRESENCE_GRACE_MS=int(os.environ.get("RFID_PRESENCE_GRACE_MS","300"))
RFID_MIN_OPEN_MS=int(os.environ.get("RFID_MIN_OPEN_MS","150"))

DJANGO_BASE_URL=os.environ.get("DJANGO_BASE_URL","http://192.168.1.10:8000").rstrip("/")
AGENT_SHARED_KEY=os.environ.get("AGENT_SHARED_KEY","changeme")
TIREUSE_BEC_ID=(os.environ.get("TIREUSE_BEC_ID","defaut") or "defaut").strip().lower()
LIQUID_LABEL=os.environ.get("LIQUID_LABEL","Liquide")

PUSH_PERIOD_S = 0.2
last_push_ts = 0.0  # throttle pour push_event "live"

HOST=os.environ.get("AGENT_HOST","0.0.0.0")
PORT=int(os.environ.get("AGENT_PORT","5000"))

AUTH_URL=f"{DJANGO_BASE_URL}/api/rfid/authorize"
EVENT_URL=f"{DJANGO_BASE_URL}/api/rfid/event"
def _guess_local_ip_for(url: str) -> str:
    import socket, urllib.parse
    host = urllib.parse.urlparse(url).hostname or "192.168.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.2)
    try:
        s.connect((host, 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL") \
    or f"http://{_guess_local_ip_for(DJANGO_BASE_URL)}:{PORT}"

print("[BOOT] AUTH_URL:", AUTH_URL)
print("[BOOT] EVENT_URL:", EVENT_URL)
print("[BOOT] TIREUSE_BEC_ID:", TIREUSE_BEC_ID, "| LIQUID_LABEL:", LIQUID_LABEL)

pi=pigpio.pi()
if not pi.connected: raise RuntimeError("pigpio daemon non joignable (pigpiod)")

pi.set_mode(PIN_VANNE,pigpio.OUTPUT); pi.write(PIN_VANNE,0)
pi.set_mode(PIN_DEBIT,pigpio.INPUT); pi.set_pull_up_down(PIN_DEBIT,pigpio.PUD_UP)
pi.set_glitch_filter(PIN_DEBIT,max(0,BOUNCE_MS)*1000)


def push_event(payload: dict):
    base = {
        "tireuse_bec": TIREUSE_BEC_ID,
        "liquid_label": LIQUID_LABEL,
        "agent_base_url": AGENT_BASE_URL,
}
    out = {**payload, **base}

    try:
        requests.post(
            EVENT_URL,
            json=out,
            headers={"X-API-Key": AGENT_SHARED_KEY},
            timeout=2.0
        )
    except Exception:
        pass


class Vanne:
  def __init__(self): self._o=False; self._t0=0.0; self.lock=threading.Lock()
  def ouvrir(self):
    with self.lock: pi.write(PIN_VANNE,1); self._o=True; self._t0=time.monotonic()
  def fermer(self):
    with self.lock:
      if self._o:
        dt=time.monotonic()-self._t0; mn=RFID_MIN_OPEN_MS/1000.0
        if dt<mn: time.sleep(mn-dt)
      pi.write(PIN_VANNE,0); self._o=False
  def est_ouverte(self):
    with self.lock: return self._o
vanne=Vanne()

class Compteur:
  def __init__(self,k):
    self.k=k; self.t=[]; self.n=0; self.lock=threading.Lock(); self.enabled=False
    self.cb=pi.callback(PIN_DEBIT,pigpio.FALLING_EDGE,self._pulse)
  def enable(self):  self.enabled=True
  def disable(self): self.enabled=False
  def _pulse(self,g,l,tick):
    if not self.enabled: return
    now=time.monotonic()
    with self.lock:
      self.n+=1; self.t.append(now)
      seuil=now-max(1.5*LISSAGE_SECONDES,1.0)
      while self.t and self.t[0]<seuil: self.t.pop(0)
  def volume_l(self):
    with self.lock: return self.n/self.k

  def debit_l_min(self):
    now=time.monotonic()
    with self.lock:
      seuil=now-max(1.5*LISSAGE_SECONDES,1.0)
      while self.t and self.t[0]<seuil: self.t.pop(0)
      if len(self.t)<2: return 0.0
      dt=(self.t[-1]-self.t[0]) or 1e-6
      freq=(len(self.t)-1)/dt
    return (freq/self.k)*60.0
  def close(self):
    try: self.cb.cancel()
    except: pass
compteur=Compteur(FACTEUR_CALIBRATION)


RFID_ENABLE = True
try:
    rfid = RFIDReader()
    RFID_ENABLE = True
    print("RFID initialisé, mode =", rfid.mode)
except RFIDInitError as e:
    rfid = None
    RFID_ENABLE = False
    print("RFID désactivé :", e)


_auth_cache = {}  # uid -> (expires_monotonic, bool)
def is_authorized(uid_hex: str):
    now = time.monotonic()
    ent = _auth_cache.get(uid_hex)
    if ent and ent[0] > now:
        _, ok_cached, allowed_cached = ent
        return (ok_cached and allowed_cached > 0.0), allowed_cached

    ok = False
    allowed_ml = 0.0
    try:
        r = requests.get(
            AUTH_URL,
            params={"uid": uid_hex, "tireuse_bec": TIREUSE_BEC_ID},
            headers={"X-API-Key": AGENT_SHARED_KEY},
            timeout=2.0,
        )
        if r.ok:
            j = r.json()
            ok = bool(j.get("authorized", False))
            allowed_ml = float(j.get("allowed_ml") or 0.0)
            enough = bool(j.get("enough_funds", allowed_ml > 0.0))
            ok = ok and enough
        else:
            print(f"[AUTH] HTTP {r.status_code} pour uid={uid_hex}")
    except Exception as e:
        print(f"[AUTH] uid={uid_hex} ERROR {e}")
        ok = False
        allowed_ml = 0.0

    _auth_cache[uid_hex] = (now + RFID_AUTH_CACHE_SEC, ok, allowed_ml)
    return (ok and allowed_ml > 0.0), allowed_ml

# ---- RFID loop: ouvre tant que carte autorisee et solde suffisant presente ----
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
    global last_seen_ml, session_base_ml, session_allowed_ml, last_push_ts
    last_seen_ts = time.monotonic()
    if not (RFID_ENABLE and rfid and rfid.is_ready):
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

                if authorized_current and session_allowed_ml > 0.0:
                    session_base_ml = compteur.volume_l() * 1000.0
                    if not vanne.est_ouverte():
                        vanne.ouvrir()
                        compteur.enable()
                    _message = f"Carte {uid_hex[:8]}… OK — quota {session_allowed_ml:.0f} ml"
                elif authorized_current and session_allowed_ml <= 0.0:
                    _message = f"Solde insuffisant ({uid_hex[:8]}…)"
                    if vanne.est_ouverte():
                        vanne.fermer()
                        compteur.disable()
                else:
                    _message = f"Carte {uid_hex[:8]}… NON autorisée"
                    if vanne.est_ouverte():
                        vanne.fermer()
                        compteur.disable()
# TODO compteur volume en parametre pour etalonnage
                # PUSH vers Django
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
                cur_ml = compteur.volume_l() * 1000.0

                if vanne.est_ouverte() and session_allowed_ml > 0.0:
                    if (cur_ml - session_base_ml) >= (session_allowed_ml - 0.5):  # petite marge anti-jitter
                        vanne.fermer()
                        compteur.disable()
                        _message = "Solde épuisé — vanne fermée"
                        push_event({
                            "uid": current_uid, "present": True, "authorized": authorized_current,
                            "vanne_ouverte": False, "volume_ml": cur_ml,
                            "debit_l_min": compteur.debit_l_min(), "message": _message,
                        })
                    if (now - last_push_ts) >= PUSH_PERIOD_S:
                        last_push_ts = now
                        push_event({
                            "uid": current_uid, "present": True, "authorized": authorized_current,
                            "vanne_ouverte": vanne.est_ouverte(),
                            "volume_ml": cur_ml,
                            "debit_l_min": compteur.debit_l_min(),
                            "message": _message,
                        })
        else:
            # Carte retirée (après délai de grâce)
            if current_uid is not None and (now - last_seen_ts)*1000 > RFID_PRESENCE_GRACE_MS:
                current_uid = None
                authorized_current = False
                if vanne.est_ouverte():
                    vanne.fermer()
                    compteur.disable()
                    # Volume de la session
                    consumed_ml = max(0.0, (compteur.volume_l() * 1000.0) - session_base_ml)
                    _message = "Aucune carte"
                    push_event({
                            "uid": None,
                            "present": False,
                            "authorized": False,
                            "vanne_ouverte": False,
                            "volume_ml": compteur.volume_l()*1000.0,
                            "session_done": True,  # marqueur de fin
                            "session_volume_ml": consumed_ml,  # volume de la session
                            "debit_l_min": compteur.debit_l_min(),
                            "message": _message,
                    })

            time.sleep(0.05)

# RAZ UI
push_event({
"uid": None,
"present": False,
"authorized": False,
"vanne_ouverte": False,
"volume_ml": compteur.volume_l()*1000.0,
"debit_l_min": compteur.debit_l_min(),
"message": "Boot: aucune carte"
 })

if RFID_ENABLE and rfid and rfid.is_ready:
    threading.Thread(target=rfid_loop, daemon=True).start()
    print("RFID : OK")
else:
    print("RFID: thread non démarré (lecteur indisponible)")
app=Flask(__name__)
def require_key(f):
  @wraps(f)
  def w(*a,**k):
    key=request.headers.get("X-API-Key") or request.args.get("key")
    if AGENT_SHARED_KEY and key!=AGENT_SHARED_KEY: return jsonify(ok=False,error="unauthorized"),401
    return f(*a,**k)
  return w

@app.route("/status")
@require_key
def status():

    return jsonify(
        ok=True,
        vanne_ouverte=vanne.est_ouverte(),
        debit_l_min=compteur.debit_l_min(),
        volume_ml=compteur.volume_l()*1000.0,
        rfid_last_uid=last_uid,
        rfid_last_seen_ms=(time.monotonic()-last_seen_ts)*1000 if last_seen_ts else None,
        message=_message
    )
# Overrides manuels ( piloter depuis UI)
@app.route("/on",methods=["POST"])
@require_key
def on(): vanne.ouvrir(); compteur.enable(); return jsonify(ok=True)

@app.route("/off",methods=["POST"])
@require_key
def off(): vanne.fermer(); compteur.disable(); return jsonify(ok=True)

def _write_kiosk_env(url:str):
  if not isinstance(url,str) or not url.startswith("http"): raise ValueError("URL invalide")
  with open("/home/sysop/kiosk.env","w",encoding="utf-8") as f: f.write(f"KIOSK_URL={url.strip()}\n")
def _restart_kiosk(): subprocess.run(["sudo","systemctl","restart","kiosk"],check=False)

@app.route("/agent/kiosk/set_url",methods=["POST"])
@require_key
def set_kiosk_url():
  data=request.get_json(force=True,silent=True) or {}
  url=(data.get("url") or "").strip()
  if not url.startswith("http"): return jsonify(ok=False,error="invalid url"),400
  try: _write_kiosk_env(url); _restart_kiosk(); return jsonify(ok=True,url=url)
  except Exception as e: return jsonify(ok=False,error=str(e)),500
# TODO verifier set url incorrecte , place localhost dans kiosk.ven
@atexit.register
def _cleanup():
  try: vanne.fermer()
  except: pass
  try: compteur.close()
  except: pass
  try: pi.stop()
  except: pass

if __name__=="__main__":
  print(f"Agent RFID+GPIO sur http://{ '0.0.0.0' }:{ 5000 }")
  app.run(host="0.0.0.0",port=5000,debug=False)
