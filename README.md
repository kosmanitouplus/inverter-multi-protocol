# Multi Onduleur Robuste — 0.3.0-rc1

Add-on Home Assistant pour mini-PC **amd64/x86_64** et Raspberry Pi **aarch64**, MQTT et lecture seule.
Cette version candidate part de la branche **0.2.3**, pas du code 0.1.5 de `main`. La fusion stable attend les essais sur les appareils réels.

## Démarrage AUTO

```yaml
inverter_name: INVERTER_1
protocol: AUTO
port: AUTO
poll_interval: 5
inverters: []
auto_baudrates: []
exclude_ports: []
```

Le service inventorie les ports toutes les 2 secondes. Chaque port a son propre travailleur : retirer un adaptateur USB ou débrancher le câble côté onduleur ne termine pas le service et ne bloque pas les autres ports. En mode `port: AUTO`, un nouvel adaptateur est trouvé automatiquement. Avec un chemin explicite, le service attend ce chemin : passer à AUTO pour accepter un adaptateur différent.

Les premières vitesses essayées sont 2400, 9600, 19200, 4800, 1200, 38400, 57600 et 115200 bauds. La recherche s'étend ensuite aux autres vitesses positives standard de pyserial jusqu'à 4 000 000 bauds, avec parité N/E/O et 1/2 bits d'arrêt, 8 bits de données. Les réglages non supportés par le pilote sont rejetés. **Une recherche exhaustive peut être longue** ; `auto_baudrates: [2400, 9600]` permet de la limiter aux vitesses documentées pour les appareils d'une installation. Il ne s'agit pas des fréquences électriques 50/60 Hz de l'onduleur : celles-ci ne sont jamais modifiées.

Deux tentatives de cadrage d'identification au maximum sont commencées par cycle de recherche. Chaque échange a un timeout borné ; les réponses de statut identiques sont réutilisées pour examiner plusieurs codecs. Les échecs de communication déclenchent un backoff de 2 à 60 secondes ; une recherche en cours reprend progressivement. Pas de rafales pour rattraper des cycles manqués : `poll_interval` est un minimum entre débuts de cycles, et les échanges lents peuvent espacer davantage les mesures.

## Protocoles et ports

| Support | Comportement |
|---|---|
| PI16, PI17, PI17INFINI, PI17M058 | Framing de lecture issu de mpp-solar 0.16.56 ; identification et statut stricts |
| PI18, PI18SV, PI18LVX | Idem ; les unités 0.1V/0.1Hz sont normalisées |
| PI30, PI30MAX, PI30REVO, PI30M044, PI30M045, PI30MST, PI41 | Idem ; aucune variante n'est affirmée sur la seule réponse QPI |
| MODBUS_RTU, MODBUS_TCP | Fonctions de lecture 3/4 uniquement ; profil de registres documenté obligatoire, configuré explicitement |
| `/dev/serial/by-id/*`, `/dev/serial/by-path/*` | Prioritaires, alias dédupliqués par chemin physique |
| Ports inventoriés par pyserial et ttyUSB, ttyACM, ttyXRUSB, ttyAMA, ttySC, ttyTHS, rfcomm | Recherche AUTO ; exclusion possible pour les ports utilisés par d'autres services |
| UART intégré, RS232, RS485 | Transport série selon le pilote et le câblage ; un UART connecté à la console système doit être exclu |
| `/dev/hidrawN` | USB HID pris en charge via chemin **explicite** ; pas de sondage automatique de claviers/périphériques HID inconnus |
| `tcp://hôte:port` | Passerelle série ou Modbus TCP, adresse explicite ; pas de balayage réseau |

Les vitesses de recherche sont des **candidats de communication**, pas une certification de chaque couple matériel/protocole. Les paramètres conseillés doivent venir du manuel du modèle ; la documentation mpp-solar donne 2400 bauds comme défaut général. RS485 décrit une liaison électrique, pas la carte de registres. CAN, Bluetooth natif, API cloud et tous les protocoles propriétaires du marché ne sont pas implémentés. Aucun équivalent universel à toute la couverture SolarAssistant n'est revendiqué.

En cas de variantes compatibles partageant la même identification, le statut est `probable` et seules les mesures décodées de façon identique par les candidats sont publiées. Une seule interprétation strictement validée donne `identified`. Aucune réponse fiable donne `unknown`, sans mesures. CRC/checksum, cadrage, longueur annoncée, nombre de champs, nombres, flags, enums et plausibilité sont contrôlés. Les placeholders documentés signifient une mesure absente, jamais zéro.

## Identité et historique

Un port ou le numéro de série USB de l'adaptateur **n'est pas** une identité d'onduleur.

