# v0.10.17 — Estimation thermique sans retour à une mémoire ancienne

**Version non publiée : nouveaux correctifs en cours de validation. Les résultats de l'ancienne révision ne valident pas ces changements.**

**Conflit découvert le 13 octobre :** GitHub possède déjà une v0.10.17
publiée le 12 octobre au commit `c2663536ede116f45c93fdf889a921f2a5654668`.
Elle ajoute une autre politique saisonnière (hybride optionnelle, fermeture
au 31 octobre, verrou persistant). Ce fichier décrit une préparation locale,
pas cette release publique. Ne pas republier v0.10.17 ni pousser cette branche
sans réconciliation explicite avec la version distante. La nouvelle campagne
complète n'est pas lancée tant que la base et la politique ne sont pas résolues.

## Correctifs du 13 octobre

- Interdiction de redémarrer la PAC pendant une mesure protégée, y compris
  depuis une commande de démarrage différée. Le scénario bloquant
  `deadline-1316` retrouve 30,446 °C dans le premier rejeu corrigé.
- Réutilisation d'une certification de moins d'une heure après une pause
  connue de quinze minutes au plus, sans réécrire sa date ni prendre une
  température de tuyau pour une nouvelle mesure du bassin. Une pause longue
  ou inconnue exige toujours une circulation et une stabilité complètes.
- Hystérésis de préservation : terminer une recharge autorisée vers sa cible
  bornée, sans relever cette cible ni contourner le choix manuel/nuit.
  La cible intègre les pertes nocturnes prévues : ne plus demander une chauffe
  vers une température inférieure à l'eau actuelle pour couvrir ces pertes.
- Coupure au 10 octobre local avec passage à Désactivé, reprise manuelle au
  printemps. Les anciens Turbo ne traversent pas cette limite ; un nouveau
  Turbo après coupure est une exception explicite bornée puis retourne à
  Désactivé. Par sécurité, cette exception n'est pas restaurée après reboot.
  Filtration, hors gel, sécurités et noms d'entités conservés. Les installations
  sans sélecteur chauffage gardent leur comportement historique.

Les tests et la nouvelle campagne doivent être terminés avant publication.
Le bilan ci-dessous concerne l'ancienne révision, pas ces correctifs.

## Défaut isolé et correction

Après six heures, une certification n'est plus fraîche. La v0.10.16
abandonnait alors son estimation de refroidissement et relisait le helper
de température mémorisée, sans vieillir cette valeur. Cela pouvait annoncer
une eau prête alors que le bassin avait refroidi, puis reprendre la chauffe
trop tard. La correction conserve la projection opérationnelle de la mesure
datée au passage de cette limite.

Cette estimation n'est pas une nouvelle mesure certifiée : les conditions
de circulation, de stabilité, de fraîcheur et d'apprentissage restent intactes.
Le gain de chauffe reste net et ne commence pas avant la session PAC réelle.
Il ne devient pas une permission de nuit ou de Turbo.

## Validation historique avant les correctifs du 13 octobre

- 232 tests Python passent, dont deux nouveaux tests d'expiration et de session
  de chauffe. La mesure certifiée n'est pas modifiée par la projection.
- Les six régressions de confort candidates issues du bilan v0.10.16 sont
  rejouées par `tools/check_comfort_regressions.py`. Les résultats détaillés
  locaux sont conservés dans `outputs/v0.10.17-critical.json`.
  Les neuf relectures passent sans violation : les cinq cas frontières sont
  entre 30,93 et 30,95 °C à leur échéance. Le cas avec panne de sonde reste
  à 30,55 °C, mais ne régresse plus contre la référence avant v0.10.16.
  Le cas `weather-cover-permission-fault-438` remonte de 30,19 à 30,93 °C ;
  `trajectory-18` (30,48 °C) et `deadline-1261` (27,84 °C) restent insuffisants
  et inchangés. Ces valeurs sont celles du modèle hors ligne, pas des garanties.
- 8 869 cas de composants sans violation, compilation, Ruff ciblé,
  `git diff --check` et analyse des six fichiers YAML réussis.
- La campagne `outputs/release-v0.10.17-balanced` est terminée le
  6 octobre 2026 à 08:02:57 UTC : 2 154 IDs uniques correspondant au manifeste,
  aucune violation de contrôle et empreinte source inchangée
  (`f00a22d33ed51f37a556a1b227c15035f7ef90bc5af6184a56096cb31f4190bf`).
  Le temps de calcul actif est de 2 h 26 min 9 s ; les pauses de la machine
  rendent la durée écoulée beaucoup plus longue.
- 1 633 cas présentent un déficit thermique. Ce compteur comprend notamment
  des arrêts, suspensions, pannes et conditions physiques défavorables :
  il ne représente pas 1 633 défauts logiciels.
- Comparaison à seuil de 0,1 °C : 117 cas s'améliorent contre v0.10.16
  (128 contre la première campagne), mais un cas régresse contre les deux.
  Les six régressions initiales sont récupérées ; cela ne valide pas
  l'ensemble du confort. Les 232 tests Python ont été relancés et passent.

## Régression bloquante et correction proposée

Le scénario `deadline-1316`, sans panne, combine eau initiale à 29 °C,
volet ouvert, soleil fort, faible consommation, mode Intelligent / Smart
fin de saison et autorisation nocturne, avec échéance samedi à 17 h.
La température à l'échéance passe de 30,446 °C dans les deux références
à 30,331 °C : perte de 0,115 °C. Un rejeu de 24 h reproduit ce résultat.

La trace montre une boucle d'arbitrage entre chauffe et certification :
la PAC redémarre après environ cinq minutes d'arrêt, avant les quinze
minutes de circulation et deux minutes de stabilité nécessaires à la
mesure hors chauffe. Elle s'arrête ensuite à nouveau après la durée
minimale de marche. Cette succession retarde la certification et fragmente
la chauffe du matin, sans enfreindre les invariants actuels du simulateur.

Le prochain correctif doit empêcher un redémarrage PAC d'interrompre une
certification en cours, maintenir la circulation requise, respecter
l'anti-cycle et définir une sortie sûre en cas d'échec de mesure.
Il doit ajouter des tests en boucle fermée sur le délai de certification,
les redémarrages et le confort de `deadline-1316`, puis être comparé aux
références avant publication. Aucun moteur, simulateur ou YAML n'a été
modifié pendant cette campagne. Aucune publication ni intervention HA
n'est effectuée à l'issue de ce bilan bloquant.

## Compatibilité et limites

Aucune entité renommée, aucun nouveau helper, aucun fichier YAML à modifier.
Les six YAML complets existants restent dans `examples/`. Le correctif porte
sur le moteur, pas uniquement sur la carte.

La physique simulée n'est pas calibrée sur le bassin réel. Les déficits liés
aux délais, à la météo, aux suspensions et aux pannes ne sont pas tous des bugs.
L'estimation ancienne reste incertaine et doit être corrigée par une nouvelle
mesure réelle. Cette version ne promet ni température garantie à l'heure du
bain, ni optimalité énergétique, ni dimensionnement automatique du Turbo.
