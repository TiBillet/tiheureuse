// =============================================================================
// tibeer_esp32.ino — Firmware TiBeer pour ESP32 WROOM-32
//
// Rôle : piloter l'électrovanne et le débitmètre via MQTT.
// Le Pi reste le cerveau (auth RFID, logique session, communication Django).
// L'ESP32 est l'exécutant hardware (temps réel, watchdog local).
//
// Librairies requises (Gestionnaire de bibliothèques Arduino) :
//   - PubSubClient  by Nick O'Leary  (v2.8+)
//   - ArduinoJson   by Benoit Blanchon (v6+)
//
// Topics MQTT :
//   Subscribe : tiheureuse/<uuid>/cmd     ← commandes du Pi
//   Publish   : tiheureuse/<uuid>/flow    → volume et débit
//   Publish   : tiheureuse/<uuid>/valve   → état vanne
//   Publish   : tiheureuse/<uuid>/status  → heartbeat
// =============================================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "config.h"

// -----------------------------------------------------------------------------
// Topics MQTT (construits depuis l'UUID en setup)
// -----------------------------------------------------------------------------
char topic_cmd[80];     // tiheureuse/<uuid>/cmd
char topic_flow[80];    // tiheureuse/<uuid>/flow
char topic_valve[80];   // tiheureuse/<uuid>/valve
char topic_status[80];  // tiheureuse/<uuid>/status

// -----------------------------------------------------------------------------
// Variables débitmètre (volatile car modifiées dans l'ISR)
// -----------------------------------------------------------------------------
volatile unsigned long total_pulses                 = 0;
volatile unsigned long pulses_depuis_dernier_update = 0;

float facteur_calibration  = FLOW_CALIBRATION_FACTOR;
float volume_total_ml      = 0.0f;
float debit_cl_min         = 0.0f;

// -----------------------------------------------------------------------------
// Variables session
// -----------------------------------------------------------------------------
float         max_volume_ml          = 0.0f;  // 0 = pas de limite
bool          vanne_ouverte          = false;
unsigned long derniere_reception_mqtt_ms = 0;  // horodatage dernier message reçu

// -----------------------------------------------------------------------------
// Timers internes
// -----------------------------------------------------------------------------
unsigned long dernier_envoi_flow_ms   = 0;
unsigned long dernier_envoi_status_ms = 0;

// -----------------------------------------------------------------------------
// Objets WiFi / MQTT
// -----------------------------------------------------------------------------
WiFiClient   wifi_client;
PubSubClient mqtt_client(wifi_client);

// =============================================================================
// ISR — Interruption débitmètre
// IRAM_ATTR : placé en RAM pour ne pas être interrompu par le cache flash
// =============================================================================
void IRAM_ATTR on_pulse_debimetre() {
    total_pulses++;
    pulses_depuis_dernier_update++;
}

// =============================================================================
// Vanne
// =============================================================================

void ouvrir_vanne() {
    // Respecte la polarité configurée dans config.h
    digitalWrite(PIN_VANNE, VALVE_ACTIVE_HIGH ? HIGH : LOW);
    vanne_ouverte = true;
    Serial.println("[VANNE] Ouverture");
}

void fermer_vanne() {
    digitalWrite(PIN_VANNE, VALVE_ACTIVE_HIGH ? LOW : HIGH);
    vanne_ouverte = false;
    Serial.println("[VANNE] Fermeture");
}

// =============================================================================
// Publication MQTT
// =============================================================================

void publier_etat_vanne() {
    StaticJsonDocument<64> doc;
    doc["open"] = vanne_ouverte;

    char buffer[64];
    serializeJson(doc, buffer);
    mqtt_client.publish(topic_valve, buffer, true);  // retain=true : état persisté
}

void publier_flow() {
    StaticJsonDocument<128> doc;
    doc["volume_ml"]   = volume_total_ml;
    doc["debit_cl_min"] = debit_cl_min;
    doc["total_pulses"] = (unsigned long)total_pulses;

    char buffer[128];
    serializeJson(doc, buffer);
    mqtt_client.publish(topic_flow, buffer);
}

void publier_status() {
    StaticJsonDocument<64> doc;
    doc["uptime_ms"]     = millis();
    doc["vanne_ouverte"] = vanne_ouverte;

    char buffer[64];
    serializeJson(doc, buffer);
    mqtt_client.publish(topic_status, buffer);
}

