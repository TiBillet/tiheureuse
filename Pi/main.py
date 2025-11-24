#!/usr/bin/env python3
import time
import signal
import sys
from utils.logger import logger
from config.settings import SYSTEMD_NOTIFY
from hardware.rfid_reader import RFIDReader
from hardware.valve import Valve
from hardware.flow_meter import FlowMeter
from network.backend_client import BackendClient

class TiBeerController:
    """Classe principale pour gérer la tireuse connectée."""

    def __init__(self):
        self.rfid_reader = RFIDReader()
        self.valve = Valve()
        self.flow_meter = FlowMeter()
        self.backend = BackendClient()
        self.current_tag = None
        self.pouring = False

    def initialize(self) -> bool:
        """Initialise tous les composants."""
        try:
            if not all([
                self.rfid_reader.initialize(),
                self.valve.initialize(),
                self.flow_meter.initialize(),
            ]):
                return False

            # Teste la connexion au backend
            if not self.backend.test_connection():
                logger.warning("Backend inaccessible (mode dégradé)")

            logger.info("TiBeer initialisé avec succès")
            return True
        except Exception as e:
            logger.error(f"Erreur initialisation: {e}")
            return False

    def _handle_tag(self, tag_id: str):
        """Gère la détection d'un tag RFID."""
        if self.pouring:
            logger.warning(f"Déjà en train de servir (Tag: {tag_id})")
            return

        self.current_tag = tag_id
        logger.info(f"Nouveau tag détecté: {tag_id}")

        # Ouvre la vanne et démarre la mesure
        if self.valve.open():
            self.flow_meter.start_measurement()
            self.pouring = True
            self.backend.send_event(
                "pour_start",
                tag_id,
                {"status": "started"}
            )

    def _monitor_pour(self):
        """Surveille le débit pendant le service."""
        while self.pouring:
            time.sleep(0.5)  # Vérifie toutes les 0.5s

            # Arrête si le débit est nul pendant 2s (fin de service)
            if self.flow_meter.flow_rate < 0.01:
                time.sleep(2)  # Attend pour confirmer
                if self.flow_meter.flow_rate < 0.01:
                    self._end_pour()

    def _end_pour(self):
        """Termine le service et envoie les données."""
        if not self.pouring:
            return

        # Arrête la mesure et ferme la vanne
        volume = self.flow_meter.stop_measurement()
        self.valve.close()

        # Envoie l'événement au backend
        success = self.backend.send_event(
            "pour_end",
            self.current_tag,
            {
                "volume": round(volume, 2),
                "status": "completed"
            }
        )

        if not success:
            logger.warning("Échec envoi données au backend (en file d'attente)")

        self.pouring = False
        self.current_tag = None
        logger.info(f"Service terminé. Volume: {volume:.2f}L")

    def run(self):
        """Boucle principale."""
        if not self.initialize():
            logger.error("Initialisation échouée. Arrêt...")
            return

        # Configure le callback RFID
        self.rfid_reader.set_callback(self._handle_tag)

        try:
            while True:
                # Flush la file d'attente périodiquement
                if len(self.backend.queue) > 0:
                    self.backend.flush_queue()

                # Si un service est en cours, surveille le débit
                if self.pouring:
                    self._monitor_pour()

                time.sleep(0.1)  # Boucle principale

        except KeyboardInterrupt:
            logger.info("Arrêt demandé par l'utilisateur...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Nettoie les ressources."""
        self.rfid_reader.close()
        self.valve.cleanup()
        self.flow_meter.cleanup()
        logger.info("Nettoyage terminé")

def main():
    """Point d'entrée du programme."""
    logger.info("Démarrage de TiBeer...")

    if SYSTEMD_NOTIFY:
        import systemd.daemon
        systemd.daemon.notify("READY=1")

    controller = TiBeerController()
    controller.run()

if __name__ == "__main__":
    main()
