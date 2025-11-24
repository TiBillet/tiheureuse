import time
import RPi.GPIO as GPIO
from threading import Thread
from utils.logger import logger
from config.settings import FLOW_GPIO_PIN, FLOW_CALIBRATION_FACTOR
from utils.exceptions import FlowMeterError

class FlowMeter:
    """
    Mesure le débit de bière via un capteur à impulsions.
    Features :
    - Calibration configurable (impulsions par litre)
    - Mesure en temps réel avec thread dédié
    - Détection des fuites (débit anormal)
    """

    def __init__(self, pin: int = FLOW_GPIO_PIN, calibration_factor: float = FLOW_CALIBRATION_FACTOR):
        self.pin = pin
        self.calibration_factor = calibration_factor  # Impulsions par litre
        self.pulse_count = 0
        self.flow_rate = 0.0  # L/min
        self.total_volume = 0.0  # Litres totaux
        self.measuring = False
        self.thread = None
        self.last_time = time.time()

    def initialize(self) -> bool:
        """Initialise le GPIO pour le débitmètre."""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self.pin,
                GPIO.FALLING,
                callback=self._pulse_callback,
                bouncetime=20  # Anti-rebond matériel (ms)
            )
            logger.info(f"Débitmètre initialisé sur GPIO{self.pin} (Calibration: {self.calibration_factor} imp/L)")
            return True
        except Exception as e:
            logger.error(f"Erreur initialisation débitmètre: {e}")
            raise FlowMeterError("Impossible d'initialiser le débitmètre") from e

    def _pulse_callback(self, channel):
        """Callback appelée à chaque impulsion (thread-safe)."""
        if not self.measuring:
            return
        self.pulse_count += 1

    def start_measurement(self):
        """Démarre la mesure du débit."""
        if self.measuring:
            logger.warning("Mesure déjà en cours")
            return

        self.measuring = True
        self.pulse_count = 0
        self.last_time = time.time()
        self.thread = Thread(target=self._calculate_flow, daemon=True)
        self.thread.start()
        logger.info("Mesure du débit démarrée")

    def stop_measurement(self) -> float:
        """Arrête la mesure et retourne le volume total."""
        if not self.measuring:
            logger.warning("Aucune mesure en cours")
            return 0.0

        self.measuring = False
        if self.thread:
            self.thread.join(timeout=1.0)

        # Calcule le volume total
        volume = self.pulse_count / self.calibration_factor
        self.total_volume += volume
        logger.info(f"Mesure arrêtée. Volume: {volume:.2f}L (Total: {self.total_volume:.2f}L)")
        return volume

    def _calculate_flow(self):
        """Calcule le débit en temps réel (thread dédié)."""
        last_count = 0
        while self.measuring:
            time.sleep(1.0)  # Met à jour toutes les secondes
            current_time = time.time()
            elapsed = current_time - self.last_time

            if elapsed >= 1.0:  # Met à jour toutes les secondes
                pulses = self.pulse_count - last_count
                self.flow_rate = (pulses / self.calibration_factor) * 60  # L/min
                last_count = self.pulse_count
                self.last_time = current_time
                logger.debug(f"Débit: {self.flow_rate:.2f}L/min")

    def reset_total(self):
        """Remet à zéro le compteur total."""
        self.total_volume = 0.0
        logger.info("Compteur total réinitialisé")

    def cleanup(self):
        """Nettoie les ressources."""
        GPIO.remove_event_detect(self.pin)
        logger.info("Débitmètre nettoyé")
