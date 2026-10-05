# Inverter Multi-Protocol — 0.2.1, branche de validation

Surveillance et réglages d’onduleurs dans MQTT/Home Assistant. Plusieurs appareils sont regroupés séparément, avec une disponibilité propre à chacun et à chaque requête. Cette version nécessite encore une validation sur le matériel avant fusion et mise à jour de l’installation active.

## Compatibilité réelle

- RS232 via adaptateur USB et USB HID (`/dev/hidraw…`) : codecs PI16, PI17, PI17INFINI, PI17M058, PI18, PI18SV, PI18LVX, PI30, PI30MAX, PI30REVO, PI30M044, PI30M045, PI30MST et PI41.
- Réseau : mêmes familles via une passerelle série TCP transparente (`tcp://adresse:port`), ou Modbus TCP avec un profil propre au modèle.
- RS485 via adaptateur USB : Modbus RTU, avec adresse de chaque onduleur et profil de registres documenté. Le verrou partagé protège les transactions des appareils d’un même bus.
- `AUTO` : recherche en lecture seule des familles PI16/17/18/30/41, avec réponse d’identification **et** réponse de mesures vérifiées. Les variantes qui retournent la même identification nécessitent une sélection explicite. AUTO ne recherche pas aveuglément des registres Modbus et ne permet jamais d’écrire.

Il n’existe pas de protocole universel. Ces codecs ne constituent pas une validation de tous les modèles commerciaux. Les interfaces propriétaires, CAN, les API cloud, SunSpec et VE.Direct ne sont pas implémentées dans cette version. Aucun profil de marque Modbus n’est inventé : il faut le manuel de registres exact avant de l’ajouter.

## Configuration

La configuration existante `inverter_name`, `port`, `protocol`, `poll_interval` reste utilisée si `inverters` est vide. Les valeurs principales gardent leurs anciens IDs et topics `homeassistant/sensor/mpp_<nom>_<champ>/state` (ou `binary_sensor`). Conserver exactement le nom de l’onduleur et son protocole évite de changer ces identifiants.

Pour plusieurs onduleurs, remplir la liste :

```yaml
inverters:
  - name: INVERTER_1
    port: /dev/serial/by-id/usb-mon-adaptateur
    protocol: PI30
    baud: 2400
    poll_interval: 5
    query_interval: 60
    timeout: 3
    allow_writes: false
  - name: INVERTER_2
    port: tcp://192.168.1.60:8899
    protocol: AUTO
    timeout: 3
```

Utiliser les chemins `/dev/serial/by-id/…` pour retrouver l’adaptateur après reconnexion. Les appareils PI ne peuvent pas partager le même port. Jusqu’à 32 entrées sont acceptées ; le débit physique d’un bus partagé limite leur cadence réelle.

Les mesures principales sont interrogées à la cadence demandée (début à début, sans rafale après un retard). Les autres requêtes de lecture connues sont réparties progressivement. Une requête optionnelle non supportée devient indisponible et bénéficie d’un backoff, sans couper les mesures principales. Les requêtes nécessitant un paramètre non déductible du protocole peuvent être ajoutées via `commands`, chaîne séparée par des virgules ; seules les requêtes de lecture reconnues sont acceptées. Les lectures d’énergie datées sont actualisées au changement de date ; leurs entités gardent des identifiants stables.

## Toutes les valeurs disponibles dans MQTT

Les champs décodés des requêtes qui réussissent produisent automatiquement leurs entités Home Assistant, regroupées sous un appareil par onduleur. Les champs des requêtes supplémentaires ont un préfixe de requête pour ne pas écraser les mesures principales. Les données textuelles, états, firmware et avertissements sont également publiés lorsqu’ils sont décodables.

- `inverter/<nom>/state` : toutes les dernières réponses, leur horodatage, valeurs et unités ; vérifier disponibilité et horodatage avant d’utiliser une valeur conservée.
- `inverter/<nom>/availability` : disponibilité de l’appareil.
- `inverter/<nom>/availability/<requête>` : disponibilité de la requête.
- `inverter/<nom>/identity` et `diagnostics` : identification et capacités découvertes.
- `inverter_multi_protocol/availability` : disponibilité globale, avec testament MQTT.

La découverte Home Assistant est retenue, rejouée après reconnexion et après le message de naissance Home Assistant. Les mesures ne sont pas retenues. Une reconnexion MQTT remet les disponibilités à offline avant de nouveaux échanges. Le manifeste local `/data/inverter-discovery.json` permet de retirer les contrôles précédemment publiés lorsqu’ils sont désactivés.

## Réglages modifiables

Lecture seule par défaut. `allow_writes: true` exige un protocole explicite, l’identification correspondante et des mesures récentes. Pour PI30 et variantes compatibles, les sélecteurs sont proposés seulement si le champ de lecture et la commande correspondante existent :

- `output_source_priority`
- `charger_source_priority`
- `input_voltage_range`
- `battery_type` (activation explicite)
- `max_charging_current` : courant maximal de charge total, choix annoncés par QMCHGCR
- `max_ac_charging_current` : courant maximal de charge secteur, choix annoncés par QMUCHGCR

