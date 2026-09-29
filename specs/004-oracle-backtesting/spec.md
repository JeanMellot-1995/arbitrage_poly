# Spécification de fonctionnalité : Backtest historique de l'Oracle BTC

**Feature Branch**: `004-oracle-backtesting`
**Created**: 2026-09-25
**Status**: Proposition
**Input**: User description: "Concentrons-nous sur l'implémentation de la partie backtesting. Utiliser les marchés Polymarket résolus et les trades Binance pour mesurer la qualité Oracle et simuler un P&L à mise fixe de 1 $ au prix de sa fair value."

## Scénarios utilisateur et tests

### User Story 1 - Mesurer la qualité historique des prévisions Oracle (Priorité : P1)

En tant que mainteneur de l'Oracle, je veux recalculer sur les trades BTC
historiques la probabilité Oracle UP à un instant configurable avant la fin
de chaque marché, puis la comparer à l'issue officielle résolue de ce marché,
afin de mesurer la qualité des prévisions sans confondre le résultat du modèle
avec la source de résolution de Polymarket.

**Pourquoi cette priorité** : les données disponibles fournissent les prix
Binance nécessaires au calcul de probabilité et les issues officielles
Polymarket nécessaires au scoring. Elles suffisent à tester le modèle sans
introduire une hypothèse de prix d'entrée ou de rentabilité.

**Test indépendant** : lancer le backtest sur un petit jeu déterministe de
marchés résolus et de trades couvrant leurs fenêtres, puis vérifier que chaque
probabilité est calculée sans données postérieures à l'instant de prédiction,
que le score utilise l'issue Polymarket et que les métriques correspondent aux
probabilités et labels de référence.

**Scénarios d'acceptation** :

1. **Étant donné** un marché BTC résolu avec `endDate` et des trades Binance
  couvrant sa fenêtre, **quand** le backtest calcule la prévision à
  `endDate - 60 s`, **alors** il alimente un état Oracle propre à ce marché
  uniquement avec les ticks de `[endDate - 5 min, endDate)`, puis retient la
  dernière probabilité calculée à ou avant l'instant de prédiction et la
  score contre l'issue officielle Polymarket.
2. **Étant donné** une plage UTC de début et de fin configurée, **quand** le
   backtest sélectionne ses marchés, **alors** il ne traite que les fenêtres
   dont l'heure de début appartient à l'intervalle semi-ouvert `[début, fin)`.
3. **Étant donné** qu'aucune plage n'est fournie, **quand** le backtest
   sélectionne ses marchés, **alors** il traite toutes les fenêtres résolues
   entièrement comprises dans la période commune couverte par les données
   marchés et trades, sous réserve que les données permettent de produire une
   prévision valide.
4. **Étant donné** une fenêtre pour laquelle le résultat directionnel dérivé
   des prix Binance diffère de l'issue officielle Polymarket, **quand** le
   backtest termine son scoring, **alors** le score principal reste basé sur
   l'issue Polymarket et l'écart est signalé séparément dans les sorties.
5. **Étant donné** un marché non résolu, une issue absente ou ambiguë, ou des
   trades insuffisants pour calculer une prévision à l'instant demandé,
   **quand** le backtest traite cette fenêtre, **alors** il ne la score pas,
   indique un motif d'exclusion explicite et continue avec les autres marchés.
6. **Étant donné** un jeu de données contenant plusieurs marchés, **quand** le
   backtest s'exécute, **alors** une barre `tqdm` rapporte la progression du
   traitement des marchés sans modifier les résultats ni les fichiers
   produits.
7. **Étant donné** une fenêtre avec une prévision Oracle valide, **quand** le
  backtest calcule son P&L synthétique, **alors** il mise 1 $ sur UP si
  `prob_up >= 0,5` et sur DOWN sinon, au prix de la probabilité Oracle du côté
  retenu, et règle le payout selon l'issue Polymarket.

### Edge Cases

- Le CSV Binance aggTrades est sans en-tête; son timestamp est en
  microsecondes et doit être interprété comme UTC puis converti correctement
  pour les interfaces internes qui utilisent des nanosecondes.
