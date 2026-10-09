# Installer 0.3.0-rc5 dans Home Assistant

Cette version candidate est sur **auto/read-only-recovery**, issue de la 0.2.3 installée. `main` reste en 0.1.5 en attendant les essais matériels ; ne pas cliquer aveuglément sur une mise à jour venant de `main` pour tester cette version.

1. Créer une sauvegarde Home Assistant incluant l'add-on 0.2.3 et copier ses options. Garder cette installation pour pouvoir revenir en arrière.
2. Arrêter l'add-on 0.2.3. Désactiver son démarrage automatique pendant les essais. Un seul lecteur doit utiliser chaque liaison et le client MQTT `inverter-multi-protocol`.
3. Dans **Paramètres → Applications (ou Modules complémentaires) → Boutique → ⋮ → Dépôts**, ajouter :

   ```text
   https://github.com/kosmanitouplus/inverter-multi-protocol#auto/read-only-recovery
   ```

4. Actualiser la boutique et installer **Multi Onduleur Robuste** en version **0.3.0-rc5** depuis ce dépôt de branche. Le nom et le slug restent identiques ; Home Assistant peut néanmoins traiter une autre URL de branche comme un dépôt/installateur distinct. Ce n'est pas une mise à jour automatique en place de l'autre branche.
5. Pour tester la recherche autonome, enregistrer :

   ```yaml
   inverter_name: INVERTER_1
   protocol: AUTO
   port: AUTO
   poll_interval: 5
   inverters: []
   auto_baudrates: []
   exclude_ports: []
   ```

   Si d'autres applications lisent des adaptateurs série, ajouter leurs chemins à `exclude_ports` avant démarrage. Aucun changement de BatMon n'est nécessaire.
6. Démarrer. Lire les journaux : protocole/candidats, vitesse, identifié/probable/inconnu et identité constructeur ou session anonyme. Tester d'abord avec un onduleur connu, puis appliquer la procédure de validation.

**Historique existant** : les IDs de 0.2.3 provenaient d'un nom configuré, pas d'une identité constructeur vérifiée. Pour les préserver sans confondre les appareils, suivre la migration `expected_serial` du README, avec le vrai numéro et l'ancien ID. Pour une nouvelle installation de branche, la sauvegarde du manifeste `/data/inverter-discovery.json` fait partie des données à conserver ; sa restauration doit viser les données de cet add-on, pas la configuration d'une autre application. Sans correspondance vérifiée, de nouveaux IDs sont créés intentionnellement.

Si l'add-on est une installation **locale** plutôt qu'un dépôt GitHub, remplacer uniquement son dossier source par `inverter-multi-protocol` de la branche, conserver les données, actualiser la boutique puis utiliser **Reconstruire**. Ne pas écraser `/data` ni les autres add-ons.

**Retour à 0.2.3** : arrêter la candidate, puis redémarrer l'installation 0.2.3 sauvegardée. Ne pas désinstaller l'ancienne avant validation.

Après validation et fusion, la branche `main` recevra la version stable. Une installation utilisant déjà le dépôt `main` pourra alors actualiser la boutique et utiliser **Mettre à jour**. Conserver les données et les options ; les entrées manuelles restent acceptées. Le passage d'une installation de branche à `main` est une migration d'installation, pas une garantie de transfert automatique de `/data`.

Voir aussi le README du dépôt et docs/VALIDATION_FR.md pour les paramètres et essais matériels.

## Comprendre une recherche AUTO

Le premier passage utilise les huit vitesses courantes et le cadrage préféré, avec un délai maximal de 0,4 seconde par requête. Avec les options par défaut, 40 probes silencieuses représentent environ 20 secondes, hors délais d’ouverture du système. Une réponse doit toujours passer les mêmes contrôles de protocole et de mesures avant identification. Ce délai décrit une recherche, pas une promesse d’identification d’un appareil inconnu ou muet.

Si ce passage ne réussit pas, la recherche reprend tous les réglages avec le délai configuré (3 secondes par défaut) pour ne pas exclure les appareils lents. Ce passage exhaustif peut rester long. Les groupes ne sont plus séparés par poll_interval : ce paramètre reste la cadence des mesures après identification. Un port absent ou impossible à ouvrir déclenche le backoff.

Une liste auto_baudrates vide conserve les 900 combinaisons du passage complet. Un redémarrage recommence la recherche. Les journaux montrent le port, le réglage, la phase fast/full et la cause d’échec. Choisir port AUTO pour les changements d’adaptateur.

## EASUN iSolar SMG II / Sumry

La famille SMG II est documentée par Solar Assistant comme Sumry (https://origin.solar-assistant.io/help/inverters/easun/ISOLAR-SMG-II-4kW-24V/rs232?locale=en). Le profil standard est documenté par https://github.com/syssi/esphome-smg-ii : Modbus RTU sur RS232, 9600 bauds, 8N1, unité 1, lectures fonction 3. La fiche Solar Assistant citée ne certifie pas le modèle 11KP de l’utilisateur ; validation réelle requise.

Pour tester directement : protocol SUMRY, port by-id de l’adaptateur Prolific présent, poll_interval 5. Ce mode ne nécessite pas de fichier de profil et évite la recherche PI. Pour les tests universels garder protocol AUTO et port AUTO : Voltronic PI reste en tête du passage rapide, puis une sonde Sumry est ajoutée si 9600 figure dans les vitesses autorisées. Le passage complet contient alors 901 essais, le premier passage 41 par défaut.

La carte standard retourne le bloc 201–217 et le numéro de série ASCII 186–197. La reconnaissance reste probable car le contenu des registres n’est pas une signature fabricant unique. Un numéro absent ne crée pas d’identité physique. Les firmwares utilisant d’autres registres ou un réveil propriétaire ne sont pas couverts. Aucune commande de réveil ou d’écriture n’est envoyée.

### Variante SMG II 8/11 kW

Source : https://github.com/alcestide/easun-smg-ii-11kw (table des registres et configuration RS232). Le registre 184 indiquant 3, 4, 5 ou 6 sélectionne la carte 8/11 kW. Les tensions réseau/sortie L1 sont à 338/346, la batterie à 277–280 ; elles ne sont pas aux adresses du bloc standard 201–217. Le registre de protocole est revérifié avant chaque lecture principale. Aucun autre numéro ne fait deviner cette carte.

Garder protocol SUMRY et le port Prolific pour l’essai direct après mise à jour. Le journal annoncera la carte choisie et le numéro retourné. Comparer la tension réseau à la photo (228 V), puis la batterie et la sortie. Une sortie à 0 V ou un mode défaut n’empêche pas la disponibilité des mesures lorsque des données cohérentes sont présentes. Les bitmaps ne sont pas une traduction automatique du code LCD 46 ; son sens exact n’est pas confirmé.
