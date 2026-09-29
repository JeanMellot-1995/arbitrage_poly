# Quickstart : lecteur de prix Binance

## Pré-requis

- macOS local ;
- Python 3.11 ou supérieur ;
- dépendances du projet installées dans l'environnement virtuel ;
- accès sortant à `data-stream.binance.vision:443`.

## Installation de développement

Depuis la racine du dépôt :

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install --index-url https://pypi.org/simple -e ".[dev]"
```

La commande exacte d'installation devra suivre `pyproject.toml` lorsqu'il sera
créé pendant la Phase 1 de scaffolding.

## Vérification hors réseau

Les tests unitaires doivent couvrir le parsing et la détection de séquence à
partir de messages synthétiques :

```sh
pytest -q tests/unit/price_collection/test_binance_ws.py tests/unit/oracle/test_oracle.py
```

Cette vérification ne doit ouvrir aucune connexion externe.

## Vérification en direct

Après implémentation du lecteur, démarrer une session courte de collecte avec
les paramètres de développement configurés, puis vérifier :

1. la réception de ticks `binance.agg_trade` et `binance.book_ticker` ;
2. la présence de timestamps UTC en nanosecondes ;
3. l'âge du dernier tick, la latence et la profondeur de file ;
4. la reconnexion après fermeture contrôlée du WebSocket ;
5. l'absence de toute écriture réseau.

La collecte longue durée et la persistance Parquet sont des étapes de Phase 4,
pas une condition de ce quickstart.

## Export CSV de diagnostic

Pour vérifier rapidement l'accès réel au flux et archiver un échantillon local :

```sh
.venv/bin/python scripts/collect_binance_csv.py \
	--duration 10 \
	--output data/binance_sample.csv
```

Le script reste en lecture seule, écrit un en-tête CSV puis un tick par ligne.
Il affiche également les messages rejetés, anomalies de séquence et
reconnexions observées. Le fichier `data/` est ignoré par Git.

## Replay local de l'Oracle

Pour le backtest historique, télécharger le fichier `aggTrades` journalier
Binance correspondant à la date UTC étudiée :

```sh
curl -L \
	"https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-YYYY-MM-DD.zip" \
	-o data/BTCUSDT-aggTrades-YYYY-MM-DD.zip
```

Le replay historique doit traiter les timestamps d'exécution Binance, produire
288 fenêtres UTC et étiqueter le rapport avec la source `agg_trade`.
Les métriques sont calculées avec une seule prédiction par fenêtre, sélectionnée
à `T-60s` ou à la dernière observation disponible avant cette échéance.

Pour le mode live `bookTicker`, après avoir collecté au moins une fenêtre
complète, lancer le replay hors réseau :

```sh
.venv/bin/python -m arbitrage_poly.apps.replay \
	--input data/binance_sample.csv \
	--output data/oracle_replay.csv
```

Pour obtenir une courbe P/L avec une mise fixe, fournir un prix d'entrée
Polymarket synthétique et une mise par fenêtre :

```sh
.venv/bin/python -m arbitrage_poly.apps.replay \
	--input data/binance_sample.csv \
	--output data/oracle_replay.csv \
	--entry-price 0.40 \
	--stake-usd 10
```

Le rapport JSON et le CSV ajoutent alors le P/L par fenêtre et le cumul. Cette
vue est une simulation de sensibilité : Binance ne fournit pas les prix ou les
fills historiques Polymarket nécessaires à une mesure de rentabilité réelle.

Le replay live ne transmet à l'Oracle que les ticks `binance.book_ticker`. Le
replay historique utilise séparément les ticks `binance.agg_trade`. Les deux
modes signalent les lignes malformées, les sources ignorées et les fenêtres
partielles ; les métriques restent étiquetées par source.

## Replay avec prix Polymarket historiques

Le module `arbitrage_poly.polymarket` fournit la découverte Gamma, la lecture
historique CLOB et l'orchestration à `T-60s` :

```python
from arbitrage_poly.polymarket import (
    ClobHistoricalPriceClient,
    GammaMarketDiscovery,
    price_window_at_offset,
)

