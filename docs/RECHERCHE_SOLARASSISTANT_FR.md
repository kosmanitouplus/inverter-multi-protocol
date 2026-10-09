# Recherche SolarAssistant, forums et GitHub — 9 octobre 2026

La documentation publique décrit les comportements utilisateur, mais ne permet pas d'affirmer connaître l'algorithme interne de SolarAssistant. Aucune implémentation propriétaire n'a été copiée.

| Source examinée | Observation et application dans cette version |
|---|---|
| [SolarAssistant : Livoltek Hyper USB série](https://solar-assistant.io/help/inverters/livoltek/HYPER/usb-serial) | Sélection explicite du modèle et des ports ; le port USB apparent peut nécessiter une liaison RS485 particulière. Garder les profils par modèle et la distinction transport/protocole. |
| [SolarAssistant : Synerji/MUST RS485](https://solar-assistant.io/help/inverters/synerji/SYSV/rs485) | Configuration dépendante de la famille et du câblage ; un équipement parallèle n'implique pas forcément un câble par appareil. Ne pas déduire la topologie du nombre de ports. |
| [SolarAssistant : MQTT vers Home Assistant](https://solar-assistant.io/help/home-assistant/setup) | Intégration MQTT et découverte. Retenir les configurations et republier après les redémarrages. |
| [Changelog officiel SolarAssistant](https://origin.solar-assistant.io/help/updates/changelog) | Corrections spécifiques de détection, d'échelles, d'identifiants et de modèles. Requérir une validation stricte et des tests par variante plutôt qu'une promesse universelle. |
| [GitHub mpp-solar : configuration](https://github.com/jblance/mpp-solar/blob/master/docs/configfile.md) | Protocoles/ports/vitesses explicites, plusieurs onduleurs, cadence configurée. Reprendre les codecs de lecture épinglés et séparer les travailleurs. |
| [GitHub mpp-solar : usage](https://github.com/jblance/mpp-solar/blob/master/docs/usage.md) | Série/HID, requêtes d'identité et défaut 2400 bauds. Détecter avec des lectures connues ; maintenir HID explicite. |
| [Références de protocoles mpp-solar](https://github.com/jblance/mpp-solar/blob/master/docs/README.md) | Documents PI16/17/18/30/41 distincts. Les vitesses testées sont des candidats, le manuel matériel demeure la référence. |
| [GitHub : Anenji/Sumry, issue 552](https://github.com/jblance/mpp-solar/issues/552) | Un appareil fonctionnant avec SolarAssistant ne devient pas compatible PI30 automatiquement. Un échec d'identité est un résultat inconnu, pas une raison de fabriquer des mesures. |
| [Forum HA : problème MQTT SolarAssistant](https://community.home-assistant.io/t/solarassistant-not-publishing-to-mqtt-genserver-noproc-err/908722) | Retour utilisateur sur un port ttyXRUSB présent malgré un problème de publication. Ajouter ce type de port et tester séparément série et MQTT. |
| [Forum HA : accès UART/TTL](https://community.home-assistant.io/t/solar-assistant-connect-to-daly-bms-directly-via-uart-ttl-pin-6-8-10/722067) | Demande d'accès direct aux UART ; inventorier aussi les ports non USB disponibles, avec exclusions. Il ne s'agit pas d'ajouter un protocole batterie à cet add-on. |
| [Forum HA : anciennes entités MQTT](https://community.home-assistant.io/t/cannot-delete-fake-mqtt-data-entries/919673) | Retours sur des entités persistantes après essais. Retirer les découvertes des sessions anonymes terminées et des anciens contrôles gérés ; ne créer une mesure qu'après validation. |
| [Home Assistant : configuration des add-ons/apps](https://developers.home-assistant.io/docs/apps/configuration/) | UART/USB/udev, données persistantes, schémas et configuration en lecture seule. Conserver le mode protégé et éviter les privilèges globaux. |
| [Supervisor : gestion Git](https://github.com/home-assistant/supervisor/blob/main/supervisor/store/git.py), [hash du dépôt](https://github.com/home-assistant/supervisor/blob/main/supervisor/store/utils.py) | Une URL de branche peut constituer une autre installation. Documenter explicitement la migration et le transfert des données plutôt que promettre une mise à jour automatique en place. |

Les témoignages des forums servent à définir des scénarios de régression. Les formats de communication sont fondés sur les codecs/références techniques, jamais sur une anecdote seule. La candidate reste à valider avec les onduleurs réels de l'utilisateur.
