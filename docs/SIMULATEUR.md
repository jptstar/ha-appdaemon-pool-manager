# Simulation et stabilisation filtration / PAC

Le simulateur est **hors ligne** : il ne contacte pas Home Assistant, ne commande
aucun équipement et n'envoie aucune notification. Il initialise la vraie classe
`FiltrationPiscine`, ses mixins, ses temporisations et ses écouteurs d'état.

## Trois niveaux de validation

1. `pytest` : régressions ciblées et contrats du moteur.
2. `tools/audit_scenarios.py` : 8 869 combinaisons de composants, dont 2 352
   transitions météo ordonnées. Ce n'est pas une simulation physique.
3. `tools/simulate_pool.py` : boucle fermée avec horloge virtuelle, backend
   AppDaemon/HA simulé, température évolutive, filtration, PAC, solaire et réseau.

Installation des dépendances de développement :

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python tools/audit_scenarios.py --output outputs/scenario-audit
python tools/simulate_pool.py --suite smoke --limit 0 --hours 48 --trace
python tools/simulate_pool.py --suite pairwise --limit 0 --hours 6 --output outputs/pairwise
```

`smoke` varie chaque axe autour d'un cas de référence (34 séquences). `pairwise`
est une couverture déterministe de **toutes les paires de valeurs** (66 séquences),
pas de toutes les interactions de trois paramètres ou davantage. Le test de
couverture vérifie réellement chaque paire ; ce n'est pas un échantillon présenté
comme exhaustif.

## Validation stratégique recommandée

### Campagne équilibrée : 2 154 simulations

```sh
python tools/run_strategic_matrix.py --profile balanced --workers 4 --output outputs/balanced-2026-10-04
```

La couverture est prouvée par les tests : toutes les paires et les **84 groupes
de triplets** de la grille initiale, soit 7 962 exigences de triplets, sont
couverts par 394 cas. Des tranches sensibles à quatre paramètres, des horaires
et des transitions complètent cette base. Les doublons exacts sont supprimés.

| Groupe | Simulations |
|---|---:|
| Régressions historiques au pas de 30 s | 4 |
| Trajectoires météo / volet sur trois jours | 24 |
| Couverture de tous les triplets | 394 |
| Météo × volet × autorisation × incident | 480 |
| Chauffage × filtration × solaire × consommation | 224 supplémentaires |
| Échéance : départ vendredi matin/soir ou samedi à 0 h/10 h, cible midi/17 h | 432 |
| Huit durées Turbo, redémarrage et expiration réellement simulée | 128 |
| Changements de décision durant la journée et la nuit | 360 |
| Températures juste autour des seuils 30,8 / 30,95 / 31 °C | 108 |
| **Total** | **2 154** |

Les scénarios durent 48 à 78 heures. Les changements passent par les vrais
écouteurs de sélection. Lors d'un redémarrage, le backend conserve les timers
HA mais ne réapplique plus artificiellement la décision initiale. Les huit
durées Turbo ont un contrôle supplémentaire de retour hors Turbo après expiry.
Une erreur d'application d'événement planifié est aussi signalée comme violation.

`progress.json` fournit compteurs par groupe, identifiants échoués et estimation
provisoire ; `results.jsonl` conserve les traces et mesures. `summary.json` est
écrit à la fin. Le calcul reste **local**, sans appel IA/HA. Le suivi périodique
dans Codex est une automation distincte : lui peut consommer des tokens et
nécessite que l'application et le Mac restent disponibles.

Ce n'est pas une preuve de toutes les combinaisons : les tranches à quatre axes
gardent les autres paramètres à une référence documentée. La physique n'est
pas calibrée au bassin réel et les trajectoires ne modélisent pas encore les
changements d'heure, le dégivrage réel, ni les seuils hydrauliques mesurés.

### Suite rapide antérieure : 204 simulations

```sh
python tools/run_strategic_matrix.py --workers 4 --output outputs/strategic-2026-10-04
```

Cette campagne contient **204 simulations** : 176 cas couvrant chaque paire et
cinq groupes de triplets à risque, quatre régressions sur 48 h avec un pas de
30 secondes, et 24 trajectoires sur 72 h (météo évolutive, volet, nuit, réseau
fort et redémarrage). Les cinq triplets sont :

- météo × eau initiale × décision ;
- chauffage × mode de filtration × solaire ;
- chauffage × mode de filtration × consommation maison ;
- chauffage × décision × incident ;
- météo × volet × incident.

Les tests Python prouvent la couverture des **1 285 exigences combinatoires**.
Les frontières d'expiration (minuit / lever du soleil) sont aussi contrôlées
directement par les tests du moteur, sans attendre le prochain pas physique.
La sélection reste une grille finie : les interactions à quatre facteurs et
les autres triplets ne sont pas intégralement couverts. Un triplet d'échéance
variable n'est pas revendiqué : l'heure et le jour de départ restent fixes.

Chaque résultat conserve une trace de température, plan et énergie. Relancer la
même commande reprend les cas sauvegardés ; une empreinte empêche le mélange
de versions et un verrou interdit les doubles lancements. Aucun appel IA, HA
ou réseau n'est effectué. Les déficits thermiques sont distincts des violations
de contrôle ; ils nécessitent une analyse avant de conclure à une régression.

## Grille complète : 540 000 combinaisons

| Axe | Valeurs |
|---|---|
| Météo | Soleil, pluie, orage, chaud/froid, froid/chaud, alternance, prévision manquante, objectif week-end, révision décalant la cible, gel |
| Eau initiale | 18, 26, 29, 30,7, 32 °C |
| Volet | Fermé, ouvert, ouvert de midi à 17 h, inconnu |
| Production solaire | Nulle, forte, alternance de nuages |
| Consommation maison hors piscine | 500, 4 000, 8 000 W |
| Chauffage | Désactivé, Automatique, Fin de saison Smart, Début de saison Smart, Turbo 12 h |
| Décision utilisateur | Journée seulement, autorisation nocturne, suspension |
| Filtration | Intelligent, Température, Hors Gel, Marche Forcée, Arrêt Forcé |
| Incident | Aucun, sonde indisponible temporairement, PAC ne chauffant pas malgré la commande, redémarrage AppDaemon |

La liste exacte, avec identifiants stables, peut être générée sans effectuer
les simulations :

```sh
python tools/simulate_pool.py --catalogue-only --output outputs/catalogue
```

Exécution de **chaque combinaison de cette grille finie** :

```sh
python tools/simulate_pool.py --suite cartesian --limit 0 --hours 48 --output outputs/full
```

Ce calcul est long. Découpage par plages, sans recouvrement :

```sh
python tools/simulate_pool.py --suite cartesian --offset 0 --limit 1000 --output outputs/shard-000
python tools/simulate_pool.py --suite cartesian --offset 1000 --limit 1000 --output outputs/shard-001
```

Pour exécuter la grille complète avec deux processus indépendants et reprendre
automatiquement les **résultats déjà calculés** lors d'une relance :

```sh
nice -n 10 python tools/run_full_matrix.py --workers 2 --hours 48 --step-s 300 --output outputs/full-matrix-2026-10-04
```

La même commande relancée après interruption ignore les identifiants terminés.
Chaque cas est sauvegardé ; une empreinte du moteur, du simulateur et de la
configuration interdit de mélanger plusieurs versions. Un verrou empêche deux
superviseurs de lancer les mêmes calculs dans le même répertoire. Les identifiants
sont parcourus dans un ordre mélangé déterministe couvrant exactement la matrice.
`progress.json` contient le compteur, le PID et l'état ; `results.jsonl` contient
les résultats. L'estimation du temps restant est provisoire et dépend du mélange
des cas terminés. Les cas manquant la température et les violations sont comptés
séparément. Il n'y a pas de service installé ni de redémarrage automatique du
processus : le Mac doit rester actif, et une interruption nécessite une relance.

Ne pas modifier le moteur pendant le calcul : le superviseur s'arrête lorsqu'il
détecte une modification. Après correction, utiliser un nouveau répertoire pour
une nouvelle campagne. La grille n'est exhaustive que lorsque les **540 000
identifiants distincts** ont été sauvegardés, pas lorsque le processus est lancé.

Les fichiers de chaque lancement sont remplacés : donner un répertoire distinct
à chaque plage. `summary.json` annonce `exhaustive: true` **uniquement** si un
lancement cartésien a réellement exécuté les 540 000 cas. Un catalogue n'est pas
un résultat de simulation.

## Ce qui est mesuré

- Température réelle du modèle indépendant à midi samedi et déficit à la cible.
- Énergie pompe, PAC, import réseau et réinjection, en kWh.
- Demande PAC sans vitesse minimale, arrêt forcé et chauffage désactivé ignorés.
- Exceptions du simulateur et erreurs explicitement journalisées par le contrôleur.
- Avec `--trace`, évolution du plan, vitesse, puissances et objectif de confort.

Un déficit n'est pas automatiquement un bug : eau à 18 °C, PAC insuffisante,
suspension ou orage peuvent rendre l'objectif impossible. **Aucune violation de
contrôle ne signifie pas température garantie.** La simulation n'a pas encore
d'oracle démontrant l'optimalité énergétique globale ni la faisabilité maximale
pour chaque cas.

## Modèle et limites

La physique est indépendante des fonctions MPC : gains thermiques selon la
température extérieure et le preset, pertes liées au delta eau/air et au volet,
faible gain solaire passif, puissance de pompe cubique, thermostat PAC simplifié.
Ce modèle est illustratif, **pas une identification de la piscine réelle**.

Le pas physique par défaut est de cinq minutes. Le scheduler traite les callbacks
à leurs dates exactes ; les capteurs physiques ne sont renouvelés qu'à chaque pas.
Utiliser `--step-s 30` pour un essai plus fin et comparer les résultats. Les gains,
pertes et puissances du modèle doivent être recalés sur les historiques réels.

Ne sont pas reproduits fidèlement : débit hydraulique réel, inertie des sondes,
thermostat du constructeur, dégivrage, COP réel, latences Modbus/réseau, panne HA,
changement d'heure, volume exact du bassin ou évaporation selon vent/humidité.
Le backend refuse les services inconnus au lieu de les ignorer silencieusement.

Les valeurs physiques possibles et les séquences météo sont infinies : cette
grille est extensible, mais ne constitue pas « tous les scénarios du monde réel ».

## Corrections du moteur

- Un objectif du jour dépassant midi reste indiqué comme manqué ; le rattrapage
  diurne en Smart continue au lieu de supprimer la baignade.
- Une petite révision météo conserve l'objectif proche (trois jours maximum)
  jusqu'à 20 h le jour visé ; orage, froid marqué ou forte baisse de score annulent
  explicitement cet objectif. Le statut est publié dans le capteur existant.
- Objectif restauré depuis les attributs HA après redémarrage ; aucune nouvelle
  entité obligatoire. Ces attributs restent soumis au plafond existant.
- Une certification de moins de trente minutes est réutilisée seulement après
  un arrêt **connu** de cinq minutes maximum ; arrêt inconnu ou long : calibration.
- Un trou météo au milieu de l'horizon conserve une journée thermique prudente,
  sans inventer une baignade pour la journée manquante.
- L'interdiction nocturne couvre aussi le plan de repli ; Économie reste Smart.
- Quota atteint : une demande PAC garde la circulation minimale au lieu de tomber
  dans la branche d'arrêt. En cours de fermeture, le volet n'est pas encore fermé.
- La nuit, l'état réellement observé du volet prévaut sur l'hypothèse habituelle.

Attributs ajoutés à `sensor.piscine_chauffage_predictif` (ou son identifiant
configuré), sans changer les noms historiques : `comfort_target_date`,
`comfort_target_hour`, `comfort_target_temperature`, `comfort_status`,
`comfort_reason`. `ready` signifie **température estimée atteinte**, pas mesure
certifiée nouvellement réalisée. Les changements de statut sont journalisés,
pas chaque recalcul.

Ces corrections sont locales. Un passage en production nécessite encore
une validation sur les capteurs et équipements réels ; aucune publication
ou modification de Home Assistant n'est effectuée par ces scripts.
