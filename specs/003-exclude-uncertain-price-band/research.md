# Phase 0 — Recherche : exclusion des tokens à prix trop incertain

## Décision : bande d'exclusion fixe sur le prix de marché, pas sur la probabilité Oracle

- **Décision** : rejeter tout côté dont le **prix de marché exécutable**
  (`up_price`/`down_price`, ou `entry_price` synthétique) se situe dans
  `[0,45, 0,55]` inclus, avant même d'évaluer l'edge minimum. Le filtre porte
  sur le prix de marché, pas sur `prob_up`/`prob_down` de l'Oracle.
- **Rationale** : un prix de marché proche de 0,50 signifie que le marché
  lui-même considère l'issue comme proche d'un pile ou face. Que l'edge
  Oracle soit positif ou non dans cette situation, la fiabilité du signal
  (Oracle et marché) y est structurellement plus faible qu'aux extrêmes ; ce
  filtre matérialise une préférence explicite pour l'abstention plutôt que le
  pari dans les situations les plus incertaines, indépendamment du calcul
  d'edge.
- **Alternative envisagée et rejetée** : appliquer la bande d'exclusion sur
  `prob_up`/`prob_down` (probabilité Oracle) plutôt que sur le prix de
  marché. Rejetée car la demande porte explicitement sur « les tokens »
  (leur prix de marché, ce qu'on achète), et parce que la probabilité Oracle
  est déjà contrainte par ailleurs (`probability_floor`/`probability_ceiling`,
  Décision 6) ; appliquer la bande sur la probabilité aurait dupliqué un
  contrôle existant sans répondre à la demande formulée.
- **Position dans le pipeline de décision** : le filtre s'applique **avant**
  le test d'edge minimum (`min_edge`, Décision du 2026-09-21 sur T030ax) et
  avant tout calcul de sizing. Un côté dans la bande interdite est rejeté même
  si son edge dépasse largement `min_edge`.
- **Motif de rejet distinct** : `economic_status = "uncertain_price_band"`,
  séparé de `edge_below_threshold`, conformément au principe IV de la
  constitution (traçabilité des rejets). Une fenêtre déjà rejetée pour une
  autre raison (`missing_market`, `api_error`, `missing_price`,
  `stale_price`) n'est pas affectée par ce nouveau filtre.
- **Bornes par défaut et configurabilité** : `0,45` / `0,55`, conformes à la
  demande utilisateur, exposées en CLI
  (`--uncertain-price-band-low` / `--uncertain-price-band-high`) pour
  permettre des tests de sensibilité sans modifier le code.
- **Impact attendu sur le backtest** : ce filtre ne peut que réduire le
  nombre de fenêtres pariées (il ne peut jamais qualifier une fenêtre qui ne
  l'était pas déjà) ; il est donc conservateur par construction et ne peut
  pas améliorer artificiellement le P/L en ajoutant des paris.

**Références** : `specs/001-binance-price-stream/research.md`, Décision 11
(garde-fou directionnel avant tout calcul d'edge) et Décision 6 (bornage des
probabilités Oracle), pour la cohérence des mécanismes de garde-fou déjà en
place.