- Pour un marché, la fenêtre est exactement l'intervalle semi-ouvert
  `[endDate - 5 minutes, endDate)`: un tick à l'ouverture est inclus et un
  tick horodaté à `endDate` est exclu.
- L'Oracle, y compris son estimateur de volatilité, est réinitialisé pour
  chaque marché. Aucun tick antérieur à l'ouverture ou appartenant à un autre
  marché ne peut influencer la référence, la volatilité ou la probabilité.
- L'instant de prédiction est `endDate - prediction_offset`. La probabilité
  retenue est celle du tick le plus récent horodaté à ou avant cet instant;
  aucun tick postérieur ne peut contribuer à cette probabilité.
- Les marchés sont traités dans l'ordre chronologique de leur `endDate`.
  Comme les trades Binance sont triés chronologiquement, leur fichier doit
  être parcouru en flux une seule fois, sans chargement ni tri complet en
  mémoire. Seul l'état du marché courant et les résultats agrégés sont
  conservés pendant le parcours.
- Le CSV peut contenir des trades non valides ou non monotones; ils ne doivent
  pas provoquer un score silencieusement incorrect. Les données rejetées et
  les fenêtres devenues inexploitables doivent être comptabilisées.
- Une fenêtre est inéligible si aucun tick de référence n'est observé dans la
  fenêtre ou si l'Oracle ne dispose d'aucun état valide à l'instant de
  prédiction. Le système ne doit pas utiliser un tick futur pour combler un
  état manquant.
- Une probabilité exactement égale à 0 ou 1 doit être bornée pour le calcul du
  log loss afin d'éviter une valeur infinie; les valeurs de probabilité
  produites par l'Oracle existant restent autrement inchangées.
- Les issues Polymarket doivent être normalisées en cible binaire UP/DOWN à
  partir des champs de résultat du jeu de marchés. Une valeur absente,
  contradictoire ou non reconnue est exclue avec un motif explicite.
- Une différence entre l'issue Binance premier/dernier tick et l'issue
  Polymarket est un diagnostic, pas une raison d'exclure la fenêtre.
- Le P&L est une simulation théorique, pas une reconstitution d'exécution
  Polymarket : le prix d'entrée est la propre probabilité de l'Oracle, aucune
  donnée de carnet ou de prix de token n'est utilisée, et les frais/slippage
  sont nuls dans cette première version.
- La bande d'exclusion incertaine `[0,45, 0,55]` s'applique au prix
  synthétique du token du côté retenu. Elle exclut uniquement le pari P&L et
  ne retire pas la prévision du scoring Oracle.
- À un prix égal à la probabilité prédite, le P&L attendu est nul si les
  probabilités sont parfaitement calibrées et si l'exécution est sans frais;
  le P&L réalisé sert donc à examiner l'effet de l'erreur de calibration,
  sans démontrer une rentabilité négociable.
- Les bornes de dates sont en UTC. La date de fin est exclusive afin que des
  plages adjacentes ne comptent pas deux fois la même fenêtre.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système DOIT fournir une commande de backtest qui accepte en
  entrée le JSON des marchés Polymarket et le CSV Binance aggTrades sans
  en-tête.
- **FR-002**: Le système DOIT réutiliser le modèle Oracle par défaut existant
  pour produire `prob_up` à partir des prix Binance; cette probabilité
  représente la probabilité théorique du résultat UP, et non le prix d'un
  token ou une cote Polymarket.
- **FR-003**: Pour chaque marché, la fenêtre DOIT être définie par
  `[endDate - 5 minutes, endDate)`. Seuls les ticks Binance de cette fenêtre
  DOIVENT alimenter l'Oracle de ce marché; le premier tick inclus DOIT établir
  le prix de référence et l'état de volatilité DOIT être réinitialisé entre
  deux marchés.
- **FR-004**: Le décalage de prédiction DOIT être configurable et valoir
  60 secondes par défaut. Il DOIT être strictement positif et inférieur à la
  durée de la fenêtre de marché. La prévision retenue DOIT être celle du tick
  le plus récent horodaté à ou avant `endDate - décalage`.