pricing = price_window_at_offset(
    window_start_ns=window_start_ns,
    window_end_ns=window_start_ns + 300_000_000_000,
    symbol="BTCUSDT",
    discovery=GammaMarketDiscovery(),
    price_client=ClobHistoricalPriceClient(),
)
if pricing.is_priced:
    ...  # utiliser pricing.up_price / pricing.down_price
```

`GammaMarketDiscovery` et `ClobHistoricalPriceClient` n'effectuent que des
requêtes GET, appliquent un rate limiting et des tentatives bornées via
`RateLimitedRestClient`, et retournent un statut explicite (`priced`,
`missing_market`, `missing_token`, `missing_price`, `stale_price` ou
`api_error`) au lieu de lever une exception. Un statut différent de `priced`
doit exclure la fenêtre du P/L, de l'edge et du sizing, sans modifier le score
Oracle de cette fenêtre.

Cette intégration est câblée dans `apps/replay.py` : fournir
`--polymarket-symbol` active la tarification par fenêtre à `T-60s` à la place
d'un `--entry-price` constant (les deux options sont mutuellement exclusives).
`--polymarket-cache` pointe vers un fichier JSON de réponses Gamma/CLOB
enregistrées, pour un replay déterministe hors réseau ; sans
`--polymarket-allow-network`, une entrée absente du cache produit un statut
`api_error` explicite au lieu d'un appel réseau silencieux :

```sh
.venv/bin/python -m arbitrage_poly.apps.replay \
	--input data/BTCUSDT-aggTrades-2026-09-11.csv \
	--output data/oracle_replay.csv \
	--format aggtrades \
	--stake-usd 10 \
	--polymarket-symbol BTCUSDT \
	--polymarket-cache data/polymarket_cache.json \
	--polymarket-allow-network
```

Relancer la même commande sans `--polymarket-allow-network` rejoue ensuite le
backtest de façon déterministe à partir du cache déjà enregistré. La sortie
CSV ajoute `economic_status`, `market_id`, `up_token_id`/`down_token_id`,
`up_price`/`down_price` et leurs timestamps/âges ; le rapport ajoute
`oracle_scored_windows` et `economic_priced_windows`.

Le mode économique doit recevoir une configuration d'accès aux endpoints
Polymarket et un cache local des réponses Gamma/CLOB pour permettre un replay
déterministe hors réseau. Pour chaque fenêtre, il vise `window_end - 60s`,
sélectionne le dernier prix `UP` et `DOWN` antérieur ou égal à cette échéance,
puis exporte leur âge et leur statut.

Une fenêtre sans prix Polymarket exploitable reste comptée dans les métriques
Oracle, mais elle est exclue du P/L et de l'edge. Les réponses API doivent être
archivées ou remplacées par des fixtures avant toute comparaison de rentabilité.

## Boucle live paper, lecture seule

La boucle live combine le flux Binance, l'Oracle, la découverte Gamma active et
les carnets CLOB publics. Elle ne possède aucune clé privée et n'effectue aucune
écriture Polymarket. Le carnet est rafraîchi périodiquement, puis chaque décision
est journalisée en JSON ; un carnet indisponible produit un rejet explicite.

Après installation du package :

```sh
.venv/bin/arbitrage-poly-live \
	--min-net-edge 0.01 \
	--slippage-buffer 0.01 \
	--fixed-stake-usd 10 \
	--refresh-interval-s 1 \
	--verbose
```

La même commande est disponible sans installation du script :

```sh
.venv/bin/python -m arbitrage_poly.apps.live_cli --help
```

Les paramètres `--quantity`, `--max-stake-usd`, `--request-interval-s` et
`--max-retries` bornent respectivement la profondeur évaluée, le risque par
marché et la résilience des appels REST. Arrêter avec `Ctrl+C`; le WebSocket
Binance est alors fermé proprement. Cette boucle reste une observation paper :
elle ne simule pas les fills et ne transmet jamais d'ordre.
