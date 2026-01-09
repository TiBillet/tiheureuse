import requests
import json
import os
from utils.logger import logger

# --- CONFIGURATION ---
BACKEND_URL_BASE = os.getenv("BACKEND_URL", "http://192.168.1.10:8000/api/rfid") 
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "changeme")
TIREUSE = os.getenv("TIREUSE")
TIMEOUT = 2.0

class BackendClient:
    def __init__(self, tireuse_id=TIREUSE):
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": BACKEND_API_KEY
        }
        self.base_url = BACKEND_URL_BASE.rstrip("/")
        self.tireuse_id = tireuse_id

    def authorize(self, uid: str) -> dict:
        """
        Interroge seulement Django. 
        Renvoie un dictionnaire avec 'authorized': True/False et le reste des infos.
        """
        url = f"{self.base_url}/authorize"
        payload = {"uid": uid, "tireuse_id": self.tireuse_id}
        
        try:
            logger.debug(f"Demande autorisation pour {uid}...")
            r = requests.post(url, json=payload, headers=self.headers, timeout=TIMEOUT)
            
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    # Si le serveur dit OK ou renvoie une session
                    if "session_id" in data or data.get("authorized") is True:
                        data["authorized"] = True 
                        return data
                return {"authorized": False, "error": "Pas de session_id"}

            elif r.status_code == 403:
                # On retourne juste l'info, c'est le Controller qui décidera de l'affichage
                return {"authorized": False, "error": "Badge refusé ou solde insuffisant"}

            else:
                return {"authorized": False, "error": f"Erreur HTTP {r.status_code}"}
                
        except Exception as e:
            logger.error(f"Erreur réseau authorize: {e}")
            return {"authorized": False, "error": "Erreur Réseau"}

    def send_event(self, event_type, uid, session_id=None, data=None):
        """
        Envoie un événement (start, update, end, auth_fail).
        """
        # 1. Préparation des données data/inner
        inner_data = {}
        
        if session_id:
            inner_data["session_id"] = session_id
            
        if data is not None:
            if isinstance(data, dict):
                inner_data.update(data)
            else:
                inner_data["volume_ml"] = data

        # 2. Payload complet
        payload = {
            "event_type": event_type,
            "uid": uid,
            "tireuse_bec": self.tireuse_id,
            "data": inner_data
        }

        try:
            # Timeout court pour le débit, un peu plus long pour les autres events
            to = 1.0 if event_type == "pour_update" else 3.0
            
            url = f"{self.base_url}/event/"
            res = requests.post(url, json=payload, headers=self.headers, timeout=to)
            
            if res.status_code != 200:
                logger.error(f"Erreur API Event ({res.status_code}): {res.text}")
                
        except Exception as e:
            logger.error(f"Echec envoi event {event_type}: {e}")
