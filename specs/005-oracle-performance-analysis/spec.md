# Spécification de fonctionnalité : Analyse complète de la performance Oracle

**Feature Branch**: `005-oracle-performance-analysis`
**Created**: 2026-09-25
**Status**: Proposition
**Input**: User description: "/speckit.specify Écris les specs de cette analyse complète de backtesting."

## Scénarios utilisateur et tests

### User Story 1 - Comprendre la qualité des probabilités Oracle (Priorité : P1)

En tant que mainteneur de l'Oracle, je veux analyser les prévisions résolues
par intervalle de probabilité et de confiance, afin de savoir si les
probabilités annoncées correspondent aux fréquences observées et dans quelles
zones le modèle se trompe.

**Pourquoi cette priorité** : l'accuracy directionnelle seule ne permet pas
d'évaluer une prévision probabiliste. Les mêmes erreurs de direction peuvent
avoir des coûts très différents selon la confiance annoncée; une ventilation
explicite doit aussi éviter de confondre une faible `prob_up` avec une
prévision UP (une faible `prob_up` correspond à une forte confiance DOWN).

**Test indépendant** : produire un jeu de prévisions déterministe dont les
labels, probabilités et mises sont connus, puis vérifier les effectifs,
fréquences observées, écarts de calibration, Brier, log loss et résultats
P&L de chaque tranche, y compris les bornes et la bande de prix incertaine.

**Scénarios d'acceptation** :

1. **Étant donné** un fichier de résultats contenant `prob_up` et un label
   officiel UP/DOWN, **quand** l'analyse regroupe les lignes par tranche de
   `prob_up`, **alors** elle rapporte pour chaque tranche les effectifs, la
   probabilité moyenne prévue, la fréquence réelle UP, l'écart entre les deux,
   l'accuracy directionnelle, le Brier et le log loss.
2. **Étant donné** une ligne avec `prob_up < 0,5`, **quand** l'analyse présente
   les résultats par côté sélectionné, **alors** elle identifie le côté comme
   DOWN, utilise `prob_down = 1 - prob_up` comme confiance/prix synthétique et
   mesure le taux de victoire contre l'issue DOWN; elle ne doit pas présenter
   la ligne comme un pari UP.
3. **Étant donné** une tranche qui comprend des prévisions exclues par la
   bande `[0,45, 0,55]`, **quand** les métriques sont calculées, **alors** ces
   lignes restent incluses dans les métriques de qualité Oracle, sont
   comptées séparément parmi les paris P&L exclus et ne contribuent ni au
   nombre de paris ni au P&L.
4. **Étant donné** une tranche de faible effectif ou sans observations,
   **quand** le rapport est généré, **alors** il affiche son effectif, marque
   explicitement les métriques non définies ou peu fiables et n'invente pas
   une estimation.

### User Story 2 - Vérifier que le signal apporte une valeur mesurable (Priorité : P1)

En tant que mainteneur, je veux comparer l'Oracle à des baselines qui
n'utilisent pas d'information future, afin de déterminer si le modèle améliore
la qualité des probabilités ou seulement répète la direction du mouvement
courant.

**Test indépendant** : sur des fenêtres synthétiques où une baseline constante
et une baseline de signe sont calculables à la main, vérifier que les métriques
Oracle et baselines utilisent exactement le même échantillon évalué.

**Scénarios d'acceptation** :

1. **Étant donné** des fenêtres évaluées et une fréquence UP estimée sur une
   période d'entraînement antérieure, **quand** les scores sont comparés,
   **alors** le rapport inclut une baseline constante à 0,5, une baseline
   constante à la prévalence UP d'entraînement et le Brier Skill Score Oracle
   contre la baseline de prévalence.
2. **Étant donné** le prix de référence et le prix au cutoff disponibles,
   **quand** la baseline directionnelle est calculée, **alors** elle prédit
   UP si le prix courant dépasse la référence, DOWN s'il lui est inférieur,
   et traite les égalités selon une convention explicite sans avantage
   artificiel.
3. **Étant donné** plusieurs offsets ou variantes comparés, **quand** leurs
   scores sont rapportés, **alors** la comparaison appariée utilise les mêmes
   identifiants de marchés scorés et signale les différences de couverture.

### User Story 3 - Mesurer la robustesse temporelle (Priorité : P1)

En tant que mainteneur, je veux évaluer les performances sur des périodes
chronologiques indépendantes et quantifier leur incertitude en respectant la
dépendance entre fenêtres voisines, afin d'éviter de retenir un réglage qui
ne fonctionne que sur l'échantillon déjà observé.

