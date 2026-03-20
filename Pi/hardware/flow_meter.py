from utils.logger import logger
from utils.exceptions import FlowMeterError


class FlowMeter:
    """
    Lecture du débitmètre via MQTT ← ESP32.
    Remplace le comptage pigpio local.

    L'ESP32 publie les données toutes les secondes sur tiheureuse/<uuid>/flow :
      { "volume_ml": 120.5, "debit_cl_min": 32.1, "total_pulses": 47 }

    Si l'ESP32 atteint le volume maximum configuré, il publie :
      { "force_close": true, "volume_ml": 450.0 }
    """

    def __init__(self, mqtt_client, calibration_factor: float = None):
        self.mqtt_client = mqtt_client

        if calibration_factor is not None:
            self.calibration_factor = float(calibration_factor)
        else:
            self.calibration_factor = 6.5

        # Données reçues depuis l'ESP32 (mise à jour push toutes les secondes)
        self.volume_total_ml    = 0.0
        self.debit_cl_min       = 0.0
        self.total_pulses       = 0

        # Drapeau levé si l'ESP32 signale que le volume max est atteint
        self.force_close_requested = False

        self.mqtt_client.subscribe("flow", self._on_flow_data)
        logger.info("[DEBIT] Débitmètre initialisé en mode MQTT")

    def _on_flow_data(self, payload: dict):
        """Reçoit les données de volume et débit depuis l'ESP32."""

        # Cas force_close : l'ESP32 a fermé la vanne de son côté
        if payload.get("force_close"):
            logger.warning("[DEBIT] ESP32 signale volume max atteint → force_close")
            self.force_close_requested = True
            # On met quand même à jour le volume final
            if "volume_ml" in payload:
                self.volume_total_ml = float(payload["volume_ml"])
            return

        self.volume_total_ml = float(payload.get("volume_ml", 0.0))
        self.debit_cl_min    = float(payload.get("debit_cl_min", 0.0))
        self.total_pulses    = int(payload.get("total_pulses", 0))

    def update(self):
        """
        Compatibilité avec l'API existante de TibeerController.
        Les données arrivent en push depuis l'ESP32 — rien à calculer ici.
        """
        pass

    def volume_l(self) -> float:
        """Retourne le volume total en litres."""
        return self.volume_total_ml / 1000.0

    def get_volume_ml(self) -> float:
        return self.volume_total_ml

    def get_flow_rate_cl(self) -> float:
        """Retourne le débit en cL/min."""
        return self.debit_cl_min

    def set_calibration_factor(self, factor: float):
        """
        Met à jour le facteur de calibration et le transmet à l'ESP32 via MQTT.
        L'ESP32 utilisera ce nouveau facteur pour ses calculs locaux.
        """
        self.calibration_factor = float(factor)
        self.mqtt_client.publish("cmd", {
            "action": "set_calibration",
            "calibration_factor": round(self.calibration_factor, 4),
        })
        logger.info(f"[DEBIT] Facteur calibration transmis à l'ESP32 : {self.calibration_factor}")

    def reset(self):
        """
        Remet à zéro les compteurs locaux.
        Note : l'ESP32 remet lui-même ses compteurs à zéro à la réception d'une commande 'open'.
        """
        self.volume_total_ml       = 0.0
        self.debit_cl_min          = 0.0
        self.total_pulses          = 0
        self.force_close_requested = False

    def cleanup(self):
        # Pas de ressource locale à libérer (MQTT géré par MqttHardwareClient)
        pass
