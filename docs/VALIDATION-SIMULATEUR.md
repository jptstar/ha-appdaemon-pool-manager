# Bilan des corrections locales — 3/4 octobre 2026

Base : v0.10.15 (`1a2bd8c`). Aucun déploiement HA ni nouvelle publication.
Les noms d'entités existants sont conservés ; aucun helper supplémentaire requis.

## Campagne équilibrée — 4 octobre 2026, en cours

Le compromis retenu avec l'utilisateur est de **2 154 simulations**. La campagne
est lancée dans `outputs/balanced-2026-10-04`, avec quatre travailleurs isolés,
48 à 78 heures virtuelles par scénario. La couverture de tous les triplets de
la grille (84 groupes / 7 962 exigences) et des paires est vérifiée par les tests.
Les horaires de départ/cible, événements de décision, huit durées Turbo et
températures frontières complètent la grille, sans revendiquer l'exhaustivité.

**227 tests Python réussis**, Ruff ciblé et `git diff --check` réussis au lancement.
Un défaut du simulateur a aussi été corrigé : le redémarrage ne doit pas
réappliquer la décision initiale ni réarmer le Turbo. Un nouveau test vérifie
qu'une autorisation nocturne modifiée survit au redémarrage.

Le suivi automatique « Suivi validation Pool Manager » vérifie toutes les
15 minutes : annonce des paliers 25 / 50 / 75 %, fin ou anomalie. Il ne publie
rien et ne modifie pas HA ni les sources pendant le calcul. Les déficits
thermiques ne sont pas confondus avec les violations de contrôle.

## Sélection stratégique rapide antérieure — interrompue

À la demande de l'utilisateur, la campagne exhaustive a été interrompue et ses
**627 résultats enregistrés** sont conservés. La sélection stratégique est
lancée dans `outputs/strategic-2026-10-04` : 204 simulations, couverture prouvée
de 1 285 exigences (toutes les paires et cinq groupes de triplets sensibles),
quatre régressions au pas de 30 s et 24 trajectoires de trois jours.
Cette suite s'est arrêtée sur changement d'empreinte, après **152/204 cas sans
violation**, afin de ne pas mélanger les versions du simulateur. Ses résultats
sont conservés mais ne constituent pas un résultat final. Lire `progress.json`, puis
`summary.json` après achèvement. **223 tests Python ont réussi**, dont la
réinitialisation unique des décisions diurnes à minuit, testée à ±1 seconde.
La nouvelle campagne par paires sur 48 h a terminé : 66 cas sans violation.

## Extension exhaustive antérieure — interrompue

La **grille complète de 540 000 cas a été lancée**, avec quatre processus isolés,
48 heures simulées par cas et un pas physique de 300 secondes. Le compteur vivant,
les résultats et l'empreinte de source sont dans
`outputs/full-matrix-2026-10-04/progress.json`, `results.jsonl` et `manifest.json`.
Ce lancement interrompu ne constitue pas une validation des 540 000 cas : le compteur doit
atteindre 540 000 identifiants distincts. Le calcul peut durer plusieurs jours.

Les premiers essais par paires étendus à 48 h ont révélé trois cas de débit
minimal non maintenu, et un essai fin à 30 secondes a révélé une autre transition
en attente PAC. Deux causes de contrôle ont été corrigées :

- Le besoin de circulation de sécurité (arrêt différé / post-circulation) était
  masqué par un besoin prédictif `False`.
- Le mode Température pouvait lancer un brassage nocturne à 35 % ou arrêter la
  pompe hors plage malgré une demande PAC. Les arrêts différés recontrôlent aussi
  la demande avant de couper la pompe.

Le simulateur ne déclenchait pas réellement `timer.finished` : les Turbo restaient
donc actifs artificiellement. Le backend simule maintenant l'expiration, son
annulation, l'entrée par le vrai écouteur de sélection et la survie des timers HA
à un redémarrage AppDaemon. Les anciens résultats Turbo doivent être relus avec
cette limite ; ils ne constituent pas une validation du cycle complet Turbo.

Après corrections : **220 tests Python réussis**, **8 869 cas de composants sans
écart**, et les quatre reproductions problématiques ne présentent plus le défaut
de circulation au pas de 30 secondes. Une nouvelle campagne par paires sur 48 h
est lancée dans `outputs/pairwise-48h-corrected-2026-10-04`.