**Test indépendant** : fournir des journées synthétiques avec ordre
chronologique connu et un prédicteur dont la calibration change entre
périodes; vérifier qu'aucune observation future ne participe à l'ajustement
ou à une baseline d'entraînement, et que les intervalles rééchantillonnent des
blocs temporels plutôt que des fenêtres isolées.

**Scénarios d'acceptation** :

1. **Étant donné** plusieurs journées UTC, **quand** l'évaluation
   walk-forward est exécutée, **alors** chaque période d'évaluation ne dépend
   que de fenêtres d'entraînement et, le cas échéant, de calibration qui la
   précèdent dans le temps.
2. **Étant donné** une calibration ou une prévalence de référence, **quand**
   elle est ajustée, **alors** les données de la période évaluée ne sont
   jamais utilisées pour apprendre ses paramètres, son seuil, son plafond ou
   son choix d'offset.
3. **Étant donné** des fenêtres corrélées au sein d'une journée, **quand** les
   intervalles d'incertitude sont calculés, **alors** le rééchantillonnage
   conserve des blocs temporels entiers et le rapport indique la méthode, la
   taille/le nombre de blocs et la graine utilisée.
4. **Étant donné** trop peu de blocs temporels indépendants pour quantifier
   l'incertitude de façon utile, **quand** le rapport est généré, **alors** il
   signale que la conclusion est exploratoire au lieu de présenter une
   précision artificielle.

### User Story 4 - Séparer performance prédictive et résultat économique (Priorité : P2)

En tant qu'utilisateur du backtest, je veux distinguer le P&L synthétique au
prix Oracle d'une simulation fondée sur des prix Polymarket exécutables, afin
de ne pas interpréter une bonne calibration comme la preuve d'une stratégie
rentable.

**Test indépendant** : analyser un rapport ne contenant que le prix synthétique
Oracle et vérifier que le rapport identifie le P&L comme hypothétique et
n'affiche aucun indicateur comme résultat exécutable; fournir ensuite des prix
historiques exécutables avec frais et vérifier que cette simulation forme une
section économique distincte.

**Scénarios d'acceptation** :

1. **Étant donné** un P&L dont le prix d'entrée est la fair value Oracle,
   **quand** il est rapporté, **alors** il est explicitement nommé synthétique,
   exclut la bande incertaine conformément à la spec 003 et n'est pas qualifié
   de rentabilité exécutable.
2. **Étant donné** que des prix historiques exécutables Polymarket ne sont pas
   fournis, **quand** l'analyse se termine, **alors** elle rapporte que
   l'évaluation économique exécutable est indisponible au lieu de déduire un
   edge à partir du prix synthétique Oracle.
3. **Étant donné** des prix exécutables historiques fournis avec leur horodatage
   et les coûts applicables, **quand** une simulation économique est
   disponible, **alors** elle rapporte séparément les hypothèses d'exécution,
   les paris pris/rejetés, les frais, le slippage, le P&L, le ROI et le
   drawdown.

## Edge Cases

- Une petite `prob_up` désigne une prévision DOWN; les tranches de confiance
  sélectionnée doivent donc être construites avec `max(prob_up, 1 - prob_up)`
  et le label du côté correspondant, pas seulement avec `prob_up`.
- Les tranches de probabilité doivent couvrir sans chevauchement tout
  l'intervalle publié par l'Oracle, y compris les bornes par défaut 0,05 et
  0,95; chaque borne doit avoir une convention d'inclusion déterministe.
- La tranche `[0,45, 0,55]` peut contribuer aux scores de prévision tout en
  étant exclue du P&L synthétique; les dénominateurs doivent être distincts.
- Le log loss doit borner numériquement les probabilités sans modifier les
  probabilités originales exportées.
- Les journées sans marchés scorés, les labels invalides, les NaN, les
  doublons d'identifiant de marché et les plages non contiguës doivent être
  signalés et ne pas contaminer silencieusement les agrégats.
- Une différence entre label Polymarket et direction terminale Binance doit
  rester un diagnostic distinct; elle ne doit pas être comptée comme erreur
  de calibration additionnelle ni servir à remplacer le label officiel.
- Les périodes d'entraînement, de calibration et d'évaluation peuvent être
  trop courtes pour produire une baseline empirique ou une calibration
  exploitable; les résultats concernés doivent alors être absents ou marqués
  exploratoires.
