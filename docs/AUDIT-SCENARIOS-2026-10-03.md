# Audit filtration, PAC et variabilité météo — 3 octobre 2026

> Document historique : résultats avant correction sur v0.10.15. Le moteur
> a ensuite été corrigé localement et les reproductions transformées en tests
> réussis. Voir [le simulateur et les corrections](SIMULATEUR.md) pour la suite.

## Résultat

Base examinée : tag publié **v0.10.15**, commit `1a2bd8c`.
**8 869 cas exécutés** ; **627 cas présentent au moins un écart aux exigences examinées**.
Ce sont des combinaisons d'entrée, pas 627 bugs indépendants ni 627 pannes physiques.
Les contrôles expriment aussi des exigences de produit à clarifier ; leur échec n'est pas automatiquement un défaut de sécurité.

Le problème principal de confort est démontré : le planificateur supprime une baignade
du jour dès que son heure cible est atteinte, sans la conserver parmi les objectifs
manqués. La couche de rattrapage ne peut alors pas identifier cet objectif perdu.
Une nouvelle date peut remplacer l'ancienne alors que l'utilisateur attend toujours
sa baignade du samedi.

Le moteur de production n'a pas été modifié pendant cet audit. Aucun déploiement HA
ni aucune nouvelle release n'a été effectué.

## Méthode et limites

Le script `tools/audit_scenarios.py` appelle les vraies méthodes du planificateur MPC,
de la politique de confirmation, de la priorité filtration/PAC, de calibration,
du hors-gel et de normalisation du volet. Les adaptateurs de test reproduisent les
entrées et enregistrent les commandes ; ils ne pilotent pas Home Assistant.

Les suites existantes passent : **196 tests**. Cinq reproductions supplémentaires
sont marquées `xfail(strict=True)` : elles échouent comme prévu et représentent
des contrats à satisfaire lors des corrections. Ce ne sont pas cinq tests réussis.

Il s'agit d'un audit de composants et de transitions de prévisions, pas d'une
simulation physique complète avec horloge AppDaemon, services HA, Modbus, inertie
hydraulique et thermostat réel. Les transitions météo comparent les décisions
avant/après avec une température d'eau identique pour isoler l'effet du recalcul.
Elles ne représentent pas deux journées de chauffe effectivement écoulées.

Les pas MPC de l'audit sont 2 h et 0,2 °C pour la matrice principale. Les tests
ciblés emploient également les paramètres existants. Les résultats de faisabilité
dépendent de ces pas ; une validation de correction devra utiliser les réglages
réels et plusieurs résolutions.

## Cas exécutés

| Famille | Nombre | Variables croisées |
|---|---:|---|
| Planificateur | 4 704 | 14 profils météo × 7 températures eau × 6 heures × 4 états volet × 2 permissions nocturnes |
| Révisions météo ordonnées | 2 352 | Toutes les 14 × 14 transitions × 2 températures eau × 2 états volet × 3 heures |
| Séquences de révisions | 24 | Report puis retour, franchissement du seuil de score, anticipation avancée, perte/rétablissement des prévisions |
| Politique de décision | 768 | Choix utilisateur × jour/nuit × température × objectif manqué × demande × preset × segment nocturne |
| Arbitrage énergétique | 768 | Temps disponible × quota × cumul × plage solaire × pompe × demande PAC × compresseur × import/export |
| Calibration | 120 | Ancienneté mesure × redémarrage × Smart/Turbo × activité PAC × volet |
| Continuité du confort | 18 | Avant/après midi, température et volet, météo inchangée |
| Hors-gel | 108 | Modes × températures/seuils/capteur absent × circulation préalable |
| Volet | 7 | Fermé, ouvert, ouverture, fermeture, inconnu, indisponible, absent |

La liste intégrale des entrées, résultats et contrôles est dans
`outputs/scenario-audit/cases.json`. Chaque cas possède un identifiant stable.
`outputs/scenario-audit/catalogue.md` fournit la liste lisible des 8 869 cas.
`outputs/scenario-audit/summary.json` contient les totaux.

### Profils météo

1. Chaleur et soleil persistants.
2. Froid et couverture nuageuse persistants.
3. Pluie durable.
4. Orages, vent fort et forte probabilité de pluie.
5. Chaud → froid/pluie → chaud.
6. Froid/pluie → chaud → froid/pluie.
7. Baignade intéressante demain.
8. Fenêtre de baignade reportée d'une journée.
9. Fenêtre avancée à aujourd'hui.
10. Météo marginale au-dessus du seuil.
11. Météo marginale au-dessous du seuil.
12. Prévision du jour absente.
13. Trou d'une journée au milieu de l'horizon.
14. Prévisions entièrement absentes.

Les 2 352 transitions provoquent 1 608 changements de prochaine date et 934 retraits
d'une date précédemment retenue alors que l'eau reste sous la cible. Ces observations
ne sont pas toutes des erreurs : une dégradation majeure ou un orage peuvent justifier
une annulation. Elles démontrent que la date est très dépendante du recalcul et qu'il
faut un contrat explicite pour conserver ou retirer un objectif annoncé.

