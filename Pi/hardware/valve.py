import RPi.GPIO as GPIO
from typing import Optional
from utils.logger import logger
from config.settings import VALVE_GPIO_PIN, VALVE_ACTIVE_HIGH
from utils.exceptions import ValveError

class Valve:
    """
    Contrôle une électrovanne via GPIO.
    Features :
    - Gestion du mode Active High/Low
    - Protection contre les changements d'état trop rapides
    - Vérification de l'état actuel
    """

    def __init__(self, pin: int = VALVE_GPIO_PIN, active_high: bool = VALVE_ACTIVE_HIGH):
        self.pin = pin
        self.active_high = active_high
        self.state = False  # État actuel (False = fermée, True = ouverte)
        self.last_change_time = 0
        self.min_delay = 0.5  # Délai minimal entre deux changements (s)

    def initialize(self) -> bool:
        """Initialise le GPIO pour la vanne."""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.OUT)
            self._set_state(False)  # Ferme la vanne au démarrage
            logger.info(f"Vanne initialisée sur GPIO{self.pin} (Active {'High' if self.active_high else 'Low'})")
            return True
        except Exception as e:
            logger.error(f"Erreur initialisation vanne: {e}")
            raise ValveError("Impossible d'initialiser la vanne") from e

    def _set_state(self, state: bool):
        """Définit l'état physique de la vanne."""
        GPIO.output(self.pin, state if self.active_high else not state)
        self.state = state
        self.last_change_time = time.time()
        logger.debug(f"Vanne {'ouverte' if state else 'fermée'}")

    def open(self) -> bool:
        """Ouvre la vanne si possible."""
        if self.state:
            logger.warning("Vanne déjà ouverte")
            return False

        current_time = time.time()
        if current_time - self.last_change_time < self.min_delay:
            logger.warning(f"Délai minimal non respecté ({self.min_delay}s)")
            return False

        try:
            self._set_state(True)
            logger.info("Vanne ouverte")
            return True
        except Exception as e:
            logger.error(f"Erreur ouverture vanne: {e}")
            raise ValveError("Impossible d'ouvrir la vanne") from e

    def close(self) -> bool:
        """Ferme la vanne si possible."""
        if not self.state:
            logger.warning("Vanne déjà fermée")
            return False

        current_time = time.time()
        if current_time - self.last_change_time < self.min_delay:
            logger.warning(f"Délai minimal non respecté ({self.min_delay}s)")
            return False

        try:
            self._set_state(False)
            logger.info("Vanne fermée")
            return True
        except Exception as e:
            logger.error(f"Erreur fermeture vanne: {e}")
            raise ValveError("Impossible de fermer la vanne") from e

    def cleanup(self):
        """Nettoie les ressources GPIO."""
        GPIO.cleanup()
        logger.info("GPIO nettoyé")
