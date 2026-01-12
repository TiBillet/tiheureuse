import time
import sys
import threading
import os
from hardware.rfid_reader import RFIDReader
from hardware.valve import Valve
from hardware.flow_meter import FlowMeter
from network.backend_client import BackendClient 
from utils.logger import logger

# Paramètres
CARD_GRACE_PERIOD_S = 1.0  # Temps avant de considérer que la carte est partie (Anti-rebond)
UPDATE_INTERVAL_S = 1.0    # Fréquence d'envoi des infos de débit

class TibeerController: 
    def __init__(self):
        logger.info("Initialisation TiBeer Controller (Mode Session Django)...")
        self.rfid = RFIDReader()
        self.valve = Valve()  
        self.flow_meter = FlowMeter()
        self.client = BackendClient(tireuse_id=os.getenv("TIREUSE_BEC", "LeBar"))
        # État du système
        self.current_uid = None     
        self.last_seen_ts = 0       
        self.session_id = None      
        self.is_serving = False     
        self.session_start_vol = 0.0 
        self.last_update_ts = 0

        self.running = True

    def run(self):
        logger.info("Service TiBeer démarré. En attente de badge...")
        
        try:
            while self.running:
                uid = self.rfid.read_uid()
                now = time.time()

                if uid:
                    # --- UNE CARTE EST PRÉSENTE ---
                    self.last_seen_ts = now
                    
                    # NOUVELLE CARTE (ou retour après micro-coupure)
                    if uid != self.current_uid:
                         logger.info(f"Nouveau badge détecté: {uid}")
                         # Sécurité: fermer l'ancienne session si elle existait
                         if self.is_serving:
                             self._end_session_actions()
                         
                         self.current_uid = uid 
                         self._handle_new_session(uid)
                    
                    # MÊME CARTE (Boucle de service)
                    elif self.is_serving:
                         self._handle_pouring_loop(now)
                         
                else:
                    # --- PAS DE CARTE ---
                    if self.current_uid is not None:
                        # Anti-rebond (Grace period)
                        if (now - self.last_seen_ts) > CARD_GRACE_PERIOD_S:
                            logger.info(f"Badge {self.current_uid} retiré.")
                            self._handle_card_removal()

                time.sleep(0.1)

        except KeyboardInterrupt:
            logger.info("Arrêt manuel.")
        finally:
            self.cleanup()

    def _handle_new_session(self, uid):
        """Vérifie le badge et décide de l'ouvrir ou de rejeter"""
        # 1. Demande au backend
        auth_response = self.client.authorize(uid)
        
        if auth_response.get("authorized") is True:
            # --- CAS 1 : AUTORISÉ (VERT) ---
            self.session_id = auth_response.get("session_id")
            
            # Reset débitmètre (snapshot)
            self.session_start_vol = self.flow_meter.volume_l() * 1000.0
            
            # Action Physique
            self.valve.open()
            self.is_serving = True
            
            logger.info(f"Autorisation OK. Session {self.session_id}. Vanne ouverte.")
            
            # Affichage VERT
            self.client.send_event("pour_start", self.current_uid, self.session_id)
            self.last_update_ts = time.time()
            
        else:
            # --- CAS 2 : REFUSÉ (ROUGE) ---
            error_msg = auth_response.get('error', 'Non autorisé')
            logger.warning(f"Badge {uid} refusé: {error_msg}")
            
            self.is_serving = False
            self.session_id = None
            
            # C'est ici que l'affichage ROUGE est envoyé
            # note: session_id est None ici, c'est normal
            self.client.send_event("auth_fail", self.current_uid, None, {"message": error_msg})

    def _handle_pouring_loop(self, now):
        """Gestion du débit pendant le service"""
        if (now - self.last_update_ts) > UPDATE_INTERVAL_S:
            current_total_vol = self.flow_meter.volume_l() * 1000.0
            served_vol = current_total_vol - self.session_start_vol
            
            self.client.send_event("pour_update", self.current_uid, self.session_id, served_vol)
            self.last_update_ts = now

    def _handle_card_removal(self):
        """Gère le retrait du badge"""
        if self.is_serving:
            # Fin de service normale (BLEU)
            self._end_session_actions()
        else:
            # Retrait d'une carte refusée ou inactive (GRIS)
            self.client.send_event("card_removed", self.current_uid, None)
            
        self.current_uid = None
        self.session_id = None

    def _end_session_actions(self):
        """Ferme la vanne et envoie le bilan"""
        self.valve.close()
        logger.info("Vanne fermée (Fin session).")
        
        if self.current_uid and self.session_id:
            final_total_vol = self.flow_meter.volume_l() * 1000.0
            served_vol = final_total_vol - self.session_start_vol
            
            logger.info(f"Envoi rapport fin. Volume: {served_vol:.1f} ml")
            self.client.send_event("pour_end", self.current_uid, self.session_id, served_vol)
            
        self.is_serving = False

    def cleanup(self):
        logger.info("Nettoyage des ressources...")
        try:
            self.valve.close()
        except:
            pass