Le superviseur complet est détaché du terminal, sauvegarde après chaque cas et
verrouille le répertoire pour éviter les doublons. Il n'installe pas de service
de démarrage automatique. La veille du Mac interrompt le temps de calcul ; une
terminaison du processus exige de relancer la commande de reprise documentée.
Le réglage de priorité `nice` a été refusé par l'environnement ; la campagne
tourne néanmoins, limitée à quatre travailleurs sur 14 processeurs visibles.

Les tableaux suivants sont le bilan **historique de la première campagne**,
avant ces essais longs supplémentaires et la correction du backend timer.

## Résultats effectivement exécutés

| Validation | Nombre | Résultat |
|---|---:|---|
| Tests Python | 213 | Tous réussis, aucune reproduction masquée en xfail |
| Audit des composants | 8 869 | Aucun écart aux invariants contrôlés |
| Séquences de référence et variation d'un axe | 34 × 48 h | Aucune violation de contrôle surveillée |
| Interactions couvrant chaque paire de valeurs | 66 × 6 h | Aucune violation de contrôle surveillée |
| Révision météo et objectif du samedi après conservation des scores réels | 2 × 48 h | Aucune violation de contrôle surveillée |
| Catalogue de toutes les combinaisons déclarées | 540 000 | Liste générée ; **pas** 540 000 simulations exécutées |

Compilation Python, vérifications Ruff ciblées et `git diff --check` réussis.
La suite historique de compatibilité des entités et de propriété des méthodes
est incluse dans les 213 tests.

Les résultats complets locaux se trouvent dans :

- `outputs/scenario-audit-final/cases.json` et `summary.json` ;
- `outputs/validated-smoke/results.jsonl` et `summary.json` ;
- `outputs/pairwise-final/results.jsonl` et `summary.json` ;
- `outputs/validated-commitment/results.jsonl` et `summary.json` ;
- `outputs/catalogue/catalogue.csv` pour les 540 000 entrées numérotées.

Le répertoire `outputs/` n'est pas versionné ; les scripts permettent de tout
régénérer. Les suites ont été relancées après les corrections substantielles ;
les tests ciblés couvrent ensuite les petits ajustements de persistance et
d'attributs. La grille complète cartésienne n'a pas été lancée.

## Ce qui est amélioré

L'objectif du samedi ne disparaît plus après midi et ne bascule pas silencieusement
sur dimanche/lundi lors d'une petite variation météo. La persistance s'appuie sur
les attributs du capteur existant. Une annulation météo importante et un retard
sont distingués d'une température estimée atteinte.

La PAC conserve sa circulation minimale lorsque le quota est atteint. Refuser la
nuit ne force plus le rattrapage diurne en Turbo. La restriction nocturne s'applique
également au repli du MPC. Une courte interruption ne relance plus systématiquement
une calibration longue, mais les arrêts inconnus restent soumis à calibration.

La chronologie thermique conserve les journées météo manquantes ; un volet en
fermeture reste traité prudemment. La nuit, son état observé remplace une hypothèse
de fermeture. Les scores météo affichés ne sont pas artificiellement remontés
pour conserver un objectif de confort. Les nombres non finis sont rejetés par
les convertisseurs des modèles prédictifs.

## Ce qui n'est pas démontré

Le modèle illustratif du cas de référence atteint environ 30,96 °C le samedi
à midi, avec eau initiale à 29 °C, volet fermé, temps chaud et choix diurne.
**Cela ne prédit pas la température réelle de l'installation.**

D'autres cas ont un déficit : départ très froid, volet ouvert, suspension ou
météo défavorable. Leur absence de violation ne prouve ni que le déficit est
inévitable ni que la stratégie est énergétiquement optimale. Il faut encore un
comparateur de faisabilité/énergie indépendant et un recalage des paramètres
physiques sur les historiques réels pour trancher ces points.

Il reste aussi à valider sur HA les états remontés par la PAC, les temps de réponse,
le débit minimal effectivement sûr et la précision des températures certifiées.
Voir [SIMULATEUR.md](SIMULATEUR.md) pour les domaines, commandes et limites.