- **FR-005**: L'issue Polymarket résolue DOIT être la vérité terrain du score
  principal. Le résultat UP/DOWN recalculé depuis Binance peut être rapporté
  comme diagnostic séparé, mais NE DOIT PAS remplacer le label officiel.
- **FR-006**: Le backtest DOIT calculer au minimum le Brier score, le log loss,
  l'accuracy directionnelle (UP si `prob_up >= 0.5`, DOWN sinon), le nombre de
  marchés résolus et scorés, ainsi que la couverture de scoring.
- **FR-007**: Le backtest DOIT accepter une date de début et une date de fin
  UTC facultatives, appliquées à l'heure de début des fenêtres dans
  l'intervalle `[début, fin)`. Sans dates explicites, il DOIT traiter toutes
  les fenêtres résolues entièrement couvertes par les deux sources.
- **FR-008**: Le backtest DOIT produire un CSV détaillé par marché/fenêtre et
  un rapport JSON agrégé. Le CSV DOIT inclure au minimum l'identifiant ou le
  slug du marché, les bornes de fenêtre, l'instant de prédiction, les prix
  Binance de référence et de prédiction, `prob_up`, l'issue Polymarket, le
  résultat Binance diagnostique, le statut de scoring et le motif d'exclusion
  s'il y a lieu.
- **FR-009**: Le rapport JSON DOIT inclure les paramètres d'exécution, les
  bornes de données effectivement traitées, les métriques définies en FR-006,
  le nombre de fenêtres exclues par motif et le nombre d'écarts entre résultat
  Binance et issue Polymarket.
- **FR-010**: Le traitement des marchés DOIT afficher une barre de progression
  `tqdm`. L'absence de sortie interactive (par exemple sortie redirigée) NE
  DOIT PAS empêcher le backtest de se terminer correctement.
- **FR-011**: Le backtest DOIT exclure explicitement les marchés non résolus,
  les issues non interprétables et les fenêtres sans données Oracle
  suffisantes; il NE DOIT PAS inventer de label, de prix ou de probabilité.
- **FR-012**: Pour chaque fenêtre scorée, le système DOIT simuler une mise
  fixe de 1 USD sur UP si `prob_up >= 0,5`, sinon sur DOWN. Le prix d'entrée
  DOIT être la fair value Oracle du côté choisi (`prob_up` pour UP,
  `prob_down` pour DOWN). Aucun pari NE DOIT être simulé pour une fenêtre
  non scorée ou si le prix d'entrée synthétique est dans `[0,45, 0,55]` inclus.
- **FR-015**: Le système DOIT calculer les shares comme `mise / prix d'entrée`,
  le payout comme les shares si le côté choisi correspond à l'issue
  Polymarket résolue (0 sinon), et le P&L comme `payout - mise`. Les frais et
  le slippage DOIVENT être nuls et clairement identifiés comme hypothèses de
  cette simulation.
- **FR-016**: Le CSV détaillé DOIT inclure le côté parié, le prix d'entrée
  synthétique, la mise, les shares, le payout, le P&L et le P&L cumulé.
  Le rapport JSON DOIT inclure le nombre de paris gagnants/perdants, le total
  misé, le payout total, le P&L total, le ROI et le drawdown maximum.
- **FR-017**: Les sorties DOIVENT préciser que ce P&L est hypothétique et ne
  représente pas un résultat exécutable, car le prix d'entrée provient de la
  fair value Oracle et non d'un prix historique Polymarket.
- **FR-018**: L'exclusion P&L due à la bande incertaine DOIT être rapportée
  séparément des exclusions de scoring, avec le motif `uncertain_price_band`.
  La fenêtre DOIT rester incluse dans le Brier score, le log loss et
  l'accuracy si sa prévision et son label sont valides.
- **FR-013**: Le système DOIT traiter les marchés par ordre chronologique de
  `endDate` et parcourir le CSV Binance trié chronologiquement une seule fois.
  Il NE DOIT PAS charger ou trier l'ensemble des aggTrades en mémoire; la
  mémoire de travail dédiée aux trades DOIT rester bornée à l'état du marché
  courant.