// =============================================================================
// Mise à jour volume et débit
// Appelée toutes les FLOW_PUBLISH_INTERVAL_MS millisecondes.
// Reproduit la logique de Pi/hardware/flow_meter.py :
//   débit (L/min) = (pulses_par_sec / facteur) * 60
//   volume (L)    = total_pulses / (facteur * 60)
// =============================================================================
void mettre_a_jour_mesures() {
    // Lecture atomique : on snapshote et remet à zéro le compteur intermédiaire
    unsigned long pulses_ce_cycle = pulses_depuis_dernier_update;
    pulses_depuis_dernier_update  = 0;

    // delta_t en secondes (intervalle entre deux mises à jour)
    float delta_t_sec = FLOW_PUBLISH_INTERVAL_MS / 1000.0f;

    // Fréquence instantanée en Hz
    float freq_hz = pulses_ce_cycle / delta_t_sec;

    // Débit en L/min puis converti en cL/min pour le Pi
    float debit_l_min = (freq_hz / facteur_calibration) * 60.0f;
    debit_cl_min      = debit_l_min * 100.0f;

    // Volume total depuis le début de la session
    // Formule identique à flow_meter.py : volume_l() = total_pulses / (facteur * 60)
    float volume_total_l = (float)total_pulses / (facteur_calibration * 60.0f);
    volume_total_ml      = volume_total_l * 1000.0f;
}

// =============================================================================
// Watchdog local
// Si le Pi ne répond plus (crash, reboot, coupure réseau), on ferme la vanne
// pour éviter un tirage infini.
// =============================================================================
void verifier_watchdog() {
    if (!vanne_ouverte) {
        return;  // Vanne déjà fermée, rien à faire
    }

    unsigned long temps_sans_message_ms = millis() - derniere_reception_mqtt_ms;

    if (temps_sans_message_ms > WATCHDOG_TIMEOUT_MS) {
        Serial.printf("[WATCHDOG] Aucun message depuis %lu ms → fermeture vanne\n",
                      temps_sans_message_ms);
        fermer_vanne();
        publier_etat_vanne();
    }
}

// =============================================================================
// Vérification du volume maximum autorisé pour la session
// Si le volume versé dépasse max_volume_ml → fermeture + notification Pi
// =============================================================================
void verifier_max_volume() {
    if (!vanne_ouverte) {
        return;
    }
    if (max_volume_ml <= 0.0f) {
        return;  // Pas de limite configurée
    }

    if (volume_total_ml >= max_volume_ml) {
        Serial.printf("[LIMITE] Volume %.1f ml >= max %.1f ml → fermeture\n",
                      volume_total_ml, max_volume_ml);
        fermer_vanne();
        publier_etat_vanne();

        // On notifie le Pi que le volume max est atteint
        StaticJsonDocument<64> doc;
        doc["force_close"]  = true;
        doc["volume_ml"]    = volume_total_ml;
        char buffer[64];
        serializeJson(doc, buffer);
        mqtt_client.publish(topic_flow, buffer);
    }
}

// =============================================================================
// Réception des commandes MQTT depuis le Pi
//
// Format JSON attendu :
//   { "action": "open",  "max_ml": 450.0, "calibration_factor": 6.5 }
//   { "action": "close" }
//   { "action": "reset" }
//   { "action": "set_calibration", "calibration_factor": 7.2 }
// =============================================================================
void on_message(char* topic, byte* payload, unsigned int longueur) {
    // Mise à jour du watchdog à chaque message reçu
    derniere_reception_mqtt_ms = millis();

    // Décodage JSON
    StaticJsonDocument<128> doc;
    DeserializationError erreur = deserializeJson(doc, payload, longueur);

    if (erreur) {
        Serial.printf("[MQTT] JSON invalide : %s\n", erreur.c_str());
        return;
    }

    const char* action = doc["action"] | "";
    Serial.printf("[MQTT] Commande reçue : %s\n", action);

    if (strcmp(action, "open") == 0) {
        // Mise à jour du facteur de calibration si fourni par le Pi
        if (doc.containsKey("calibration_factor")) {
            facteur_calibration = doc["calibration_factor"].as<float>();
            Serial.printf("[DEBIT] Facteur calibration mis à jour : %.4f\n",
                          facteur_calibration);
        }

        // Volume maximum autorisé pour cette session
        max_volume_ml = doc["max_ml"] | 0.0f;

        // Remise à zéro des compteurs avant l'ouverture
        total_pulses                 = 0;
        pulses_depuis_dernier_update = 0;
        volume_total_ml              = 0.0f;
        debit_cl_min                 = 0.0f;

        ouvrir_vanne();
        publier_etat_vanne();

    } else if (strcmp(action, "close") == 0) {
        fermer_vanne();
        publier_etat_vanne();

    } else if (strcmp(action, "reset") == 0) {
        // Remise à zéro compteurs sans toucher à la vanne
        total_pulses                 = 0;
        pulses_depuis_dernier_update = 0;
        volume_total_ml              = 0.0f;
        debit_cl_min                 = 0.0f;
        max_volume_ml                = 0.0f;
        Serial.println("[SESSION] Compteurs remis à zéro");

    } else if (strcmp(action, "set_calibration") == 0) {
        facteur_calibration = doc["calibration_factor"] | FLOW_CALIBRATION_FACTOR;
        Serial.printf("[DEBIT] Facteur calibration mis à jour : %.4f\n",
                      facteur_calibration);

    } else {
        Serial.printf("[MQTT] Action inconnue : %s\n", action);
    }
}

