import pigpio
import os
from utils.logger import logger

class Valve:
    """Gestion de la vanne électromagnétique via pigpio."""
    def __init__(self):
        self.gpio_pin = int(os.getenv("GPIO_VANNE", "18"))
        self.pi = pigpio.pi()
        
        if not self.pi.connected:
            logger.error("Pigpio non connecté (Vanne) !")
            return

        self.pi.set_mode(self.gpio_pin, pigpio.OUTPUT)
        self.close() # Sécurité au démarrage
        logger.info(f"Vanne initialisée sur GPIO {self.gpio_pin}")
        self.is_open = False

    def open(self):
        self.pi.write(self.gpio_pin, 1)
        self.is_open = True
        logger.info("Vanne ouverte.")

    def close(self):
        self.pi.write(self.gpio_pin, 0)
        self.is_open = False
        logger.info("Vanne fermée.")

    def cleanup(self):
        self.close()
