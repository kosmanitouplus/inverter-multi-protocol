# Changements

## 0.2.3

- Rechargement des options sans redémarrage manuel, nouvelle identité MQTT lors d’un changement de nom et retrait des anciennes découvertes gérées.
- Noms d’onduleurs avec espaces et accents acceptés ; noms affichés séparés des IDs MQTT.
- Identifiants existants préservés, champ id facultatif pour conserver l’historique lors d’un changement de libellé.
- Configuration invalide : service vivant, message explicite et reprise après correction, sans boucle watchdog.

## 0.2.2

- Nom affiché permanent : Multi Onduleur Robuste.
- Identité de l’application inchangée : slug inverter_multi_protocol et même dépôt/branche.
- Configuration, noms d’onduleurs, IDs et topics MQTT conservés.
- Procédure de mise à jour automatique Home Assistant documentée ; aucun nouveau nom TEST à chaque version.

## 0.2.1

- Courants de charge total et secteur issus des choix annoncés par l’onduleur.
- Limites et adresse de commande configurables par appareil ; option pour masquer la découverte des contrôles chez les clients.

## 0.2.0

- Service multi-onduleurs résilient, détection PI et profils Modbus RTU/TCP.
- Reconnexion série/MQTT, disponibilité par requête et réglages avec accusé et relecture.
