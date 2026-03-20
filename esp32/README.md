# TiBeer ESP32 — Firmware

Firmware Arduino pour ESP32 WROOM-32.
Pilote l'électrovanne et le débitmètre via MQTT.

## Matériel

- ESP32 WROOM-32
- Électrovanne 12V (relais ou MOSFET entre ESP et vanne)
- Débitmètre à impulsions (YF-S201 ou similaire)

## Câblage

| ESP32 GPIO | Composant |
|-----------|-----------|
| 26        | Commande relais/MOSFET → électrovanne (DAC2, pas de SPI, libre au boot) |
| 27        | Signal débitmètre (fil jaune) + pull-up 10kΩ vers 3.3V (ADC2, pas de SPI) |
| GND       | GND commun |

> Le signal du débitmètre doit être en 3.3V max sur l'ESP32 (pas 5V direct).

## Librairies Arduino requises

Dans l'IDE Arduino → Gestionnaire de bibliothèques :

- **PubSubClient** by Nick O'Leary (v2.8+)
- **ArduinoJson** by Benoit Blanchon (v6+)

## Configuration

Éditer `config.h` avant de flasher :

```cpp
#define WIFI_SSID      "MonReseau"
#define WIFI_PASSWORD  "MonMotDePasse"
#define MQTT_BROKER_IP "192.168.1.10"   // IP du Pi
#define TIREUSE_UUID   "b7100a7b-..."   // UUID généré par install.sh
#define PIN_VANNE      18
#define PIN_DEBIMETRE  23
```

## Topics MQTT

| Direction | Topic | Contenu |
|-----------|-------|---------|
| Pi → ESP  | `tiheureuse/<uuid>/cmd`    | `{"action":"open","max_ml":450,"calibration_factor":6.5}` |
| Pi → ESP  | `tiheureuse/<uuid>/cmd`    | `{"action":"close"}` |
| ESP → Pi  | `tiheureuse/<uuid>/flow`   | `{"volume_ml":120.5,"debit_cl_min":32.1,"total_pulses":47}` |
| ESP → Pi  | `tiheureuse/<uuid>/valve`  | `{"open":true}` |
| ESP → Pi  | `tiheureuse/<uuid>/status` | `{"uptime_ms":12000,"vanne_ouverte":false}` |

## Sécurités embarquées

- **Watchdog** : si aucun message MQTT reçu depuis 10s → fermeture vanne automatique
- **Volume max** : fermeture automatique quand `volume_ml >= max_ml`
- **Reconnexion MQTT** : si broker injoignable → fermeture vanne préventive
- **État sûr au boot** : vanne toujours fermée au démarrage
