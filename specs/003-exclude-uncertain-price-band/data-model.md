# Phase 1 — Modèle de données : exclusion des tokens à prix trop incertain

Cette fonctionnalité n'introduit aucune nouvelle entité persistée ; elle
ajoute un paramètre de configuration et une valeur de statut supplémentaire
aux structures existantes.

## Entités modifiées

### Paramètre de configuration (`_simulate_pnl`, `run_replay`, CLI)

| Champ | Type | Défaut | Description |
|---|---|---|---|
| `uncertain_price_band_low` | `float` | `0.45` | Borne basse (incluse) du prix de marché exclu |
| `uncertain_price_band_high` | `float` | `0.55` | Borne haute (incluse) du prix de marché exclu |

Contrainte de validation : `0.0 <= uncertain_price_band_low <= uncertain_price_band_high <= 1.0`,
sinon `ValueError`.

### Ligne de sortie du backtest (CSV / `economic_status`)

Nouvelle valeur possible pour la colonne existante `economic_status` :

| Valeur | Signification |
|---|---|
| `uncertain_price_band` | Le côté favori de l'Oracle avait un prix de marché dans la bande exclue ; aucun pari n'a été simulé pour cette fenêtre, indépendamment de l'edge disponible |

Cette valeur s'ajoute aux valeurs existantes (`priced`, `missing_market`,
`api_error`, `missing_price`, `stale_price`, `edge_below_threshold`) sans les
remplacer.

### Rapport de backtest (`ReplayReport`)

Deux nouveaux champs traçant la configuration effectivement utilisée pour
l'exécution :

| Champ | Type | Défaut |
|---|---|---|
| `uncertain_price_band_low` | `float` | `0.45` |
| `uncertain_price_band_high` | `float` | `0.55` |
