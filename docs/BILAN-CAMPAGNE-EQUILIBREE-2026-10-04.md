# Bilan des 2 154 simulations — 4 octobre 2026

## Résultat vérifié

La campagne `outputs/balanced-2026-10-04` est terminée. Le manifeste et les
résultats contiennent exactement **2 154 identifiants distincts**, chaque cas
correspondant à son entrée du manifeste. Durée réelle : **5 317,76 secondes**,
soit 1 h 28 min 38 s, sur quatre processus locaux.

**Aucune violation des règles contrôlées**, aucune exception de simulation :
circulation minimale lorsque la PAC est commandée, respect de l'arrêt forcé et
du chauffage désactivé, événements de décision appliqués, retour hors Turbo à
l'expiration dans les scénarios dédiés. Les tests Python étaient 227/227 réussis
au lancement. Cela ne signifie ni absence universelle de défaut, ni température
garantie, ni énergie globalement optimale.

| Groupe | Cas terminés | Cas sous 30,8 °C à l'échéance de référence |
|---|---:|---:|
| Régressions historiques, pas physique 30 s | 4 | 0 |
| Trajectoires météo sur trois jours | 24 | 13 |
| Couverture de tous les triplets | 394 | 357 |
| Météo / volet / décision / incident | 480 | 417 |
| Chauffage / filtration / énergie | 224 | 171 |
| Horaires de départ et échéance midi / 17 h | 432 | 328 |
| Huit durées Turbo et expiration | 128 | 60 |
| Changements de décision | 360 | 278 |
| Frontières de température | 108 | 40 |
| **Total** | **2 154** | **1 664** |

## Pourquoi les 1 664 déficits ne sont pas 1 664 bugs

Le décompte est effectué même pour un arrêt forcé, un chauffage désactivé,
une suspension, une PAC qui reste physiquement inactive, une eau à 18 °C ou
une météo interdisant la baignade. Il mesure une échéance extérieure de
référence, pas toujours un engagement de baignade effectivement retenu.
Le mode Automatique est testé avec `gestion_pac_auto: false`, valeur du fichier
exemple : son absence de commande de chauffe n'est donc pas une régression.
Cette campagne ne valide pas la variante Automatique activée.

Les populations sont choisies pour stresser le logiciel, et ne représentent
pas la fréquence des situations réelles. Le taux global de déficit n'est pas
un taux d'échec estimé pour l'installation.

L'analyse des traces n'a repéré aucun déplacement vers une date ultérieure
d'un objectif déjà retenu, avant sa journée d'usage, hors annulation météo
explicite. Elle ne prouve pas que la première sélection correspond toujours
à la date de baignade souhaitée par l'utilisateur. Une échéance du simulateur
n'est pas une consigne explicite de date transmise au moteur.

## Points de confort encore à examiner

Des cas Smart sans arrêt forcé ni panne restent sous la cible. Exemples
reproductibles dans les résultats :

- `weather-cover-permission-fault-438` : départ vendredi 8 h, eau 29 °C,
  soleil, volet ouvert, nuit autorisée, consommation maison 8 kW : **30,19 °C
  samedi midi**, objectif conservé et signalé en retard.
- `trajectory-18` : révision météo, volet alternant, journée seulement,
  redémarrage : **30,48 °C samedi midi**, objectif conservé mais en retard.
- `deadline-1261` : départ vendredi 18 h, volet ouvert, journée seulement :
  **27,84 °C samedi midi**. Le délai réduit et les pertes peuvent rendre la
  cible impossible sans changer les contraintes ; ne pas conclure à un bug.

Une comparaison exploratoire avec une enveloppe physique optimiste (chauffe
Turbo instantanée pendant tous les créneaux permis, capteurs parfaits, aucun
délai de certification ou de compresseur) trouve **89 cas** dont l'écart dépasse
0,3 °C alors que cette enveloppe atteint 30,8 °C, parmi 457 cas Fin de saison
Smart / Intelligent / eau initiale >=29 °C / décision eco ou night / sans panne
PAC ou sonde / sans événement manuel. Ce sont **des candidats à analyser**, pas
89 défauts établis : certains n'ont pas de baignade retenue ou ont une annulation
météo, et l'enveloppe utilise plus de Turbo que la stratégie Smart. Ce calcul
n'est pas un oracle d'optimalité ni une preuve de faisabilité sur le bassin réel.

La trace peut afficher un statut `ready` avant que la température physique
ne baisse ensuite sous la cible. La qualité et l'âge de la certification,
l'estimation des pertes et le contrôle de maintien méritent une vérification
conjointe ; une comparaison avec la température physique simulée seule ne
suffit pas à attribuer la cause au moteur.

## Corrections proposées, non appliquées dans ce suivi

1. Ajouter un verdict de confort distinct : objectif retenu ou non, annulé,
   réalisable dans les créneaux autorisés, menacé ou impossible. Ne jamais
   appeler « réussi » un scénario uniquement parce que la circulation est sûre.
2. Calculer avant l'échéance le besoin thermique net, les pertes jusqu'au bain,
   le temps réellement disponible et une marge liée à l'incertitude météo.
   Signaler tôt le risque au lieu d'attendre le statut `late`.
3. Dimensionner un renfort à partir du déficit et du gain net appris ; annoncer
   la température/heure estimée et proposer une autorisation supplémentaire si
   nécessaire. Ne pas activer implicitement un Turbo ou une nuit interdite.
4. Vérifier le maintien après un premier passage à la cible, en tenant compte
   de l'âge de la certification, du volet réel et des pertes nocturnes.
5. Compléter la validation par les variantes `gestion_pac_auto: true`, une
   date de baignade imposée explicitement, et des contraintes/mesures
   hydrauliques réelles. Comparer plusieurs marges thermiques avec les exports
   HA pour calibrer la physique avant de promettre une heure de disponibilité.

## Limites et état de publication

Tous les triplets de la grille initiale sont couverts, pas toutes les
combinaisons à quatre facteurs ou les trajectoires possibles. La physique
est approximative : pas de COP/dégivrage mesuré, débit réel, vent/évaporation
calibrés, changements d'heure ou toutes les formes de pannes HA/Modbus.
Le pas physique est 300 s sauf quatre régressions à 30 s ; les temporisations
AppDaemon sont néanmoins exécutées par l'ordonnanceur virtuel.

**Aucune source de production modifiée pendant le suivi, aucune publication,
aucune commande envoyée à Home Assistant.** Le calcul local n'appelle pas d'IA.
Le suivi périodique est désactivé après ce bilan.
