# Arbitrage Poly sur Windows

Par défaut, le paquet fonctionne en mode paper/read-only : aucune clé privée
n'est requise et aucun ordre Polymarket n'est envoyé. L'exécution réelle
d'ordres est optionnelle et doit être activée explicitement (voir
« Exécution d'ordres réels »).

## Pré-requis

- Windows 10 ou 11 ;
- Python 3.11 ou supérieur, avec `py` disponible dans PowerShell ;
- accès HTTPS à `data-stream.binance.vision`, `gamma-api.polymarket.com` et
  `clob.polymarket.com`.

## Installation

Depuis PowerShell, dans le dossier du projet :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools
.\.venv\Scripts\python.exe -m pip install -e .
```

## Lancement

```powershell
.\.venv\Scripts\arbitrage-poly-live.exe `
  --min-net-edge 0.01 `
  --slippage-buffer 0.01 `
  --fixed-stake-usd 10 `
  --refresh-interval-s 1
```

Arrêter avec `Ctrl+C`. Les décisions sont écrites dans la sortie standard en
JSON. Un accès Polymarket filtré ou réécrit par le réseau doit être corrigé au
niveau DNS, proxy ou pare-feu de cette machine ; ne désactivez pas TLS dans le
projet.

La boucle live prend au plus une décision par fenêtre de 5 minutes, à
`T-120s` (`--prediction-offset-s 120` par défaut, même convention que
`arbitrage-poly-live-decisions` et le backtest). `--prediction-offset-s 0`
rétablit l'évaluation continue à chaque tick.

## Exécution d'ordres réels (optionnel, argent réel)

Installer le SDK Polymarket :

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[live-trading]"
```

Les identifiants sont lus dans `creds.json` (ignoré par git, ne jamais le
committer) ou dans les variables d'environnement `POLYMARKET_API_KEY`,
`POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`, `POLYMARKET_PRIVATE_KEY` et
`POLYMARKET_ADDRESS` (prioritaires sur le fichier).

Commandes (toutes en simulation sans `--yes`) :

```powershell
# Ordre BUY marketable sur la fenêtre BTC 5 min en cours (mise en USDC)
.\.venv\Scripts\arbitrage-poly-market-order.exe --side UP --quantity 5
.\.venv\Scripts\arbitrage-poly-market-order.exe --side UP --quantity 5 --yes

# Ordre manuel sur un token CLOB précis
.\.venv\Scripts\arbitrage-poly-execute-order.exe `
  --token-id <TOKEN_ID> --price 0.55 --quantity 5 --side BUY

# Boucle live qui passe un ordre BUY réel pour chaque opportunité acceptée à T-120s
.\.venv\Scripts\arbitrage-poly-live.exe --min-net-edge 0.01 --fixed-stake-usd 10 `
  --enable-live-trading --creds-file creds.json
```

Les ordres sont des limites GTC qui croisent le spread (au minimum 5 parts).

### Trader Oracle à T-120s (`make oracle-trade`)

L'Oracle lit Binance en direct et fige sa décision à `T-120s` (`LIVE_OFFSET_S`).
Pour le côté choisi (UP si `prob_up >= 0.5`, sinon DOWN) :

- si la fair value est **inférieure** au meilleur ask Polymarket, un ordre
  LIMIT BUY est posé au prix de la fair value (arrondi au tick inférieur) ;
- sinon, un ordre MARKET BUY est exécuté (best ask + 0,01, limite GTC qui croise
  le spread).

```powershell
make oracle-trade                          # simulation : rien n'est envoyé
make oracle-trade CONFIRM=1 STAKE_USD=10   # ordres RÉELS (argent réel)
```

Chaque décision est écrite dans `data/tmp/oracle-trades.csv` (`TRADE_OUTPUT`)
avec `status` = `dry_run`, `sent`, `error` ou `skipped`. Si le programme
démarre après le cutoff T-120s d'une fenêtre, cette fenêtre est ignorée
(`--max-late-s`, 5 s par défaut) : aucune décision tardive. Les ordres LIMIT
non exécutés sont annulés par Polymarket à la résolution du marché.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Utilisation avec `make`

Le `Makefile` détecte Windows (`OS=Windows_NT`) : il utilise `cmd.exe`, les
exécutables de `.venv\Scripts\*.exe`, `py -3.12` pour `make venv`, et écrit les
sorties dans `data/tmp` au lieu de `/tmp`. GNU Make (par exemple GnuWin32 ou
`winget install GnuWin32.Make`) doit être dans le `PATH`.

```powershell
make venv
make install
make test
make live-decisions        # data/tmp/live-decisions.csv, décision à T-120s
make oracle-results DAYS=3
make help                  # liste des cibles
```

Le dossier de sortie se change avec `OUT_DIR`, par exemple
`make backtest OUT_DIR=data/backtests`.

Les données historiques sont lues dans `src/arbitrage_poly/data` (`DATA_DIR`) :
`polymarket-btc-5m-last-10d.json` (`MARKETS`) et
`BTCUSDT-aggTrades-concat.csv` (`BINANCE_AGGTRADES`). Si ce CSV Binance est
absent, `make backtest` / `make oracle-results` le téléchargent d'abord depuis
`data.binance.vision` pour la période couverte par le fichier des marchés
(`make aggtrades` pour le faire seul ; supprimer le CSV pour le régénérer
après une mise à jour du JSON).

Les cibles `help`, `clean` et
l'affichage d'`oracle-results` passent par `scripts/make_tools.py` (Python
standard, lancé avec `py -3`) plutôt que par `awk`/`find`/`rm`.