## Défauts et corrections nécessaires

| ID | Priorité | Constat | Reproduction et correction proposée |
|---|---|---|---|
| AUD-01 | P1 confort | Objectif du samedi supprimé à midi, sans état « manqué » | `deadline_continuity-00007` : eau 30,7 °C, cible 31 °C, météo inchangée. À 10 h samedi est retenu ; à midi dimanche devient la première date, `missed=[]`. Conserver la date et distinguer heure souhaitée, fin réelle de la fenêtre d'usage, retard et abandon explicite. |
| AUD-02 | P1 stabilité | Nouvelle calibration après un redémarrage bref, malgré une certification d'une minute | `calibration-00013`. La comparaison `certified_at < last_pompe_on` relance la mesure. Réutiliser la certification récente dans une fenêtre bornée ; attendre le brassage nécessaire avant d'utiliser la sonde brute. Ne pas certifier une eau stagnante ni chauffer sur une valeur douteuse. |
| AUD-03 | P1 politique | Le rattrapage du jour force Turbo malgré « Journée seulement » / économie Smart | `policy-00297`, 16 combinaisons. La branche `same_day_recovery_needed` précède les restrictions d'économie. Appliquer le choix utilisateur au rattrapage et signaler le retard estimé ; conserver l'autorisation exceptionnelle comme une politique distincte. |
| AUD-04 | P1 anticipation | Une journée météo absente disparaît aussi de la simulation thermique | 228 combinaisons `missing_middle`, notamment `planner-04082`. Les dates disponibles sont triées puis simulées consécutivement, sans période thermique pour le trou. Compléter la chronologie avec une estimation conservatrice ou dégrader explicitement la faisabilité ; jamais considérer une journée manquante comme zéro temps/pertes. |
| AUD-05 | P2 cohérence | Le repli PRESERVE ignore `allow_night_heating=False` | 308 combinaisons, notamment `planner-00001`. La restriction est appliquée à l'optimiseur mais pas transmise au planificateur de repli. La couche de confirmation peut bloquer la demande ensuite : ce résultat ne démontre pas une chauffe physique nocturne interdite. Propager la politique jusqu'au repli et tester la chaîne complète. |
| AUD-06 | P2 lisibilité | Le choix actif disparaît des attributs du plan lorsqu'aucune exception n'est nécessaire | 56 combinaisons, notamment `policy-00193`. Le retour anticipé ne renseigne pas `decision_choice`. Publier toujours le choix actif et son échéance, même en WAIT, sans le confondre avec la recommandation MPC. |
| AUD-07 | P2 modèle volet | `closing` est immédiatement considéré `closed` | `cover-00004`. Les pertes prévues baissent dès le début de fermeture. C'est une simplification du modèle, pas une preuve de défaut matériel. Conserver une hypothèse prudente pendant le mouvement ; ne prendre « fermé » comme référence d'apprentissage qu'après confirmation. |

Les échecs par contrôle sont : 6 continuités de cible, 12 réutilisations de mesure,
16 politiques économie, 228 trous météo, 308 replis nocturnes, 56 diagnostics de choix,
1 état de volet. Certains cas échouent à plusieurs contrôles ; total unique : 627.

### Causes structurelles à traiter pour la météo variable

- `build_mpc_plan()` est un calcul sans mémoire d'engagement : les jours intéressants
  sont sélectionnés à nouveau à partir du score météo. Il ne peut pas, seul, garantir
  la conservation d'une baignade annoncée samedi lors d'une révision vendredi soir.
- À l'heure cible passée, le jour est retiré des opportunités avant la création de
  `missed_swim_dates`. Le mécanisme de rattrapage dépend pourtant de cette liste.
- Le plan repose sur un gain thermique estimé et des fenêtres journalières. Il ne
  simule pas explicitement les 17 minutes de calibration, les délais de démarrage,
  chaque dégivrage, l'hystérésis de la PAC ou les coupures de communication.
- Les pertes thermiques dépendent du delta eau/air et du volet. Le vent et la pluie
  influencent le score baignade mais n'entrent pas directement dans la loi de pertes
  MPC. L'ensoleillement ne constitue pas non plus un gain solaire passif explicite.
  Ce sont des limites du modèle, à valider avec les données réelles avant d'ajouter
  des coefficients arbitraires.
- L'énergie optimisée par le MPC est celle de la PAC. Les dépenses marginales de
  filtration et les cycles de commande doivent être intégrés au bilan de validation
  pour comparer réellement efficacité énergétique et confort.

## Contrat de confort recommandé

Une date annoncée doit avoir une identité durable, une température et une heure
cible. Conserver un état : proposé, retenu, préchauffage, prêt, en retard, annulé
pour météo défavorable, suspendu par l'utilisateur ou impossible selon le modèle.