Un numéro de série constructeur utilisable doit être relu deux fois pendant l'identification. Il est revérifié avant et après les lectures. Les IDs MQTT stables sont dérivés de la famille et de ce numéro, indépendamment du port et de l'adaptateur. Une correspondance persistante est stockée dans `/data/inverter-discovery.json`. Deux ports déclarant le même numéro rendent l'identité ambiguë : leurs mesures sont refusées jusqu'à résolution.

**Migration volontaire de l'historique 0.2.3** : après avoir vérifié le numéro réel, conserver l'ancien `inverter_id` (ou `id`) et renseigner `expected_serial` avec un port explicite. Seul cet appareil peut alors recevoir les anciens IDs. Cela évite de rattacher arbitrairement le premier onduleur branché à un historique existant. La correspondance persiste ensuite et peut être retrouvée en AUTO. Le fichier manifeste doit être conservé/copier lors d'un changement d'installation de l'add-on.

```yaml
inverter_name: Onduleur Atelier
inverter_id: INVERTER_1
protocol: AUTO
port: /dev/serial/by-id/usb-votre-adaptateur
expected_serial: "9293333010501"  # remplacer par le numéro réellement vérifié
poll_interval: 5
inverters: []
```

Sans numéro exploitable (ou avec un profil Modbus sans identité), l'appareil reste une **session anonyme** explicitement affichée. Une reconnexion après perte de communication crée une nouvelle session ; les anciennes découvertes anonymes sont retirées. Ce jeton n'est pas présenté comme une identité matérielle. **Deux appareils anonymes échangés sans aucune interruption observable ne peuvent pas être distingués avec certitude.** Le logiciel ne prétend pas le contraire et ne garantit pas leur historique physique. Un numéro constructeur cloné sur deux appareils successifs est aussi indétectable sans autre preuve indépendante.

## Plusieurs ports / appareils

Un seul pool `port: AUTO` gère jusqu'à 32 ports découverts. Les ports déclarés explicitement sont exclus du pool. Exemple mixte :

```yaml
inverters:
  - name: Recherche automatique
    port: AUTO
    protocol: AUTO
    poll_interval: 5
    auto_baudrates: [2400, 9600, 19200]
    exclude_ports: [/dev/serial/by-id/usb-port-utilise-par-une-autre-application]
  - name: Onduleur Modbus
    port: /dev/serial/by-id/usb-rs485
    protocol: MODBUS_RTU
    baud: 9600
    parity: N
    stopbits: 1
    unit_id: 1
    profile: modele_documente
    poll_interval: 5
```

Le profil doit exister dans `/config/inverter-profiles/modele_documente.json`, avec les adresses, fonctions 3/4, types, ordre des mots/octets, échelles et unités **du manuel du modèle**. Ne pas utiliser un exemple comme une carte universelle. Plusieurs unités Modbus peuvent partager un bus si leurs réglages série sont identiques ; les transactions sont verrouillées.

## MQTT et sécurité

Découverte retenue, disponibilité globale et par appareil/requête, Last Will offline. Au retour du broker ou après redémarrage du processus, les découvertes sont rejouées et les disponibilités restent offline jusqu'à des mesures nouvelles. Le message de naissance Home Assistant relance aussi la découverte. Les états de mesure ne sont pas retenus. Les anciens appareils avec identité vérifiée restent archivés offline pour conserver leur historique.

Aucune commande de réglage n'est disponible, aucun abonnement MQTT aux setters. `allow_writes: true` est refusé même avec un protocole explicite ; les anciens champs de contrôle restent acceptés pour migration lorsqu'ils sont désactivés. Les anciennes découvertes select/number présentes dans le manifeste sont retirées. Ne lancer qu'un lecteur par liaison physique.

Le conteneur garde AppArmor et le mode protégé ; accès UART/USB/udev, configuration Home Assistant en lecture seule, sans `full_access`, Docker API ni privilèges supplémentaires. Les identifiants MQTT ne sont pas journalisés. Aucun fichier ou dépôt BatMon n'est modifié.

## Installation et validation

Voir [mise à jour Home Assistant](docs/MISE_A_JOUR_FR.md), [essais matériels avant fusion](docs/VALIDATION_FR.md), [recherche SolarAssistant et sources](docs/RECHERCHE_SOLARASSISTANT_FR.md) et [changelog](inverter-multi-protocol/CHANGELOG.md).

Tests : `python -m pytest tests -q` avec les dépendances épinglées et pytest 8.3.5. La CI exécute les simulations Linux et construit les images nativement sur amd64 et aarch64. Les tests de codecs utilisent des fixtures upstream et des trames synthétiques explicitement cohérentes ; ils ne constituent pas une certification des modèles réels.
