#!/usr/bin/env python3
import time
import signal
import sys
import logging
from utils.logger import logger, setup_logger
from config.settings import (
    SYSTEMD_NOTIFY, RFID_TYPE, BACKEND_URL, AGENT_SHARED_KEY
)
from hardware.rfid_reader import RFIDReader
from hardware.valve import Valve
from hardware.flow_meter import FlowMeter
from network.backend_client import BackendClient
from utils.exceptions import RFIDReadError, ValveError, FlowMeterError

class TiBeerController:
    def __init__(self):
        self.rfid_reader = RFIDReader()
        self.valve = Valve()
        self.flow_meter = FlowMeter()
        self.backend = BackendClient()
        self.current_tag = None
        self.pouring = False
        self.session_id = None  # ID de session côté Django

    def initialize(self) -> bool:
        """Initialise tous les composants."""
        try:
            logger.info("Initialisation de TiBeer...")

            # Initialise le matériel
            if not self.rfid_reader.initialize():
                logger.error("Échec initialisation RFID")
                return False
            if not self.valve.initialize():
                logger.error("Échec initialisation vanne")
                return False
            if not self.flow_meter.initialize():
                logger.error("Échec initialisation débitmètre")
                return False

            # Teste la connexion au backend
            if not self.backend.test_connection():
                logger.warning("Backend inaccessible (mode dégradé)")

            logger.info("TiBeer prêt !")
            return True

        except Exception as e:
            logger.error(f"Erreur critique lors de l'initialisation: {e}")
            return False

    def _handle_tag_detected(self, tag_id: str):
        """Géré quand un tag RFID est détecté."""
        if self.pouring:
            logger.warning(f"Déjà en train de servir (Tag: {tag_id})")
            return

        self.current_tag = tag_id
        logger.info(f"Tag détecté: {tag_id}")

        # 1. Vérifie l'autorisation auprès du backend
        auth_data = self.backend.authorize_card(tag_id)
        if not auth_data or not auth_data.get("authorized"):
            logger.warning(f"Tag non autorisé: {tag_id}")
            return

        # 2. Ouvre la vanne et démarre la mesure
        if not self.valve.open():
            logger.error("Impossible d'ouvrir la vanne")
            return

        self.flow_meter.start_measurement()
        self.pouring = True
        self.session_id = auth_data.get("session_id")

        # 3. Notifie le backend du début de service
        self.backend.send_event(
            event_type="pour_start",
            tag_id=tag_id,
            data={
                "session_id": self.session_id,
                "status": "started",
                "volume_ml": 0.0
            }
        )

    def _monitor_pouring(self):
        """Surveille le débit pendant le service."""
        last_volume = 0.0
        while self.pouring:
            try:
                current_volume = self.flow_meter.get_current_volume()
                if current_volume != last_volume:
                    # Envoie des mises à jour périodiques au backend
                    self.backend.send_event(
                        event_type="pour_update",
                        tag_id=self.current_tag,
                        data={
                            "session_id": self.session_id,
                            "volume_ml": current_volume,
                            "flow_rate": self.flow_meter.flow_rate
                        }
                    )
                    last_volume = current_volume

                # Arrête si le débit est nul pendant 2s
                if self.flow_meter.flow_rate < 0.01:
                    time.sleep(2)
                    if self.flow_meter.flow_rate < 0.01:
                        self._end_pouring()

                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Erreur pendant le service: {e}")
                self._end_pouring(force=True)

    def _end_pouring(self, force=False):
        """Termine le service et envoie les données finales."""
        if not self.pouring and not force:
            return

        try:
            # Arrête la mesure et ferme la vanne
            final_volume = self.flow_meter.stop_measurement()
            self.valve.close()

            # Envoie l'événement final au backend
            success = self.backend.send_event(
                event_type="pour_end",
                tag_id=self.current_tag,
                data={
                    "session_id": self.session_id,
                    "volume_ml": final_volume,
                    "status": "completed"
                }
            )

            if not success:
                logger.warning("Échec envoi données finales (en file d'attente)")

        except Exception as e:
            logger.error(f"Erreur lors de la fin du service: {e}")

        finally:
            self.pouring = False
            self.current_tag = None
            self.session_id = None

    def run(self):
        """Boucle principale."""
        if not self.initialize():
            logger.error("Initialisation échouée. Arrêt.")
            return

        # Configure le callback pour le lecteur RFID
        self.rfid_reader.set_callback(self._handle_tag_detected)

        try:
            while True:
                # Lit les tags RFID en continu
                self.rfid_reader.read_continuous()
                if self.pouring:
                    self._monitor_pouring()
                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Arrêt demandé par l'utilisateur")
        except Exception as e:
            logger.error(f"Erreur critique: {e}")
        finally:
            self.rfid_reader.close()
            self.valve.close()
            logger.info("TiBeer arrêté proprement")

if __name__ == "__main__":
    controller = TiBeerController()
    controller.run()
