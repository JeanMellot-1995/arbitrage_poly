# Checklist de qualité de spécification : Flux de prix Binance

**Objet** : Vérifier la complétude et la qualité de la spécification du lecteur de prix Binance  
**Créée le** : 2026-09-18  
**Fonctionnalité** : [spec.md](../spec.md)

## Qualité du contenu

- [x] Aucun placeholder de spécification ne subsiste.
- [x] La fonctionnalité est centrée sur la valeur d'observation et la fiabilité des données.
- [x] Les scénarios sont compréhensibles par un opérateur non spécialiste de l'implémentation.
- [x] Toutes les sections obligatoires du modèle sont complétées.

## Complétude des exigences

- [x] Aucun marqueur `[NEEDS CLARIFICATION]` ne subsiste.
- [x] Les exigences sont testables et non ambiguës.
- [x] Les critères de succès sont mesurables.
- [x] Les critères de succès restent indépendants des détails d'implémentation.
- [x] Les scénarios d'acceptation couvrent les flux principaux.
- [x] Les cas limites sont identifiés.
- [x] Le périmètre lecture seule est explicitement borné.
- [x] Les hypothèses et dépendances sont documentées.

## Préparation de la fonctionnalité

- [x] Chaque exigence fonctionnelle possède un comportement vérifiable.
- [x] Les user stories couvrent la réception, la reprise et la consommation des données.
- [x] Les résultats attendus sont reliés aux critères de succès.
- [x] Aucun ordre réel n'est placé ; la logique de pricing reste une simulation
  paper trading en lecture seule (edge, prix limite, sizing, P/L).
- [x] La collecte Binance est séparée du module Oracle et les scripts Python privilégient la simplicité.
- [x] Le calcul d'edge, le prix limite, le sizing `Q`/`A` et la simulation P/L
  sont spécifiés séparément du scoring Oracle (FR-014, FR-033 à FR-039).
- [x] L'intégration des prix historiques Polymarket (`T-60s`, causalité,
  fraîcheur, absence de marché ou de token) est spécifiée avec des motifs
  d'exclusion stables (FR-040 à FR-044, SC-018).

## État d'implémentation au 2026-09-20

- Le lecteur Binance, l'Oracle, le replay `aggTrades`, la découverte Gamma et
  les prix historiques CLOB sont implémentés et couverts par des tests ciblés.
- Le sizing fixe/Kelly est implémenté, mais il consomme une opportunité déjà
  acceptée.
- Le calcul d'edge et la décision `Opportunity` sont implémentés dans
  `pricing/edge.py` et `pricing/opportunity.py`, avec profondeur, frais,
  slippage, limite, garde-fou directionnel et motifs de rejet. Leur intégration
  dans une boucle live complète reste à faire.
- La calibration hors échantillon, les horizons multiples, la validation
  multi-jours, la persistance Parquet et la collecte continue restent à faire.
- Le replay P/L avec prix constant ou prix Polymarket historique est une
  simulation ; il ne mesure pas les fills réels ni la rentabilité exploitable.

## Notes

- La dépendance directe est l'accès aux WebSocket publics Binance depuis le Mac local.
- Le choix du modèle de résolution Polymarket reste volontairement hors périmètre.
- `price_collection` récupère et normalise les ticks `bookTicker` ; `oracle` mémorise la référence de fenêtre et produit `FairValue` sans dépendance WebSocket.
- Le pricing d'edge et d'opportunité, les règles de limite et les tests
  synthétiques sont implémentés : voir les tâches `T030g` à `T030ak` dans
  `tasks.md`. L'intégration live et l'exécution simulée basée sur un carnet
  restent à raccorder aux applications.
- La spécification est prête pour `/speckit.plan`.
