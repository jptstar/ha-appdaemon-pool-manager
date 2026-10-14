# Validation de stabilité

## Principe de publication

Un nombre élevé de simulations ou une suite unitaire verte ne suffit pas.
Les commandes, protections, affichages et choix proposés doivent rester
cohérents pendant les transitions, les interruptions et les jours successifs.
Un échec de contrôle bloque la publication, même si les températures simulées
semblent bonnes. Aucun test en échec ne doit être masqué par un `xfail`.

## Incidents transformés en tests permanents

- History-3, 14 octobre : répétition de démarrages, mesure de 17 minutes,
  arrêt après quelques minutes, pause de 10 minutes, nouveau démarrage.
  Le test de séquence couvre douze interruptions courtes et vérifie que la
  réutilisation ne redonne pas une date récente à un ancien échantillon.
  Une mesure reste nécessaire après vieillissement ou arrêt prolongé.
- Demande nocturne après arrêt forcé ou chauffage désactivé : les tests
  exigent sa disparition lors de la publication du diagnostic de décision.
- Diagnostic sous arrêt forcé : il ne doit pas présenter un plan actif
  de chauffe nocturne. L'intention du planificateur ne prouve pas que le
  compresseur fonctionne ; la mesure électrique doit rester distinguée.
- Début de saison vers Automatique non piloté : le scénario historique
  `pac-filtration-energy-1036` est rejoué pendant 48 heures. Une demande PAC
  sans circulation minimale constitue un échec, même si la puissance est
  faible au moment de l'observation.
- Trois simulations automne de 24 heures, sans chauffe, avec soleil absent,
  variable et fort : zéro violation et bilan énergétique concordant avec
  la trace du simulateur. Le simulateur utilise une physique approximative.

Ces tests sont exécutés par la suite normale pytest et bloquent les workflows
existants de validation/publication s'ils échouent. Les simulations démarrent
dans des processus isolés, sans connexion à Home Assistant. Elles ne sont pas
un replay exact du CSV : ses tarifs, configurations et entrées ne sont pas
tous connus. Le pas physique de 300 secondes de ces tests ne remplace pas les
replays fins des transitions de sécurité.

## Cohérence énergétique à valider avant toute promesse d'amélioration

Pour des journées identiques et un volume filtré/objectif sanitaire identique,
comparer le nombre de démarrages, changements de vitesse, recalibrages, kWh
pompe/PAC, volume réellement filtré, quota restant et consommation hors période
solaire. Ne pas obtenir une économie en supprimant silencieusement le quota,
les protections hors gel ou la circulation requise par la PAC.

Le rattrapage sur réseau doit rester possible lorsque le quota ne peut pas être
satisfait sur solaire. Il doit être anticipé et expliqué, pas déclenché après
une journée de cycles contradictoires. La production solaire totale n'est pas
le surplus disponible ; les autres usages de la maison doivent être conservés
dans le bilan. Sans tarifs horaires, aucune économie financière ne peut être
affirmée.

Dans History-3, la filtration du 14 octobre jusqu'à 21 h 56 représente environ
6,18 kWh, dont 2,50 kWh après 18 h (environ 40 %). Cela démontre le décalage
horaire constaté, pas que ces 2,50 kWh étaient intégralement évitables.

## Validation finale requise

1. Corriger les incidents détectés avec des tests reproduisant l'échec avant
   correction et réussissant après, sans supprimer les assertions.
2. Rejouer tous les anciens cas de régression de contrôle et de confort,
   notamment les 28 violations de circulation de la campagne partielle v18.
3. Exécuter une nouvelle campagne complète sur une empreinte de sources figée.
   Vérifier les identifiants uniques, le manifeste, les exceptions et toutes
   les violations, pas seulement le compteur d'avancement.
4. Examiner chaque nouvelle dégradation thermique supérieure à 0,1 °C et
   chaque dégradation énergétique ou de stabilité. Distinguer changement
   saisonnier volontaire, déficit physique et régression de contrôle.
5. Repasser tests unitaires, compilation, audit des scénarios, analyse des six
   YAML et vérification des différences. Ne pas modifier le moteur validé
   entre fin de campagne et publication.
6. Publier uniquement après bilan complet et vérification du tag, de la
   release et des workflows distants. Ne jamais assimiler une campagne
   interrompue à une validation complète.

La campagne historique interrompue et ses résultats restent conservés.
Une validation partielle ne certifie ni la température réelle du bassin,
ni l'optimalité énergétique, ni un dimensionnement Turbo.
