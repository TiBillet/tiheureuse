from utils.logger import logger
from utils.exceptions import ValveError


class Valve:
    """
    Commande de l'électrovanne via MQTT → ESP32.
    Remplace le contrôle GPIO direct (pigpio supprimé).

    Le Pi publie une commande JSON sur tiheureuse/<uuid>/cmd.
    L'ESP32 confirme l'état réel sur tiheureuse/<uuid>/valve.
    """

    def __init__(self, mqtt_client):
        self.mqtt_client = mqtt_client
        # État local optimiste (mis à jour par la confirmation ESP32)
        self.is_open = False

        # Abonnement à la confirmation d'état réel de la vanne
        self.mqtt_client.subscribe("valve", self._on_etat_vanne)

        # Sécurité au démarrage : fermer la vanne
        self.close()
        logger.info("[VANNE] Initialisée en mode MQTT")

    def _on_etat_vanne(self, payload: dict):
        """
        Reçoit la confirmation d'état depuis l'ESP32.
        Permet de détecter une divergence entre l'état demandé et l'état réel
        (ex: watchdog ESP32 qui ferme la vanne de son côté).
        """
        etat_reel = payload.get("open", False)
        if etat_reel != self.is_open:
            logger.info(f"[VANNE] État mis à jour par ESP32 : {'ouverte' if etat_reel else 'fermée'}")
            self.is_open = etat_reel

    def open(self, max_ml: float = 0.0, calibration_factor: float = 6.5):
        """
        Ouvre la vanne en envoyant une commande MQTT à l'ESP32.

        - max_ml            : volume maximum autorisé (l'ESP32 fermera automatiquement
                              la vanne si ce seuil est atteint). 0 = pas de limite.
        - calibration_factor: facteur débitmètre transmis à l'ESP32 pour son calcul
                              local de volume (même formule que flow_meter.py).
        """
        self.mqtt_client.publish("cmd", {
            "action": "open",
            "max_ml": round(max_ml, 1),
            "calibration_factor": round(float(calibration_factor), 4),
        })
        self.is_open = True
        logger.info(f"[VANNE] Commande ouverture → max={max_ml:.0f} ml, facteur={calibration_factor}")

    def close(self):
        """Ferme la vanne via MQTT."""
        self.mqtt_client.publish("cmd", {"action": "close"})
        self.is_open = False
        logger.info("[VANNE] Commande fermeture")

    def cleanup(self):
        self.close()