// =============================================================================
// Connexion WiFi (bloquante au démarrage, non bloquante ensuite)
// =============================================================================
void connecter_wifi() {
    Serial.printf("[WIFI] Connexion à %s ...\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    unsigned long debut = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        // Timeout 30s pour ne pas bloquer indéfiniment
        if (millis() - debut > 30000UL) {
            Serial.println("\n[WIFI] Timeout — redémarrage ESP32");
            ESP.restart();
        }
    }

    Serial.printf("\n[WIFI] Connecté ! IP : %s\n",
                  WiFi.localIP().toString().c_str());
}

// =============================================================================
// Connexion / reconnexion MQTT
// =============================================================================
void connecter_mqtt() {
    while (!mqtt_client.connected()) {
        Serial.printf("[MQTT] Connexion au broker %s:%d ...\n",
                      MQTT_BROKER_IP, MQTT_BROKER_PORT);

        bool connecte;
        if (strlen(MQTT_USER) > 0) {
            connecte = mqtt_client.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASSWORD);
        } else {
            connecte = mqtt_client.connect(MQTT_CLIENT_ID);
        }

        if (connecte) {
            Serial.println("[MQTT] Connecté !");
            mqtt_client.subscribe(topic_cmd);
            Serial.printf("[MQTT] Abonné à %s\n", topic_cmd);

            // Si la vanne était ouverte avant la déconnexion, on la ferme par sécurité
            if (vanne_ouverte) {
                Serial.println("[SECURITE] Reconnexion MQTT → fermeture vanne préventive");
                fermer_vanne();
                publier_etat_vanne();
            }
        } else {
            Serial.printf("[MQTT] Échec (rc=%d), retry dans 3s\n",
                          mqtt_client.state());
            delay(3000);
        }
    }
}

// =============================================================================
// setup — exécuté une seule fois au démarrage
// =============================================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n=== TiBeer ESP32 démarrage ===");

    // Construction des topics depuis l'UUID
    snprintf(topic_cmd,    sizeof(topic_cmd),    "tiheureuse/%s/cmd",    TIREUSE_UUID);
    snprintf(topic_flow,   sizeof(topic_flow),   "tiheureuse/%s/flow",   TIREUSE_UUID);
    snprintf(topic_valve,  sizeof(topic_valve),  "tiheureuse/%s/valve",  TIREUSE_UUID);
    snprintf(topic_status, sizeof(topic_status), "tiheureuse/%s/status", TIREUSE_UUID);

    // GPIO vanne — état sûr par défaut (vanne fermée)
    pinMode(PIN_VANNE, OUTPUT);
    fermer_vanne();

    // GPIO débitmètre — pull-up interne, interruption sur front descendant
    pinMode(PIN_DEBIMETRE, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_DEBIMETRE),
                    on_pulse_debimetre,
                    FALLING);

    // Connexions réseau
    connecter_wifi();
    mqtt_client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
    mqtt_client.setCallback(on_message);
    mqtt_client.setKeepAlive(15);  // Heartbeat MQTT toutes les 15s
    connecter_mqtt();

    // Init du watchdog (pas encore de message → on repart de maintenant)
    derniere_reception_mqtt_ms = millis();

    Serial.println("=== Prêt ===");
}

// =============================================================================
// loop — boucle principale
// =============================================================================
void loop() {
    // Reconnexion automatique si coupure réseau
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[WIFI] Connexion perdue — reconnexion...");
        connecter_wifi();
    }
    if (!mqtt_client.connected()) {
        connecter_mqtt();
    }

    // Traitement des messages MQTT entrants
    mqtt_client.loop();

    unsigned long maintenant_ms = millis();

    // --- Mise à jour volume/débit + publication flow (toutes les secondes) ---
    if (maintenant_ms - dernier_envoi_flow_ms >= FLOW_PUBLISH_INTERVAL_MS) {
        dernier_envoi_flow_ms = maintenant_ms;
        mettre_a_jour_mesures();
        publier_flow();
        verifier_max_volume();
    }

    // --- Heartbeat status (toutes les 5 secondes) ---
    if (maintenant_ms - dernier_envoi_status_ms >= STATUS_PUBLISH_INTERVAL_MS) {
        dernier_envoi_status_ms = maintenant_ms;
        publier_status();
    }

    // --- Watchdog : fermeture vanne si Pi silencieux ---
    verifier_watchdog();
}