- Les fenêtres consécutives sont temporellement dépendantes; les intervalles
  calculés en supposant les fenêtres indépendantes ne doivent pas être
  présentés comme inférence fiable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: L'analyse DOIT accepter les sorties détaillées du backtest
  Oracle et leur rapport agrégé; elle DOIT vérifier les colonnes, les types,
  l'unicité des marchés et la cohérence des comptes avant de produire des
  métriques.
- **FR-002**: Le rapport DOIT distinguer explicitement la qualité
  directionnelle (accuracy, matrice de confusion) de la qualité probabiliste
  (au minimum Brier, log loss et fiabilité/calibration).
- **FR-003**: Le rapport DOIT fournir une analyse par tranches non chevauchantes
  de `prob_up`; chaque tranche DOIT inclure l'effectif, le nombre et la
  fréquence de labels UP, `prob_up` moyen, fréquence UP observée, écart de
  calibration, accuracy, Brier et log loss.
- **FR-004**: Le rapport DOIT fournir une analyse miroir par côté sélectionné
  et niveau de confiance `max(prob_up, prob_down)`. Pour chaque tranche, il
  DOIT identifier le côté prévu, l'effectif, la confiance moyenne, la
  fréquence de victoire observée, l'écart de calibration et le Brier score
  du côté choisi.
- **FR-005**: Chaque table DOIT afficher les effectifs et gérer explicitement
  les tranches vides ou de faible effectif; les métriques indéfinies DOIVENT
  être nulles/absentes et non présentées comme zéro.
- **FR-006**: Les métriques de prévision DOIVENT inclure toutes les fenêtres
  scorables valides, même celles dont le P&L est exclu par la bande de prix
  incertaine. Les rapports DOIVENT maintenir des dénominateurs séparés pour
  scoring, paris placés et paris exclus.
- **FR-007**: Le rapport DOIT inclure une baseline probabiliste constante à
  0,5 et une baseline à prévalence UP estimée uniquement depuis les données
  d'entraînement précédant chaque période évaluée. Il DOIT calculer le Brier
  Skill Score contre cette dernière lorsque celle-ci est définie.
- **FR-008**: Le rapport DOIT inclure une baseline directionnelle fondée sur
  le signe du prix Binance au cutoff par rapport au prix de référence de la
  fenêtre, avec le nombre d'égalités et une convention explicite pour leur
  traitement.
- **FR-009**: Les comparaisons entre offsets ou configurations DOIVENT être
  appariées par identifiant de marché; le rapport DOIT préciser les marchés
  communs, les marchés manquants par configuration et les métriques sur
  l'intersection commune.
- **FR-010**: Lorsque plusieurs journées sont disponibles, l'analyse DOIT
  fournir les métriques globales et par journée UTC et DOIT pouvoir effectuer
  une validation walk-forward où entraînement, calibration et évaluation
  respectent l'ordre chronologique.
- **FR-011**: Aucun réglage, modèle, seuil, plafond, offset ou calibration ne
  DOIT être sélectionné en utilisant les résultats de sa période d'évaluation.
  Toute méthode de calibration DOIT être ajustée exclusivement sur des
  observations antérieures à la période évaluée.
- **FR-012**: Le rapport DOIT fournir des intervalles d'incertitude sur les
  métriques principales par rééchantillonnage de blocs temporels, en
  conservant ensemble les fenêtres d'un même bloc/jour. La méthode, le niveau
  de confiance, la taille des blocs, le nombre de blocs disponibles, le nombre
  de réplications et la graine DOIVENT être rapportés. Si le nombre de blocs
  est insuffisant, l'analyse DOIT signaler une conclusion exploratoire.
- **FR-013**: L'analyse DOIT fournir au minimum une courbe/table de fiabilité
  dont chaque tranche rapporte l'effectif, la confiance moyenne et la fréquence
  observée correspondante; l'ECE DOIT être accompagné de ses effectifs et de
  la définition des tranches.
- **FR-014**: Le rapport DOIT ventiler la performance par journée UTC, niveau
  de confiance, volatilité et offset lorsque ces variables sont disponibles.
  Les seuils de segmentation et leurs effectifs DOIVENT être traçables.
- **FR-015**: Le rapport DOIT analyser la couverture et les exclusions par
  période et par motif, ainsi que les divergences de label Polymarket/Binance
  dans un tableau diagnostique séparé.
