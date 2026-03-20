import json
import paho.mqtt.client as mqtt
from config.settings import MQTT_BROKER_HOST, MQTT_BROKER_PORT, TIREUSE_BEC
from utils.logger import logger


class MqttHardwareClient:
    """
    Client MQTT partagé entre Valve et FlowMeter.
    Tourne dans un thread background (loop_start) pour ne pas bloquer le controller.

    Topics utilisés (tous préfixés par tiheureuse/<uuid>/) :
      Publie sur  : cmd    → commandes vers l'ESP32
      Écoute sur  : valve  ← confirmation état vanne (ESP32)
      Écoute sur  : flow   ← volume et débit (ESP32)
      Écoute sur  : status ← heartbeat ESP32
    """

    def __init__(self):
        self.tireuse_uuid = TIREUSE_BEC

        self._client = mqtt.Client(client_id="tibeer-pi")
        self._client.on_connect    = self._on_connect
        self._client.on_message    = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # Dictionnaire des callbacks enregistrés par suffixe de topic
        # { "flow": callable(payload_dict), "valve": callable(payload_dict), ... }
        self._handlers = {}

        self._client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=15)
        # loop_start lance un thread background — n'est pas bloquant
        self._client.loop_start()
        logger.info(f"[MQTT] Client connecté à {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")

    # -------------------------------------------------------------------------
    # Construction des topics
    # -------------------------------------------------------------------------

    def _topic(self, suffixe: str) -> str:
        """Retourne le topic complet : tiheureuse/<uuid>/<suffixe>"""
        return f"tiheureuse/{self.tireuse_uuid}/{suffixe}"

    # -------------------------------------------------------------------------
    # Callbacks paho
    # -------------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("[MQTT] Connecté au broker Mosquitto")
            # Réabonnement automatique à tous les topics après reconnexion
            for suffixe in self._handlers:
                topic = self._topic(suffixe)
                client.subscribe(topic)
                logger.info(f"[MQTT] Réabonné à {topic}")
        else:
            logger.error(f"[MQTT] Erreur connexion broker (rc={rc})")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning(f"[MQTT] Déconnexion inattendue (rc={rc}) — paho reconnectera automatiquement")

    def _on_message(self, client, userdata, msg):
        """Dispatch le message vers le bon handler selon le topic."""
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning(f"[MQTT] Message non JSON reçu sur {msg.topic}")
            return

        for suffixe, handler in self._handlers.items():
            if msg.topic == self._topic(suffixe):
                handler(payload)
                return

    # -------------------------------------------------------------------------
    # API publique
    # -------------------------------------------------------------------------

    def subscribe(self, suffixe: str, handler):
        """
        Abonne au topic tiheureuse/<uuid>/<suffixe>.
        Le handler reçoit le payload déjà désérialisé en dict.
        """
        self._handlers[suffixe] = handler
        self._client.subscribe(self._topic(suffixe))
        logger.info(f"[MQTT] Abonné à {self._topic(suffixe)}")

    def publish(self, suffixe: str, payload: dict):
        """Publie un message JSON sur tiheureuse/<uuid>/<suffixe>."""
        topic = self._topic(suffixe)
        message = json.dumps(payload)
        self._client.publish(topic, message)
        logger.debug(f"[MQTT] Publié sur {topic} : {message}")

    def cleanup(self):
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("[MQTT] Client déconnecté proprement")
