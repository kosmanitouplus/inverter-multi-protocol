# Validation matérielle requise avant publication stable

Base inspectée le 9 octobre 2026 : `main` 0.1.5 à `42c71a7` ; branche `robustness/protocol-detection-mqtt-controls` 0.2.3 à `60f8c6a`. L'utilisateur confirme utiliser 0.2.3 sur mini-PC Home Assistant. Candidate : **0.3.0-rc1** sur `auto/read-only-recovery`.

Les simulations ne valident ni le câblage réel, ni les variantes de firmware, ni l'exposition des périphériques USB à chaud par Supervisor. La fusion dans `main` attend ces résultats. Aucun test d'écriture n'est demandé ni disponible.

## Essai sur mini-PC amd64, puis Raspberry Pi aarch64

1. Sauvegarder l'add-on et ses options. Installer la candidate suivant MISE_A_JOUR_FR.md. Arrêter l'ancien lecteur, laisser le mode protégé actif et lancer AUTO sans câble ; attendre cinq minutes : pas de crash, pas de boucle de redémarrage, état inconnu/offline.
2. Brancher l'USB avec un onduleur connu. Vérifier famille/candidats, vitesse, numéro de série réellement affiché par le constructeur, tension, courant, fréquence et puissance contre l'écran/manuels. Aucune mesure ne doit être inventée pour les champs absents.
3. Débrancher **l'adaptateur USB du mini-PC** pendant une lecture. Vérifier offline, service vivant, puis reprise après rebranchement. Répéter trois fois ; tester un changement de numéro ttyUSB et d'adaptateur. Le même onduleur avec identité fiable doit retrouver le même ID MQTT.
4. Garder l'USB branché, retirer **le câble RS232/RS485 côté onduleur**. Vérifier offline après détection du timeout, aucune boucle de crash, puis reprise avec backoff sans redémarrage. Répéter trois fois, dont une pendant une lecture secondaire.
5. Échanger deux onduleurs du même protocole, avec numéros différents, puis deux de protocoles différents sur le même adaptateur. L'ancien appareil reste offline ; le nouvel appareil ne doit pas recevoir ses IDs. Rebrancher le premier : ses IDs fiables doivent revenir. Tester aussi un onduleur sans numéro exploitable ; vérifier la mention session anonyme et l'absence de promesse d'historique entre reconnexions.
6. Brancher au moins deux ports. Retirer un appareil et confirmer que le second continue de publier. Vérifier les exclusions de ports utilisés par d'autres applications et la déduplication by-id/by-path/ttyUSB.
7. Redémarrer un broker MQTT **de test** (ou en maintenance), puis Home Assistant, puis l'add-on. Les découvertes reviennent ; aucun ancien appareil ne devient online sans lectures nouvelles. Aucun select/number de commande de la candidate ne doit apparaître.
8. Mesurer l'espacement des cycles/mesures. Il ne doit pas être inférieur à `poll_interval`. Les timeouts/lectures supplémentaires peuvent le rendre plus grand ; il ne doit pas y avoir de rafales de rattrapage.
9. Pour chaque modèle Modbus : fournir le manuel, la carte de registres, les paramètres série et le profil ; utiliser exclusivement les fonctions 3/4, puis refaire les deux déconnexions. AUTO PI ne devine pas une carte Modbus.
10. Répéter sur Raspberry Pi aarch64 si la publication doit revendiquer cette plateforme sur matériel. Arrêt de l'add-on pendant une lecture : sortie propre et offline, sans double lecteur au redémarrage.

À transmettre : modèle/firmware de chaque onduleur, plateforme et version HA/Supervisor, type d'adaptateur, options **sans mots de passe**, journaux des branchements, comparaison des valeurs et IDs MQTT avant/après. Signaler chaque modèle resté probable/inconnu. Les résultats de CI amd64/aarch64 doivent aussi être concluants sur le commit candidat exact.

La couverture n'est pas « tous les onduleurs du marché » : les manuels et fixtures de chaque famille ajoutée restent nécessaires. Un échange sans interruption observable entre appareils anonymes, ou entre appareils utilisant le même numéro cloné, n'est pas identifiable avec certitude à partir de cette liaison.
