#!/usr/bin/env python3
import sys
import time
import os
import signal
from dotenv import load_dotenv
from ui.ui_server import run_server
import threading

# Charge les variables d'environnement
load_dotenv()

# Imports projet
from utils.logger import logger
from config.settings import SYSTEMD_NOTIFY
from controllers.tibeer_controller import TibeerController

# --- Debug Permissions (Utile pour SystemD) ---
def debug_environment():
    print("--- ENVIRONNEMENT ---")
    print(f"UID: {os.getuid()}, GID: {os.getgid()}")
    if os.path.exists("/dev/gpiochip0"):
        import stat, pwd, grp
        st = os.stat("/dev/gpiochip0")
        print(f"Permissions /dev/gpiochip0: {stat.filemode(st.st_mode)}")
        try:
            print(f"Proprio: {pwd.getpwuid(st.st_uid).pw_name} / Groupe: {grp.getgrgid(st.st_gid).gr_name}")
        except KeyError:
            print("Utilisateur/Groupe ID inconnu au système")
    else:
        print("ATTENTION: /dev/gpiochip0 introuvable!")
# ---------------------------------------------

def main():
    """Point d'entrée du programme."""
    debug_environment()
    
    logger.info("Démarrage de TiBeer Main...")
    controller = None

    # TODO mettre en service debian sur le pi
    # 1. Démarrer l'interface Web (Flask) dans un thread séparé
    ui_thread = threading.Thread(target=run_server)
    ui_thread.daemon = True # S'arrêtera quand le programme principal s'arrête
    ui_thread.start()
    logger.info("Serveur UI démarré sur le port 5000")
    # TODO: Tester le port 5000 s'il répond

    # Notification Systemd (Ready)
    if SYSTEMD_NOTIFY:
        try:
            import systemd.daemon
            systemd.daemon.notify("READY=1")
        except ImportError:
            logger.warning("Module python-systemd manquant, notification ignorée.")

    try:
        # Création et lancement du contrôleur
        controller = TibeerController()
        controller.run()  

    except KeyboardInterrupt:
        logger.info("Arrêt signal (CTRL+C).")
    except Exception as e:
        logger.error(f"Erreur fatale dans le main: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Nettoyage final garantit
        if controller:
            logger.info("Exécution du nettoyage final...")
            controller.cleanup()
        logger.info("Processus terminé.")

if __name__ == "__main__":
    main()
# TODO voir kiosk.env
