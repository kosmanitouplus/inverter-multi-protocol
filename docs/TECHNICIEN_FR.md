# Lecture seule — 0.3.0-rc1

Les anciennes commandes de réglage 0.2.x sont retirées dans cette candidate. Aucun setter PI, aucune fonction Modbus 6/16, aucun abonnement MQTT `inverter/+/set/+`. `allow_writes: true` est refusé explicitement ; le passer à false avant migration. `expose_controls`, `controls`, `write_limits`, `charge_current_command` et `command_unit` sont tolérés pour reprendre d'anciennes options désactivées, sans créer de voie d'écriture.

Les profils Modbus existants peuvent contenir des métadonnées `write` ; celles-ci ne sont jamais utilisées pour communiquer. Ne documenter que les registres lisibles, leurs fonctions 3/4, types, échelles et unités. Un profil n'identifie pas automatiquement un appareil physique : sans méthode d'identité documentée, sa session reste anonyme.

Conserver `/data/inverter-discovery.json` pour les correspondances d'IDs et la découverte retenue. Ne pas modifier une correspondance de numéro de série pour réaffecter l'historique d'un autre appareil. Utiliser `expected_serial` pour migrer volontairement un ancien ID après vérification du numéro réel.

Le pool AUTO sonde les ports série disponibles : exclure les ports déjà utilisés par une autre application et les consoles UART. Les sondes sont des lectures **des protocoles connus**, pas une garantie sémantique pour un périphérique utilisant un protocole inconnu. Les claviers/périphériques HID et le réseau ne sont pas balayés automatiquement.

Sources, couverture, limites et procédure : README.md, RECHERCHE_SOLARASSISTANT_FR.md, VALIDATION_FR.md et MISE_A_JOUR_FR.md. Le dépôt BatMon n'est pas concerné.
