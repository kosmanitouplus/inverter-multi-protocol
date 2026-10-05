# Réglages par onduleur — version 0.2.2

Cette version ajoute les courants PI30 et laisse le technicien définir les contrôles et limites pour chaque appareil. Le protocole pilote la trame, son format et les champs de relecture ; chaque marque/variante peut nécessiter son propre mapping. Un protocole autodétecté reste en lecture seule : sélectionner son protocole exact avant d’activer les commandes.

## Activer les réglages connus

Dans les options Home Assistant, conserver les anciens paramètres simples, mais compléter la liste `inverters`. Dès qu’elle est remplie, elle remplace la configuration simple. Garder **exactement** le nom et le port existants pour conserver les entités.

```yaml
inverters:
  - name: INVERTER_1
    port: /dev/serial/by-id/REMPLACER_PAR_VOTRE_PORT_ACTUEL
    protocol: PI30
    baud: 2400
    poll_interval: 5
    query_interval: 60
    timeout: 3
    allow_writes: true
    expose_controls: true
    controls: "max_charging_current,max_ac_charging_current,output_source_priority,charger_source_priority,input_voltage_range"
```

Après démarrage, les requêtes QPIRI, QMCHGCR et QMUCHGCR sont prioritaires. Les contrôles apparaissent après une réponse valide d’identification, de mesures, de réglages et, pour les courants, de choix autorisés. Si une requête est refusée, son contrôle n’est pas inventé. Les courants sont des sélecteurs contenant des valeurs comme `40 A`, pas des nombres arbitraires.

- `max_charging_current` : maximum de charge total. Ce n’est pas le courant instantané mesuré, ni nécessairement le courant solaire seul.
- `max_ac_charging_current` : maximum de charge secteur.
- `battery_cutoff_voltage` : tension basse de coupure (PSDV, champ Battery Under Voltage).
- `battery_recharge_voltage` et `battery_redischarge_voltage` : seuils de bascule/reprise propres au protocole, à distinguer de la coupure basse.
- `battery_bulk_charge_voltage` et `battery_float_charge_voltage` : tensions de charge bulk et float.

Pour ajouter la coupure basse, compléter `controls` avec `battery_cutoff_voltage`, puis `write_limits` avec un objet JSON comportant ses limites **choisies par vous pour cet appareil** :

```text
{"battery_cutoff_voltage":{"min":VOTRE_MINIMUM,"max":VOTRE_MAXIMUM,"step":0.1}}
```

Remplacer les marqueurs par des nombres avant de coller cette chaîne dans `write_limits`. Les bornes sont configurables, non fixées à 48 V ; leur format doit correspondre au protocole, par exemple PI30 attend deux chiffres et une décimale pour PSDV. Répéter ces limites pour les autres contrôles de tension désirés. Il n’y a pas de liste de réglages universelle pour toutes les marques. Modbus utilise les limites et fonctions de chaque registre du profil du modèle.

## Courants supérieurs à 99 A et machines parallèles

Le format par défaut MCHGC/MUCHGC code un numéro de machine et deux chiffres de courant. Pour le courant **total** d’un modèle documenté utilisant le format trois chiffres, sélectionner `charge_current_command: MNCHGC` ; la relecture reste Max Charging Current dans QPIRI. Ce choix n’active pas le format trois chiffres pour le courant secteur. Les valeurs non représentables sont exclues. Les choix doivent toujours figurer dans la réponse de capacité de l’appareil.

Si Output Mode indique « single machine output », l’adresse 0 est utilisée. Pour une installation parallèle, renseigner explicitement `command_unit` entre 0 et 9 selon le protocole et le numéro de machine réel. L’ordre des entrées dans la liste Home Assistant ne définit pas cette adresse.

## Distinguer clients et techniciens

`expose_controls: true` publie les sélecteurs et nombres dans Home Assistant. `expose_controls: false` les retire de la découverte, tout en conservant les commandes MQTT pour votre outil technicien :

```text
Topic : inverter/INVERTER_1/set/max_charging_current
Payload : 40 A
QoS : 0
Retain : false
```

Réponse dans `inverter/INVERTER_1/command_result`, valeur confirmée dans `inverter/INVERTER_1/settings/max_charging_current`. Ne pas utiliser une commande MQTT retenue.

Pour **empêcher** les modifications côté client, configurer les droits du broker :

1. Compte technicien : publication sur les topics `inverter/+/set/+` autorisée.
2. Comptes clients, y compris la connexion MQTT Home Assistant : publication sur ces topics interdite ; lecture des états autorisée.
3. Compte de l’add-on : abonnement aux commandes et publication des états, disponibilités, découverte et résultats autorisés ; il n’a pas à publier de commandes.

Masquer une carte du tableau de bord ou ne pas publier la découverte des contrôles n’empêche pas, à lui seul, un appel au service mqtt.publish. Les ACL doivent s’appliquer au broker et ne pas être annulées par une permission globale de publication. Le technicien publie avec son propre compte. Les clients ne doivent pas avoir les accès administrateur à l’add-on/broker. La mise en place de ces comptes et ACL dépend du serveur installé et n’est pas effectuée automatiquement par l’add-on.

## Validation d’une modification

Les commandes périmées, retenues, dupliquées ou hors limites sont refusées. Réglages et capacités doivent être frais et disponibles. L’écriture n’est envoyée qu’une fois ; `confirmed` exige un ACK et une relecture correspondant à la valeur demandée. `unconfirmed` indique un résultat inconnu : pas de réessai automatique. La version 0.2.2 est validée par simulation ; valider le format et la relecture sur chaque modèle avant déploiement chez un client.

## Mise à jour depuis GitHub

Ajouter à la boutique le dépôt `https://github.com/kosmanitouplus/inverter-multi-protocol#robustness/protocol-detection-mqtt-controls`, rechercher les mises à jour, puis mettre à jour l’application de ce dépôt en 0.2.2. Une copie installée sous `local_...` ne reçoit pas cette mise à jour GitHub : installer l’application du dépôt et recopier ses options une fois, en arrêtant l’ancienne avant de démarrer la nouvelle sur le même port.