- **FR-016**: Le P&L au prix synthétique Oracle DOIT rester distinct des
  métriques de calibration, intégrer les paris exclus par la bande 003 dans
  des comptes séparés et être signalé comme hypothétique, sans conclusion de
  rentabilité exécutable.
- **FR-017**: Une analyse économique exécutable NE DOIT être calculée que si
  des prix d'entrée historiques Polymarket applicables sont présents avec une
  convention temporelle définie; les frais, slippage et hypothèses d'exécution
  DOIVENT être déclarés séparément.
- **FR-018**: L'analyse DOIT produire un rapport machine-lisible et des tables
  exportables par tranche, période et configuration, avec paramètres,
  période de données, source des labels et limites connues.
- **FR-019**: Le résultat DOIT être déterministe à données, configuration et
  graine de rééchantillonnage identiques.

### Key Entities

- **Période d'évaluation** : intervalle chronologique de marchés réservés au
  calcul de métriques hors échantillon.
- **Tranche de probabilité** : intervalle non chevauchant de `prob_up` avec
  une convention de bornes définie et les agrégats des prévisions qu'il
  contient.
- **Tranche de confiance directionnelle** : intervalle de confiance du côté
  choisi, où la confiance vaut `prob_up` pour UP et `1 - prob_up` pour DOWN.
- **Baseline** : règle probabiliste ou directionnelle calculée sans utiliser
  l'information de la période évaluée.
- **Bloc temporel** : groupe ordonné de fenêtres rééchantillonné ensemble pour
  préserver une partie de la dépendance sérielle lors de l'estimation de
  l'incertitude.
- **Résultat économique** : simulation P&L identifiée comme synthétique ou
  exécutable selon la source réelle du prix d'entrée et les coûts utilisés.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Pour un jeu déterministe de labels et probabilités, les effectifs,
  fréquences, écarts de calibration, accuracy, Brier et log loss de chaque
  tranche correspondent aux valeurs calculées indépendamment à partir des
  lignes sources.
- **SC-002**: Toutes les prévisions scorables apparaissent exactement une fois
  dans une tranche de `prob_up`; les totaux des tranches égalent le nombre de
  lignes scorables.
- **SC-003**: Toute ligne avec `prob_up < 0,5` est évaluée comme prévision DOWN
  dans les tables côté/confiance; son taux observé porte sur les labels DOWN.
- **SC-004**: Les lignes exclues du P&L par `[0,45, 0,55]` restent présentes
  dans les scores de prévision et sont comptées exactement une fois parmi les
  exclusions économiques.
- **SC-005**: Les baselines et l'Oracle sont évalués sur la même intersection
  de marchés; aucune valeur de la période évaluée ne contribue à une
  prévalence d'entraînement ou à une calibration.
- **SC-006**: Les rapports walk-forward ne contiennent aucune fuite temporelle
  entre périodes d'entraînement/calibration et leurs évaluations.
- **SC-007**: Les intervalles d'incertitude sont reproductibles à graine fixe,
  rééchantillonnent des blocs complets et documentent quand leur base de blocs
  est trop faible pour une conclusion robuste.
- **SC-008**: Le rapport sépare sans ambiguïté la performance de prédiction,
  le P&L synthétique au prix Oracle et, si disponible, le P&L aux prix
  historiques exécutables.
- **SC-009**: Les dénominateurs de couverture, scoring, mises placées et
  exclusions sont réconciliables à partir du rapport et des tables exportées.

## Assumptions

- La vérité terrain principale demeure l'issue officielle UP/DOWN du jeu de
  marchés Polymarket; les issues Binance sont uniquement diagnostiques.
- La probabilité source est la fair value Oracle bornée publiée par le
  backtest; toute analyse de probabilité brute doit être identifiée comme une
  variante distincte si cette donnée est disponible.
- Les tranches d'analyse doivent être configurables ou documentées dans le
  rapport; elles incluent séparément la bande `[0,45, 0,55]` pour rendre
  visible l'effet de la règle 003.
- Le P&L actuel à la fair value Oracle et sans frais est un indicateur
  synthétique de la calibration/résolution, pas une estimation de
  rentabilité négociable.
- Un horizon d'environ dix jours donne un nombre limité de blocs temporels;
  les intervalles calculés sur cette période peuvent rester exploratoires.
- Cette fonctionnalité analyse les résultats du backtest et ne change ni le
  modèle Oracle en production, ni la référence de fenêtre, ni l'estimateur de
  volatilité.