- **FR-014**: Le système DOIT détecter un timestamp Binance décroissant pendant
  le parcours et terminer avec une erreur explicite ou rejeter la ligne selon
  une politique documentée; il NE DOIT PAS trier silencieusement les trades
  ni produire des résultats présentés comme fiables après une rupture
  d'ordre non signalée.

### Key Entities

- **Fenêtre de marché** : marché binaire BTC résolu, avec un début et une fin
  UTC, un identifiant/slug et une issue officielle UP ou DOWN.
- **Trade Binance** : aggTrade comportant au minimum un prix et un timestamp
  UTC; sa séquence constitue l'entrée historique de l'Oracle.
- **Prévision Oracle** : probabilité `prob_up` calculée à l'instant de
  prédiction à partir des observations Binance disponibles jusque-là.
- **Évaluation de backtest** : prévision associée à une issue Polymarket,
  éventuellement accompagnée du résultat directionnel Binance diagnostique,
  et des métriques calculées sur l'ensemble des fenêtres scorées.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Sur un jeu synthétique déterministe, les probabilités reportées
  sont reproductibles en instanciant un Oracle neuf par marché et en ne lui
  fournissant que les ticks de `[endDate - 5 min, endDate)`.
- **SC-002**: Chaque fenêtre scorée est comparée à son issue Polymarket
  résolue; aucun marché non résolu ou sans prévision valide n'entre dans le
  dénominateur des métriques.
- **SC-003**: Le Brier score, le log loss et l'accuracy rapportés sont
  reproductibles à partir des lignes CSV marquées comme scorées et de leurs
  probabilités et labels.
- **SC-004**: Une exécution sans dates explicites traite toutes les fenêtres
  éligibles de la période commune des données; une exécution avec dates
  n'inclut aucune fenêtre hors de `[début, fin)`.
- **SC-005**: Les rapports distinguent le nombre total de marchés examinés,
  les marchés résolus, les fenêtres scorées, les fenêtres exclues par motif
  et les divergences Binance/Polymarket.
- **SC-006**: Un test de frontière confirme qu'un tick à `endDate - 5 min` est
  inclus, qu'un tick à `endDate` est exclu et qu'aucun tick après
  `endDate - prediction_offset` ne change la probabilité retenue.
- **SC-007**: Sur un fichier volumineux trié, le backtest lit les aggTrades
  une seule fois en ordre chronologique et ne conserve pas la collection
  complète des ticks en mémoire.
- **SC-008**: Sur un cas synthétique avec une fenêtre gagnante et une fenêtre
  perdante à probabilité 0,5, chaque marché reçoit une mise de 1 USD, les
  shares et payouts sont corrects, et le P&L cumulé, le ROI et le drawdown
  sont reproductibles à partir du CSV.
- **SC-009**: Une probabilité de prix d'entrée synthétique égale à 0,45, 0,50
  ou 0,55 exclut le pari P&L, mais conserve la fenêtre dans les métriques de
  scoring et produit un motif d'exclusion P&L distinct.

## Assumptions

- Le JSON de marchés contient les informations permettant d'identifier les
  fenêtres, leur statut de résolution et leur issue officielle UP/DOWN.
- Les fenêtres évaluées sont des marchés BTC binaires de cinq minutes; leur
  fin provient de `endDate` et leur début est défini comme `endDate - 5 min`.
- Le modèle Oracle, les paramètres de volatilité, les bornes de probabilité
  et la convention de référence restent ceux du replay existant; l'état du
  modèle est toutefois isolé et réinitialisé pour chaque marché.
- Le P&L de première version utilise le prix Oracle comme prix synthétique
  d'entrée et des frais nuls; il permet une analyse de résultat/calibration,
  mais ne prétend pas estimer une rentabilité exécutable sans prix historiques
  de tokens Polymarket.
- La période par défaut est l'intersection temporelle des marchés résolus et
  des trades Binance, puis restreinte aux fenêtres complètement couvertes et
  disposant des observations nécessaires à l'Oracle.