Une prévision favorable retardée ou avancée recalcule la puissance et le démarrage,
pas silencieusement l'engagement. Une petite oscillation du score autour du seuil
doit subir une hystérésis et une durée de confirmation. Une météo réellement
dangereuse ou durablement défavorable peut annuler la baignade, mais la date et la
raison doivent apparaître explicitement dans le journal et la carte.

En cas de retard probable : annoncer l'heure estimée, la marge d'incertitude et le
complément nécessaire. Proposer l'autorisation nocturne suffisamment tôt. Lorsque
la cible n'est plus physiquement atteignable, garder l'objectif d'origine comme
manqué et présenter la prochaine opportunité séparément.

## Contrôles qui passent dans cette matrice

- Toutes les demandes de suspension testées bloquent la chauffe prédictive.
- Toutes les autorisations nocturnes testées avec eau sous cible et choix encore
  valide produisent une demande de chauffe dans la couche de décision.
- Les plans MPC optimisés respectent l'interdiction nocturne ; le défaut concerne
  le repli et son interaction avec les autres couches.
- Les commandes de vitesse produites par l'arbitrage testé restent dans 47–100 %.
- Une activité physique PAC conserve la circulation dans les cas de priorité testés,
  y compris lorsqu'une demande logique a disparu ou que le quota est atteint.
- Les cas de forte consommation testés utilisent le minimum PAC hors quota critique.
- Le Turbo explicite n'arme pas la calibration dans les cas testés.
- L'arrêt forcé garde sa priorité sur le hors-gel dans cette matrice.
- Le retour à une prévision identique donne la même décision à entrées identiques.

Ces résultats sont locaux aux composants et aux configurations testés. Ils ne
valident pas l'ensemble des séquences réelles ni toutes les configurations possibles.

## Scénarios restant à exécuter avec un simulateur complet

| Groupe | Scénarios supplémentaires | Critères |
|---|---|---|
| Physique temporelle | Six semaines été/inter-saison ; pluie et vent réels ; couverture changeante ; gain PAC plus faible que le modèle ; pertes plus fortes | Température à midi/17 h, erreur de prédiction, énergie et retard |
| Révisions météo | Rafraîchissements horaires, décalages ±6/12/24/48 h ; oscillations du score ; prévision stable puis changement brutal avant nuit | Maintien de l'engagement, alerte en avance, absence de report silencieux |
| Chronologie | Minuit, changement d'heure, horaires avec fuseau, lever/coucher exacts, nuit autorisée après minuit | Échéances restaurées et politique effective cohérente |
| Automatismes HA | Redémarrage pendant mesure, Turbo, anti-cycle ; confirmation reçue en double/tardive ; changement manuel simultané | Pas de prolongation involontaire, pas de commandes contradictoires |
| Filtration réelle | Nuages toutes les 30 s, pics maison 1–10 min, surplus variable, quota à la limite, pression/filtre encrassé | Quota, cycles, rampes de vitesse, circulation PAC |
| PAC réelle | Veille thermostat 0,5–2 °C, dégivrage, compresseur ne démarre pas malgré `heat`, unité indisponible, défaut débit | Différencier commande, action et puissance ; notifier un blocage durable |
| Sondes | Valeurs figées, NaN/inf, erreurs d'unité, température locale biaisée, incohérence entrée/sortie, coupure des puissances | Diagnostic explicite, pas de faux zéro ni de faux apprentissage |
| Volet/électrolyse | Blocage mi-course, oscillations d'état, volet inconnu pendant perte nocturne, eau froide, faible circulation | Pas de certitude de fermeture anticipée ; apprentissage valide et électrolyse correcte |
| Disponibilité | Contraintes de communication et timers qui se croisent ; callbacks retardés ; commandes non appliquées | Reprise bornée et état publié correspondant au comportement réel |

Il faut simuler ces scénarios sur le contrôleur complet puis rejouer les exports HA.
Le défaut physique constaté le 3 octobre (`heat` + `idle` pendant plusieurs heures)
nécessite un modèle de thermostat et les diagnostics PAC ; l'audit logiciel ne
permet pas d'attribuer ce cas à une cause interne précise.

## Ordre de correction

1. Objectif de baignade durable, conservation des retards et révisions météo explicites.
2. Chronologie météo complète et prise en compte du temps réellement disponible.
3. Calibration récente réutilisée sous conditions et orchestration mesure → décision → chauffe.
4. Politique utilisateur commune au MPC, repli, rattrapage et expiration.
5. États PAC réels, diagnostics de veille prolongée et affichage du volet/mesures anciennes.
6. Validation temporelle de confort et consommation avec des modèles physiques prudents.

Reproduction : `.venv/bin/python tools/audit_scenarios.py` puis
`.venv/bin/python -m pytest -q`. Les résultats complets sont générés localement ;
ils n'effectuent aucune commande vers HA.
