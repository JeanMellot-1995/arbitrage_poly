# Tâches : Flux de prix Binance

**Entrée** : documents de conception de `specs/001-binance-price-stream/`  
**Prérequis** : `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Organisation

Les tâches suivent les trois user stories de la spécification. Le MVP est la
réception et la normalisation de messages Binance sans réseau d'écriture.

## Phase 1 : Mise en place

- [x] T001 Créer la structure de paquet `src/arbitrage_poly/`, `src/arbitrage_poly/price_collection/`, `src/arbitrage_poly/oracle/` et les tests séparés conformément à `specs/001-binance-price-stream/plan.md`
- [x] T002 [P] Ajouter `pyproject.toml` avec le paquet src-layout, Python >= 3.11, `websockets` et les dépendances de test dans `pyproject.toml`
- [x] T003 [P] Ajouter les fichiers `__init__.py` du paquet dans `src/arbitrage_poly/__init__.py`, `src/arbitrage_poly/price_collection/__init__.py` et `src/arbitrage_poly/oracle/__init__.py`
- [x] T004 [P] Ajouter la configuration de test et les exclusions locales dans `pyproject.toml` et `.gitignore`

## Phase 2 : Fondations bloquantes

- [x] T005 Définir les modèles immuables `Tick`, `ConnectionState` et `FlowMetrics` dans `src/arbitrage_poly/models.py`
- [x] T006 Définir l'horloge UTC corrigée et l'interface `now_ns()` dans `src/arbitrage_poly/clock.py`
- [x] T007 [P] Définir les erreurs et événements de transport dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T008 [P] Ajouter les fixtures de messages `bookTicker` dans `tests/unit/price_collection/test_binance_ws.py`

## Phase 3 : User Story 1 - Recevoir les prix BTC en temps réel (P1) 🎯 MVP

**Objectif** : Transformer les messages publics `BTCUSDT` `bookTicker` en ticks
normalisés publiés dans une file asynchrone bornée.

**Test indépendant** : injecter un message Binance représentatif et vérifier les
champs du `Tick`, la source, la quantité et les timestamps.

### Tests de la User Story 1

- [x] T009 [P] [US1] Tester le parsing d'un message `bookTicker` et la production d'un `Tick` dans `tests/unit/price_collection/test_binance_ws.py`
- [x] T011 [P] [US1] Tester le rejet des prix, quantités, symboles et événements invalides dans `tests/unit/price_collection/test_binance_ws.py`

### Implémentation de la User Story 1

- [x] T012 [US1] Implémenter le parseur strict `bookTicker` dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T013 [US1] Implémenter la normalisation des timestamps source et réception dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T014 [US1] Implémenter la publication asynchrone dans une `asyncio.Queue` bornée dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T015 [US1] Exposer une API de lecteur injectable avec transport et horloge remplaçables dans `src/arbitrage_poly/price_collection/binance_ws.py`

**Point de contrôle** : la User Story 1 doit être testable sans connexion réseau.

## Phase 4 : User Story 2 - Maintenir le flux après interruption (P1)

**Objectif** : Détecter les timeouts et reconnexions, puis reprendre la
publication après une interruption temporaire.

**Test indépendant** : utiliser un transport simulé qui ferme puis réouvre la
connexion et vérifier l'état et le compteur de reconnexion.

### Tests de la User Story 2

- [x] T016 [P] [US2] Tester la fermeture et la reconnexion avec délai borné dans `tests/unit/price_collection/test_binance_ws.py`
- [ ] T017 [P] [US2] Tester le timeout de heartbeat et l'état déconnecté dans `tests/unit/price_collection/test_binance_ws.py`
- [ ] T018 [P] [US2] Tester l'absence de reconnexion immédiate infinie et le plafond de délai dans `tests/unit/price_collection/test_binance_ws.py`

### Implémentation de la User Story 2

- [x] T019 [US2] Implémenter la machine d'état de connexion dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T020 [US2] Implémenter le heartbeat, le timeout et l'arrêt propre dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T021 [US2] Implémenter la reconnexion exponentielle plafonnée et la remise à zéro après reprise dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T022 [US2] Exposer les erreurs de transport et transitions de connexion via logs structurés dans `src/arbitrage_poly/price_collection/binance_ws.py`

**Point de contrôle** : une interruption simulée ne doit pas arrêter durablement le lecteur.

## Phase 5 : User Story 3 - Transmettre sans effet de bord (P2)

**Objectif** : Garantir une file bornée, la détection des anomalies de séquence
et une interface indépendante du transport réseau.

**Test indépendant** : injecter des séquences synthétiques et ralentir le
consommateur pour vérifier les métriques et les anomalies.

### Tests de la User Story 3

- [ ] T023 [P] [US3] Tester les doublons, reculs et gaps de séquence par flux dans `tests/unit/price_collection/test_binance_ws.py`
- [x] T024 [P] [US3] Tester la saturation de file et la métrique de débordement dans `tests/unit/price_collection/test_binance_ws.py`
- [x] T025 [P] [US3] Tester l'injection d'un transport synthétique sans ouverture réseau dans `tests/unit/price_collection/test_binance_ws.py`
- [ ] T026 [P] [US3] Tester l'absence d'appels d'écriture réseau et d'import de client de trading dans `tests/unit/price_collection/test_binance_ws.py`

### Implémentation de la User Story 3

- [x] T027 [US3] Implémenter le suivi de séquence pour `bookTicker` dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T028 [US3] Implémenter les compteurs `FlowMetrics`, l'âge du dernier tick et la latence dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [x] T029 [US3] Implémenter la politique explicite de saturation de file dans `src/arbitrage_poly/price_collection/binance_ws.py`
- [ ] T030 [US3] Documenter l'API publique et les garanties du lecteur dans `src/arbitrage_poly/price_collection/binance_ws.py`

### Tâches du module Oracle distinct

- [x] T030a [US3] Créer `src/arbitrage_poly/oracle/oracle.py` comme consommateur de `Tick` sans dépendance WebSocket Binance
- [x] T030b [US3] Implémenter la référence de début de fenêtre 5 minutes dans `src/arbitrage_poly/oracle/reference.py`, avec le tick `bookTicker` le plus proche comme placeholder
- [x] T030c [US3] Implémenter dans `src/arbitrage_poly/oracle/volatility.py` une volatilité annualisée EWMA des log-returns de midpoints, avec fenêtre, facteur de lissage et plancher configurables
- [x] T030d [US3] Implémenter dans `src/arbitrage_poly/oracle/probability.py` la baseline lognormale terminale sans drift : `Phi(log(current/reference) / (volatility * sqrt(tau)))`
- [x] T030e [US3] Produire `FairValue` avec référence, prix courant, volatilité et temps restant dans `src/arbitrage_poly/oracle/oracle.py`
- [x] T030f [US3] Tester le module Oracle avec des `Tick` synthétiques : symétrie à `current=reference`, monotonie, complément à 1 et échéance déterministe
- [ ] T030j [US3] Comparer après collecte la baseline à une régression logistique réguliarisée avec validation walk-forward et calibration indépendante
- [x] T030k [US3] Mesurer Brier score, log loss, reliability curve et comparaison à la probabilité implicite Polymarket avant d'activer un challenger — fait le 2026-09-21 pour le challenger retour à la moyenne (`reversion_speed`) : rejeté, voir `specs/001-binance-price-stream/research.md` Décision 6 et `specs/002-confirm-zero-drift-model/spec.md`
- [x] T030aa [US3] Ajouter l'agrégation des prix `agg_trade` sur une grille régulière d'une seconde par défaut, avec dernier prix de l'intervalle comme prix représentatif, avant calcul des log-returns et de l'EWMA
- [x] T030ab [US3] Ajouter le bornage des probabilités après calcul brut et avant scoring, par défaut `[0.05, 0.95]`, avec `prob_up_raw`, `prob_up` et les bornes dans le rapport
- [ ] T030ac [US3] Implémenter une calibration hors échantillon des probabilités avec séparation temporelle stricte entre entraînement et évaluation
- [ ] T030ad [US3] Comparer les sorties brutes et calibrées aux baselines 50 % et fréquence empirique `UP`, avec accuracy directionnelle, Brier, log loss et fiabilité

### Tâches de replay Oracle

- [x] T030l [US4] Implémenter le lecteur CSV typé et la reconstruction de `Tick` dans `src/arbitrage_poly/apps/replay.py`
- [x] T030m [US4] Filtrer les sources et signaler les lignes malformées ou non monotones sans interrompre silencieusement le replay
- [x] T030n [US4] Implémenter le classement des fenêtres complètes, partielles et exclues avec leur motif
- [x] T030o [US4] Exporter les `FairValue` et les évaluations terminales dans un format déterministe
- [x] T030p [US4] Calculer Brier score, log loss et table de fiabilité uniquement sur les fenêtres complètes et hors `TIE`
- [x] T030q [US4] Ajouter un jeu de replay synthétique couvrant fenêtre complète, fenêtre partielle, source ignorée et ligne malformée
- [x] T030r [US4] Ajouter l'adaptateur de fichiers journaliers Binance `aggTrades` dans `src/arbitrage_poly/apps/replay.py`
- [x] T030s [US4] Ajouter un mode Oracle historique identifié par `terminal_lognormal_ewma_agg_trade`
- [x] T030t [US4] Valider une journée UTC complète et les 288 fenêtres attendues avec timestamps d'exécution Binance
- [x] T030u [US4] Sélectionner le premier trade dans la fenêtre comme référence et le dernier trade avant sa fin comme prix terminal
- [x] T030v [US4] Ajouter au rapport la source, la date UTC, la couverture temporelle, les fenêtres absentes et le motif de chaque fenêtre partielle
- [x] T030w [US4] Tester un replay synthétique de 288 fenêtres `agg_trade` avec timestamps non exactement alignés sur les bornes
- [x] T030x [US4] Interdire le mélange silencieux des modes historiques `agg_trade` et live `book_ticker`
- [x] T030y [US4] Calculer Brier, log loss et fiabilité sur une seule prédiction par fenêtre à `T-60s`
- [x] T030z [US4] Exposer `scored_windows` et `prediction_offset_ns` dans le rapport de replay
- [ ] T030ae [US4] Tester les horizons `T-120s`, `T-60s` et `T-30s` sans fuite de données et les rapporter séparément
- [ ] T030af [US4] Exécuter le replay sur plusieurs journées UTC et produire les métriques par journée ainsi qu'une agrégation globale
- [ ] T030ag [US4] Ajouter un contrôle indiquant que les scores Oracle ne mesurent ni rentabilité ni qualité d'exécution Polymarket

### Tâches du module Pricing

- [x] T030g [US3] Créer `src/arbitrage_poly/pricing/edge.py` pour comparer `FairValue` au carnet Polymarket et calculer l'edge net
- [x] T030h [US3] Créer `src/arbitrage_poly/pricing/opportunity.py` pour produire une `Opportunity` acceptée ou rejetée avec son motif
- [x] T030i [US3] Tester l'edge net et les décisions d'opportunité avec une `FairValue` et un carnet synthétiques
- [x] T030bb [US3] Modéliser dans `pricing/edge.py` les frais Polymarket conditionnés au type d'exécution (`0` maker en mode passif, `0.07 %` catégorie Crypto en mode taker), avec un test du cas où un ordre passif croise immédiatement le carnet et devient taker
- [x] T030ah [US3] Calculer le prix limite maximal depuis `fair_probability`, les frais, le buffer de slippage et le `min_net_edge`
- [x] T030ai [US3] Arrondir le prix limite vers le bas au `tick_size` et séparer `max_limit_price` de `limit_price`
- [x] T030aj [US3] Implémenter les modes de cotation passif et agressif sous la contrainte du prix limite
- [x] T030ak [US3] Tester la profondeur, l'exécution partielle, l'arrondi et l'absence d'ask exécutable au-dessous du prix limite
- [x] T030al [US3] Définir le contrat de mise `Q`, du prix par part `p`, du paiement gagnant et du rendement `A`
- [x] T030am [US3] Implémenter un sizing v1 plafonné (`kelly_fraction_cap=0.5`, `max_stake_per_market=20 $`, `min_stake_usd=1 $`), avec baseline à mise fixe et option de fraction de Kelly basée sur la probabilité Oracle
- [x] T030an [US3] Tester le sizing dynamique, les plafonds de risque (valeurs verrouillées ci-dessus), `kill_switch_active` booléen et le cas `Q = 0`
- [x] T030az [US3] Définir `SizingConfig` et `SizingDecision` (`Q`, `kelly_fraction`, plafonds appliqués, motif, mode `fixed`/`dynamic`) dans `models.py` ou `src/arbitrage_poly/pricing/sizing.py`, avec `Q = 0` immédiat pour une `Opportunity` rejetée
- [x] T030ba [US4] Ajouter au replay un mode de sizing dynamique optionnel (`bankroll` en constante de config, `kelly_fraction_cap=0.5`, `max_stake_per_market=20 $`, `min_stake_usd=1 $`) comparable à `--stake-usd`, avec `Q`, `kelly_fraction` et le motif exportés dans le CSV et le rapport
- [x] T030bc [US4] Remplacer `--fee-per-share` (montant constant par part) par `--fee-rate` (taux sur le notionnel `stake_usd`, défaut `0.0007` taker Crypto) dans `src/arbitrage_poly/apps/replay.py`, avec test de la nouvelle formule `fees_usd = stake_usd * fee_rate`
- [x] T030ao [US4] Ajouter au replay la simulation P/L paramétrée par prix d'entrée, mise et frais
- [x] T030ap [US4] Exporter le P/L par fenêtre, le cumul, les payouts, les frais et le drawdown dans le CSV et le rapport
- [x] T030aq [US4] Documenter que la courbe P/L est une simulation faute de carnet Polymarket historique
- [x] T030ar [US4] Ajouter la découverte historique des marchés et des tokens UP/DOWN via l'adaptateur Gamma
- [x] T030as [US4] Ajouter la lecture paginée et limitée par débit des prix historiques CLOB autour de T-60s
- [x] T030at [US4] Sélectionner le dernier prix UP/DOWN causal (`timestamp <= T-60s`) avec sa fraîcheur et sa source
- [x] T030au [US4] Exclure le P/L d'une fenêtre sans prix Polymarket valide tout en conservant son score Oracle
- [x] T030av [US4] Ajouter un cache ou des fixtures de réponses Gamma/CLOB pour les replays hors réseau
- [x] T030aw [US4] Tester les marchés ambigus, tokens absents, réponses vides, timestamps postérieurs et erreurs API
- [x] T030ax [US4] Restreindre `_simulate_pnl` au garde-fou directionnel (`prob > 0.5` et prix exécutable < probabilité) dans `src/arbitrage_poly/apps/replay.py`, en supprimant le choix par « meilleur edge entre les deux côtés »
- [x] T030ay [US4] Tester le rejet d'un pari non qualifié, l'application des frais à un pari accepté et la qualification symétrique `DOWN` seule dans `tests/unit/oracle/test_replay.py` et `tests/unit/oracle/test_replay_polymarket.py`

**Point de contrôle** : les consommateurs reçoivent un contrat stable et toute
perte ou anomalie est mesurable.

## Phase finale : Finition et contrôles transverses

- [ ] T031 [P] Ajouter les tests de couverture des cas limites du quickstart dans `tests/unit/price_collection/test_binance_ws.py` et `tests/unit/oracle/test_oracle.py`
- [x] T032 [P] Exécuter ruff et corriger les erreurs de qualité dans `src/arbitrage_poly/` et `tests/`
- [x] T033 Exécuter la suite `pytest` et vérifier les critères SC-001 à SC-006 dans `specs/001-binance-price-stream/quickstart.md`
- [ ] T034 Vérifier par recherche statique l'absence de `post_order`, `create_order`, de signature et d'écriture réseau dans le module Binance
- [x] T035 Mettre à jour la checklist `specs/001-binance-price-stream/checklists/requirements.md` avec les résultats d'implémentation

## Dépendances et ordre d'exécution

### Dépendances des phases

- Phase 1 : indépendante.
- Phase 2 : dépend de la Phase 1 et bloque les user stories.
- User Story 1 : dépend de la Phase 2 et constitue le MVP.
- User Story 2 : dépend de T012 et T015 de la User Story 1.
- User Story 3 : dépend de T012 et T015 de la User Story 1 ; peut être développée en parallèle de la User Story 2 après le MVP.
- Finition : dépend des user stories retenues.

### Opportunités de parallélisation

- T002, T003, T004 et T008 peuvent être réalisés en parallèle après T001.
- T009, T010 et T011 peuvent être écrites en parallèle avant T012.
- T016, T017 et T018 peuvent être écrites en parallèle avant T019.
- T023, T024, T025 et T026 peuvent être écrites en parallèle avant T027.
- T031 et T032 peuvent être réalisés en parallèle après les implémentations.

## Stratégie d'implémentation

1. Terminer les fondations et faire passer les tests de parsing de la User Story 1.
2. Livrer le MVP avec la réception et la normalisation sans réseau d'écriture.
3. Ajouter reconnexion et heartbeat, puis valider la User Story 2.
4. Ajouter séquences, métriques et backpressure, puis valider la User Story 3.
5. Exécuter la suite complète et le quickstart avant de passer au pricing et
	au calcul d'edge.
