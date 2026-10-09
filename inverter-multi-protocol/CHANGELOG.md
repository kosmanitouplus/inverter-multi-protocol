# Changements

## 0.3.0-rc5 — 9 octobre 2026 (candidate)

- Sélection de la carte Sumry SMG II 8/11 kW lorsque le registre 184 retourne un protocole 3–6 ; profil standard conservé sinon.
- Lecture des tensions L1 à 338/346, batterie à 277–280 ; valeurs 0 en sortie acceptées lorsque réseau ou batterie valide, même en défaut.
- Carte vérifiée avant chaque bloc principal, compatibilité probable et vérification série conservées ; fonction 3 uniquement.
- PV2 limité aux protocoles 3/4, fréquence et bitmaps défaut/avertissement optionnels documentés.
- Diagnostic du bloc rejeté incluant carte, protocole, mode et tensions ; tests des quatre variantes et changement de protocole.

## 0.3.0-rc4 — 9 octobre 2026 (candidate)

- Ajout SUMRY / EASUN SMG II standard : Modbus RTU sur RS232, 9600 8N1, unité 1 par défaut, fonction 3 uniquement.
- AUTO conserve le premier groupe Voltronic PI, puis teste Sumry à 9600 avant le repli complet ; SUMRY explicite évite le balayage pour le diagnostic.
- Deux blocs de mesures validés avant compatibilité probable ; numéro ASCII vérifié pour identité et swaps, CRC/unité/fonction/longueur/plausibilité stricts.
- Mesures réseau, sortie, batterie et capteurs PV optionnels ; aucun réglage ni séquence propriétaire de réveil.
- Firmware standard seulement, variante EASUN SMG II 11KP à confirmer sur matériel.

## 0.3.0-rc3 — 9 octobre 2026 (candidate)

- Première recherche courte : vitesses courantes, cadrage préféré, délai maximal de 0,4 s par requête.
- Suppression de l’attente poll_interval entre les groupes de détection ; cadence des mesures conservée.
- Repli sur tous les réglages avec le délai configuré pour les appareils plus lents ; backoff immédiat si le port ne peut pas être ouvert.
- Identité et mesures retrouvent le délai normal après détection ; contrôles stricts conservés.

## 0.3.0-rc2 — 9 octobre 2026 (candidate)

- Journaux AUTO : port exact, vitesse, cadrage, famille interrogée et cause du dernier échec ; diagnostic MQTT incluant le port.
- Suppression des annonces répétitives de construction des 14 codecs.
- Recherche et validations strictes inchangées ; une liste auto_baudrates vide explore les 900 combinaisons, et un redémarrage recommence la recherche.

## 0.3.0-rc1 — 9 octobre 2026 (candidate, validation matérielle requise)

- Mode AUTO autonome : inventaire hotplug multiports, alias by-id/by-path dédupliqués, changement d'adaptateur et ports ttyXRUSB/UART additionnels.
- Déconnexions USB et câble côté onduleur : indisponibilité, reprise sans crash et backoff, travailleurs indépendants.
- Recherche progressive des 14 codecs PI, vitesses standard positives de 50 à 4 000 000 bauds, parités N/E/O et 1/2 bits d'arrêt ; limites de couverture et paramètres documentés.
- Statuts identifié/probable/inconnu, variantes ambiguës explicites, mesures communes validées seulement ; contrôles stricts des trames, champs, unités, enums et valeurs.
- Identité constructeur relue et vérifiée autour des mesures ; ID stable indépendant de l'adaptateur, refus des numéros dupliqués, sessions anonymes sans identité inventée.
- Migration sûre des historiques 0.2.3 avec expected_serial et correspondance persistante.
- Découverte MQTT persistée/rejouée après redémarrage, disponibilités remises offline avant lectures fraîches ; retrait des sessions anonymes et anciens contrôles gérés.
- Suppression complète des setters et abonnements MQTT de réglage ; fonctions Modbus 3/4 uniquement, profils documentés explicites.
- Cadence poll_interval sans rattrapage, arrêt interruptible, logs limités ; correction du framing Modbus TCP avec transaction basse >= 128.
- Tests automatisés des deux déconnexions, changement d'onduleur/adaptateur, identités absentes/dupliquées, codecs multiples, vitesses/cadrages, reconnexion MQTT et migration.
- Documentation de recherche SolarAssistant, installation de branche et validation matérielle amd64/aarch64. Main et BatMon inchangés.

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
