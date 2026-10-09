# Installer 0.3.0-rc1 dans Home Assistant

Cette version candidate est sur **auto/read-only-recovery**, issue de la 0.2.3 installée. `main` reste en 0.1.5 en attendant les essais matériels ; ne pas cliquer aveuglément sur une mise à jour venant de `main` pour tester cette version.

1. Créer une sauvegarde Home Assistant incluant l'add-on 0.2.3 et copier ses options. Garder cette installation pour pouvoir revenir en arrière.
2. Arrêter l'add-on 0.2.3. Désactiver son démarrage automatique pendant les essais. Un seul lecteur doit utiliser chaque liaison et le client MQTT `inverter-multi-protocol`.
3. Dans **Paramètres → Applications (ou Modules complémentaires) → Boutique → ⋮ → Dépôts**, ajouter :

   ```text
   https://github.com/kosmanitouplus/inverter-multi-protocol#auto/read-only-recovery
   ```

4. Actualiser la boutique et installer **Multi Onduleur Robuste** en version **0.3.0-rc1** depuis ce dépôt de branche. Le nom et le slug restent identiques ; Home Assistant peut néanmoins traiter une autre URL de branche comme un dépôt/installateur distinct. Ce n'est pas une mise à jour automatique en place de l'autre branche.
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
