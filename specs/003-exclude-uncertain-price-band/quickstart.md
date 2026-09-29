# Quickstart : exclusion des tokens à prix trop incertain

Le filtre est activé par défaut (bornes `0,45`/`0,55`) dès que le pricing
Polymarket ou un `--entry-price` synthétique est utilisé ; aucune option n'est
requise pour en bénéficier :

```sh
.venv/bin/python -m arbitrage_poly.apps.replay \
	--input data/BTCUSDT-aggTrades-2026-09-11_15.csv \
	--output data/replay_09-11_15.csv \
	--report data/replay_report_09-11_15.json \
	--format aggtrades \
	--stake-usd 10 \
	--min-edge 0.01 \
	--polymarket-symbol BTCUSDT \
	--polymarket-cache /Users/mac-JMELLO02/arbitrage_poly/polymarket_cache.json
```

Pour ajuster ou désactiver le filtre (par exemple pour comparer avec/sans),
fournir des bornes explicites :

```sh
.venv/bin/python -m arbitrage_poly.apps.replay \
	--input data/BTCUSDT-aggTrades-2026-09-11_15.csv \
	--output data/replay_no_band.csv \
	--report data/replay_report_no_band.json \
	--format aggtrades \
	--stake-usd 10 \
	--min-edge 0.01 \
	--polymarket-symbol BTCUSDT \
	--polymarket-cache /Users/mac-JMELLO02/arbitrage_poly/polymarket_cache.json \
	--uncertain-price-band-low 0.5 \
	--uncertain-price-band-high 0.5
```

(Avec des bornes égales à `0,5`, seul un prix exactement à `0,5` est exclu,
ce qui revient en pratique à désactiver le filtre pour des prix réels.)

La sortie CSV expose le nouveau motif de rejet `uncertain_price_band` dans la
colonne `economic_status` ; le rapport JSON expose les bornes effectivement
appliquées (`uncertain_price_band_low`, `uncertain_price_band_high`).
