# Audit et validation avant fusion

## État initial et corrections

Le dépôt main audité contient la version **0.1.5**, alors que l’installation signalée utilise **0.1.6**. Les différences propres à l’installation ne figurent donc pas nécessairement dans ce dépôt. Sauvegarder la configuration et la copie locale avant d’envisager de remplacer l’add-on actif.

Le lanceur quittait immédiatement quand le câble était absent. Il exécutait uniquement QPIGS avec le daemon mpp-solar ; la cadence et les erreurs dépendaient de ce daemon. Le patch MQTT était copié dans un chemin Python 3.14 codé en dur, avec une base latest et les anciennes architectures. Les listes upstream destinées à la découverte incluent aussi des setters ; elles ne peuvent pas être utilisées telles quelles pour sonder un appareil.

La version 0.2.0 remplace le daemon par un service avec travailleurs indépendants, utilise mpp-solar 0.16.56 comme codec, filtre les requêtes de lecture, vérifie les trames/checksums et isole les tables de codecs mutées par les variantes upstream. Les commandes d’écriture sont séparées, limitées et nécessitent une relecture. Les anciens topics principaux sont conservés.

## Vérification logicielle

`python -m pytest tests -q` avec les dépendances de `inverter-multi-protocol/requirements.txt` et pytest 8.3.5. La CI utilise Linux/Python 3.12 et construit les images nativement sur amd64 et aarch64.

Les tests couvrent les plans de lecture des 14 codecs, les identifications et CRC, le refus des trames tronquées, les variantes isolées, la détection interrompable, les limites et commandes périmées, l’accusé/relecture et la perte d’accusé, Modbus RTU/TCP multi-unités et endianness, MQTT tardif/reconnexion réelle, découverte retenue, anciens IDs, commandes MQTT retenues, démarrage sans câble ni broker, arrêt du processus. Une simulation Linux par pseudo-port série vérifie câble absent, connexion, retrait, reconnexion et maintien d’un second onduleur ; elle est ignorée sur macOS en raison du comportement différent des pseudo-ports.

## Test matériel obligatoire sur Raspberry

La branche n’est pas une mise à jour de production. Ne pas fusionner tant que cette procédure n’est pas concluante. Elle ne demande aucun changement à BatMon.

1. Sauvegarder les options, les logs et la copie locale de 0.1.6. Préparer une copie locale de la branche `robustness/protocol-detection-mqtt-controls` dans le répertoire d’add-ons de test, avec slug `inverter_multi_protocol_test` et nom de test. Changer également le `client_id` MQTT dans cette copie en `inverter-multi-protocol-test` pour éviter une collision si deux instances sont accidentellement actives. Conserver les réglages désactivés.
2. Ne connecter qu’un lecteur au port. Arrêter temporairement l’instance 0.1.6 avant tout test utilisant son onduleur. Démarrer la copie de test **câble onduleur absent** : attendre 5 minutes ; son état doit rester started, aucun restart watchdog, MQTT doit afficher offline. Le câble batterie actuellement débranché n’est pas celui de ce test.
3. Rebrancher le câble onduleur : des mesures doivent arriver sans redémarrer, après le backoff (jusqu’à 60 secondes). Vérifier tension, fréquence, température et champs principaux contre l’écran de l’appareil. Vérifier que le même appareil Home Assistant et les anciens IDs reçoivent ces mesures.
4. Retirer à chaud le câble onduleur ; vérifier offline après le délai d’échange, absence de crash et reprise automatique après reconnexion. Répéter trois fois, dont une fois pendant une requête optionnelle. Avec un deuxième onduleur disponible, vérifier qu’il continue de publier pendant ces essais.
5. Sur la copie de test, tester `AUTO` en lecture seule ; comparer la famille détectée au protocole connu. Pour une variante, vérifier ensuite son protocole explicite. Laisser les lectures supplémentaires se découvrir pendant 15 minutes ; contrôler les valeurs contre le manuel, les erreurs de requêtes non supportées et la cadence sans rafales.
6. Dans une fenêtre de maintenance, arrêter/reprendre le broker MQTT utilisé par la copie, ou pointer cette copie vers un broker de test et démarrer celui-ci après l’add-on. L’add-on doit rester vivant, reconstruire la disponibilité sur des mesures nouvelles et republier la découverte. Vérifier aussi le redémarrage de Home Assistant. Ne pas interrompre le broker partagé en production hors maintenance.
7. Pour Modbus, fournir **la marque, le modèle, le manuel, les adresses et le format exacts**. D’abord tester seulement des registres de lecture connus sur RTU et TCP. Sur un bus multi-unités, rendre une unité indisponible ; les autres doivent continuer. Une passerelle TCP transparente PI et un appareil Modbus TCP sont deux configurations distinctes.
8. Seulement après validation de lecture : sélectionner le protocole exact, activer un unique réglage réversible expressément autorisé par le manuel, noter sa valeur originale. Modifier depuis Home Assistant, vérifier `confirmed`, l’écran et la relecture MQTT, puis remettre la valeur originale. Vérifier qu’une commande retenue ou périmée n’est pas exécutée. Ne pas tester de limites de tension tant que les limites du modèle ne sont pas renseignées. En cas d’`unconfirmed`, vérifier physiquement la valeur avant toute nouvelle commande.
9. Arrêter l’add-on de test pendant une lecture ; vérifier arrêt borné et disponibilité globale offline. Redémarrer l’instance 0.1.6 pour revenir à l’installation initiale, sans lancer les deux lecteurs simultanément.

Transmettre modèle exact, connexion, options sans mots de passe, logs des étapes 2–6, valeurs comparées et résultat du réglage autorisé. Sans ces éléments, les codecs et transports sont validés par simulation, pas les appareils réels. Aucun support « pratiquement tous les onduleurs » n’est revendiqué.