Les limites de tension sont configurées par le technicien **pour chaque onduleur**, sans valeur imposée pour une installation 12/24/48 V. Les limites de tension ne sont jamais devinées. Les contrôles numériques suivants nécessitent leurs limites autorisées par le fabricant : `battery_bulk_charge_voltage`, `battery_float_charge_voltage`, `battery_recharge_voltage`, `battery_redischarge_voltage`, `battery_cutoff_voltage`. Exemple de structure **à remplacer par les limites exactes du modèle** :

```yaml
allow_writes: true
controls: "output_source_priority,input_voltage_range"
# Pour une tension autorisée, ajouter le nom du contrôle à controls puis :
# write_limits: '{"battery_float_charge_voltage":{"min":MINIMUM,"max":MAXIMUM,"step":0.1}}'
```

Pour les courants PI30, le format par défaut MCHGC/MUCHGC utilise un numéro de machine et deux chiffres de courant (0–99 A). Le format étendu de courant total `charge_current_command: MNCHGC` permet trois chiffres lorsque le manuel du modèle le confirme. La liste effectivement offerte reste limitée aux choix annoncés par l’appareil, éventuellement filtrés par `write_limits`. En parallèle, renseigner `command_unit: 0` à `9` selon l’adresse confirmée ; aucune adresse n’est déduite. En mode « single machine output » confirmé, le numéro 0 est utilisé. Le nombre d’appareils/configurations n’est pas une limite de courant.

Chaque contrôle apparaît sous le même appareil Home Assistant. Sa commande va à `inverter/<nom>/set/<contrôle>`, son état à `inverter/<nom>/settings/<contrôle>`. `command_result` donne `confirmed`, `rejected` ou `unconfirmed`.

Pas d’API de commandes brutes. Une commande retenue, dupliquée, ancienne, hors limites ou provenant d’une ancienne connexion MQTT est rejetée. La réussite exige accusé de réception **et relecture du réglage**. Aucun nouvel essai automatique après une commande d’écriture, même si son accusé est perdu. Un résultat `unconfirmed` signifie que la valeur peut avoir changé : vérifier l’onduleur avant toute autre action. Les autres familles PI restent en lecture seule jusqu’à ajout de mappings documentés. PI41 et certaines variantes rapportant PI30 peuvent rester sans réglages si leur identification n’est pas confirmée.

## Profils Modbus

Placer `<profil>.json` dans `/config/inverter-profiles/`, utiliser `protocol: MODBUS_RTU` ou `MODBUS_TCP`, `profile: <profil>`, `unit_id: 1` (1–247), `baud`, `parity: N|E|O`, `stopbits: 1|2`.

Exemple de **structure fictive**, pas un registre réel à tester :

```json
{"sensors":[{"name":"AC Voltage","address":100,"function":3,"data_type":"uint16","scale":0.1,"unit":"V"}]}
```

Les adresses sont des offsets de protocole, pas les numéros 40001 des manuels. Types : uint/int 16/32/64, float32/64 ; `word_order`, `byte_order` big/little ; `scale` et `offset`. Fonctions de lecture 3/4 uniquement. Une écriture nécessite un champ `write` avec `function` 6/16 et `min`, `max`, `step`, ainsi que `allow_writes: true`. Aucun registre n’est writable sans ce mapping explicite. Plusieurs unités sur un bus doivent avoir le même débit et cadrage. L’identité Modbus repose sur le profil et l’adresse configurés, pas sur une reconnaissance automatique du fabricant.

## Pannes, arrêt et construction

Absence/retrait du câble : processus vivant, appareil offline, retries 2–60 s, nouvelles ouvertures du port. Un onduleur en panne ne fait pas tomber les autres. L’absence ou la reconnexion MQTT est gérée indépendamment. Les échanges et attentes de verrou sont bornés ; SIGTERM/SIGINT arrête les travailleurs et publie offline.

Images natives aarch64 et amd64, base Alpine Home Assistant 3.22, dépendances Python épinglées, environnement virtuel sans chemin contenant une version mineure Python. armhf/armv7/i386 sont retirés. Pas de modification ni de déploiement automatique sur le Raspberry.

Voir [audit et validation matérielle](docs/VALIDATION_FR.md). Sources de formats : [mpp-solar](https://github.com/jblance/mpp-solar), [spécification Modbus](https://www.modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf), [découverte MQTT Home Assistant](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery).

## Installations clients et accès technicien

Voir [configuration technicien](docs/TECHNICIEN_FR.md) pour activer des contrôles par appareil et réserver les commandes aux techniciens. `expose_controls: false` supprime leur découverte Home Assistant, mais conserve leur API MQTT pour le technicien. Cette option masque les contrôles ; elle ne constitue pas une autorisation par utilisateur. Le broker doit appliquer des ACL empêchant les comptes clients et la connexion MQTT Home Assistant de publier sur `inverter/+/set/+`, et permettre cette publication uniquement au compte technicien. Le compte de l’add-on doit pouvoir s’abonner à ces commandes et publier les états/résultats. Ne pas donner aux clients les droits d’administration permettant de changer ces règles.
