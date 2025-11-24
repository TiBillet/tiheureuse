import requests
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from utils.logger import logger
from config.settings import BACKEND_URL, NETWORK_TIMEOUT, MAX_RETRIES
from utils.exceptions import BackendError

class BackendClient:
    """
    Client pour communiquer avec le backend Django.
    Features :
    - Retries automatiques en cas d'échec
    - Timeout configurable
    - Gestion des erreurs réseau
    - Mise en file d'attente des événements en cas de déconnexion
    """

    def __init__(self):
        self.session = requests.Session()
        self.retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            allowed_methods=["POST"]
        )
        self.adapter = HTTPAdapter(max_retries=self.retry_strategy)
        self.session.mount("http://", self.adapter)
        self.queue = []  # File d'attente pour les événements non envoyés

    def send_event(self, event_type: str, tag_id: str, data: dict) -> bool:
        """
        Envoie un événement au backend.
        Args:
            event_type: Type d'événement (ex: "pour_start", "pour_end")
            tag_id: UID du tag RFID
            data: Données supplémentaires (ex: {"volume": 0.5})
        Returns:
            bool: True si succès, False si échec (l'événement est mis en file)
        """
        payload = {
            "event_type": event_type,
            "tag_id": tag_id,
            "data": data,
            "timestamp": int(time.time())
        }

        try:
            response = self.session.post(
                BACKEND_URL,
                json=payload,
                timeout=NETWORK_TIMEOUT
            )
            response.raise_for_status()
            logger.info(f"Événement envoyé: {event_type} (Tag: {tag_id})")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Échec envoi événement {event_type}: {e}")
            self.queue.append(payload)  # Met en file d'attente
            return False

    def flush_queue(self) -> int:
        """Tente d'envoyer tous les événements en file d'attente."""
        if not self.queue:
            return 0

        success_count = 0
        failed_events = []

        for event in self.queue:
            try:
                response = self.session.post(
                    BACKEND_URL,
                    json=event,
                    timeout=NETWORK_TIMEOUT
                )
                response.raise_for_status()
                success_count += 1
                logger.info(f"Événement en file envoyé: {event['event_type']}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Échec envoi événement en file: {e}")
                failed_events.append(event)

        self.queue = failed_events  # Conserve les événements non envoyés
        logger.info(f"{success_count}/{len(self.queue) + success_count} événements envoyés")
        return success_count

    def test_connection(self) -> bool:
        """Teste la connectivité avec le backend."""
        try:
            response = self.session.get(
                f"{BACKEND_URL.rstrip('/')}/ping",
                timeout=NETWORK_TIMEOUT
            )
            response.raise_for_status()
            logger.info("Connexion au backend OK")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Test connexion échoué: {e}")
            return False
