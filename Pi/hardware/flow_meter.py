import time
import pigpio
import os
from utils.logger import logger

class FlowMeter:
    """
    Gestion du débitmètre via pigpio (interruptions précises).
    Calcule le volume total et le débit instantané.
    """
    def __init__(self):
        # Configuration depuis variables d'env
        self.gpio_pin = int(os.getenv("GPIO_FLOW_SENSOR", "23"))
        try:
            self.calibration_factor = float(os.getenv("FLOW_CALIBRATION_FACTOR", "6.5"))
        except ValueError:
            self.calibration_factor = 6.5

        self.pi = pigpio.pi()
        if not self.pi.connected:
            logger.error("Pigpio non connecté ! Le débitmètre ne fonctionnera pas.")
            logger.error("Avez-vous lancé 'sudo pigpiod' ?")
            raise Exception("Pigpio connection failed")

        # Config GPIO
        self.pi.set_mode(self.gpio_pin, pigpio.INPUT)
        self.pi.set_pull_up_down(self.gpio_pin, pigpio.PUD_UP)

        # Variables internes
        self.flow_count = 0
        self.total_pulses = 0
        self.volume_total_ml = 0.0
        self.last_time = time.time()
        self.current_flow_rate = 0.0 # L/min

        # Callback (Interruption)
        self.cb = self.pi.callback(self.gpio_pin, pigpio.FALLING_EDGE, self._callback)
        logger.info(f"Débitmètre initialisé sur GPIO {self.gpio_pin}")

    def _callback(self, gpio, level, tick):
        """Appelé à chaque impulsion du capteur."""
        self.flow_count += 1
        self.total_pulses += 1

    def update(self):
        """
        À appeler régulièrement (ex: toutes les secondes) pour mettre à jour
        le débit instantané (L/min) et le volume cumulé.
        """
        now = time.time()
        delta_t = now - self.last_time
        
        # On met à jour si plus de 0.5s s'est écoulé pour lisser
        if delta_t > 0.5:
            # Fréquence en Hz
            freq = self.flow_count / delta_t
            
            # Calcul débit L/min = (Hz / facteur) * 60
            self.current_flow_rate = (freq / self.calibration_factor) * 60 if freq > 0 else 0
            
            # Ajout au volume total (L) converti en ml
            # Volume ce cycle = (Débit L/min / 60) * delta_t_sec * 1000
            vol_added = (self.current_flow_rate / 60) * delta_t * 1000
            self.volume_total_ml += vol_added

            # Reset compteurs intermédiaires
            self.flow_count = 0
            self.last_time = now
            
            return self.current_flow_rate
        return self.current_flow_rate
    def volume_l(self):
        """
        Fonction requise par TibeerController.
        Retourne le volume total en Litres.
        Formule: 1 L = (Facteur * 60) impulsions.
        """
        pulses_per_liter = self.calibration_factor * 60
        if pulses_per_liter == 0: return 0.0
        return self.total_pulses / pulses_per_liter


    def get_volume_ml(self):
        return self.volume_total_ml
    
    def get_flow_rate(self):
        return self.current_flow_rate

    def reset(self):
        self.flow_count = 0
        self.total_pulses = 0
        self.current_flow_rate = 0.0
        self.last_time = time.time()

    def cleanup(self):
        if self.cb:
            self.cb.cancel()
        # Note: on ne stop pas self.pi ici car partagé avec Valve si besoin, 

