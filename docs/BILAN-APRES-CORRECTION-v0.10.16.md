# Validation après correction — v0.10.16

Campagne `outputs/release-v0.10.16-balanced`, terminée le 4 octobre 2026 à
10 h 44 (Europe/Paris). Durée : 6 128,92 s, soit 1 h 42 min 9 s.
Les 2 154 identifiants sont distincts et correspondent aux cas du manifeste.
La référence de comparaison est la campagne locale **avant les dernières
corrections**, `outputs/balanced-2026-10-04` ; ce n'est pas un comparatif complet
contre la release v0.10.15 installée sur Home Assistant.

## Contrôle et publication

Zéro violation des règles surveillées sur les 2 154 cas : circulation PAC,
arrêts forcés, chauffage désactivé, événements de décision et expiration des
huit durées Turbo. Les quatre régressions historiques de circulation passent.
230 tests Python et les contrôles GitHub Validate/Release ont passé.

La version v0.10.16 a été publiée **avant la fin de cette campagne**, à la
demande explicite de publication immédiate. Le tag pointe sur
`cc61607adf73f0a91b40c26121d3cda2e20d2984` ; les notes publiques signalent
que la validation longue était partielle au moment de publier.

## Comparaison de confort : résultat mitigé

- Cas sous 30,8 °C à l'échéance : **1 664 avant / 1 669 après**.
- Déficit réduit de plus de 0,1 °C : **16 cas**.
- Déficit augmenté de plus de 0,1 °C : **6 cas**.
- Déficit thermique cumulé sur la population : pratiquement inchangé
  (4 569,92 contre 4 569,94 °C). Cet agrégat n'est pas un indicateur de
  fréquence réelle ou de performance énergétique globale.
- Les cas qui motivaient l'audit de confort restent inchangés :
  `weather-cover-permission-fault-438` à 30,19 °C, `trajectory-18` à
  30,48 °C, `deadline-1261` à 27,84 °C à leur échéance respective.

Les nombreux déficits incluent volontairement des suspensions, arrêts,
pannes, météo annulant la baignade et délais physiquement trop courts.
Ils ne sont pas autant de bugs. En revanche, les six dégradations doivent
être traitées comme des régressions de confort candidates, pas masquées par
le résultat « zéro violation de contrôle ».

## Régression reproduite dans les traces

`temperature-boundary-2147` : eau initiale 31,01 °C, soleil, volet ouvert,
solaire variable, maison 500 W, Fin de saison Smart, nuit autorisée, aucune
panne, départ vendredi matin, échéance samedi midi.

| Mesure | Avant | v0.10.16 |
|---|---:|---:|
| Eau à midi | 30,926 °C | 30,431 °C |
| Eau samedi 8 h | 30,853 °C | 29,898 °C |
| Statut samedi 8 h | ready | ready |
| PAC électrique samedi 8 h | 4 W | 4 W |

La nouvelle trace affiche `ready` à 6 h et 8 h malgré une eau physique simulée
à 30,126 puis 29,898 °C. La chauffe reprend à 9 h, l'objectif est annoncé
menacé à 10 h puis en retard à midi. C'est une incohérence entre disponibilité
estimée et résultat physique simulé ; la cause précise doit être isolée avant
de modifier encore le moteur. Le modèle physique n'est pas calibré sur le
bassin réel : ne pas extrapoler ces valeurs exactes à l'installation.

Autres cas dégradés : `temperature-boundary-2111`, `2091`, `2109`, `2125`
et `all-triples-158` (volet inconnu, variation météo et panne de sonde).
Les cinq cas frontières ont le volet ouvert et une autorisation nocturne.

## Suite proposée — non mise en œuvre par ce suivi

1. Ajouter des assertions de confort sur ces six cas, en plus des invariants
   de circulation. Vérifier la cohérence de `ready` avec une estimation datée
   et l'incertitude de certification ; ne pas confondre une mémoire ancienne
   avec une eau actuellement disponible.
2. Isoler l'effet de la projection de refroidissement, du renouvellement de
   certification et des transitions chauffage/arrêt/apprentissage. Comparer
   aussi la variante sans la dernière modification d'estimation.
3. Recalculer le maintien jusqu'à l'heure du bain et la marge de récupération
   avant d'arrêter une chauffe autorisée, particulièrement volet ouvert.
4. Revalider les cas de dégradation et les cas initialement insuffisants avant
   toute nouvelle publication ; aucun Turbo/nuit supplémentaire implicite.

**Cette campagne ne permet donc pas de déclarer le confort totalement
fiabilisé.** Aucun nouveau commit de release, aucune nouvelle publication
automatique et aucune modification HA n'ont été effectués par ce bilan.
