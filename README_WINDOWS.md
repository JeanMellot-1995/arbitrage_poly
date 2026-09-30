# Arbitrage Poly sur Windows

Ce paquet lance uniquement la boucle live en mode paper/read-only. Il ne
contient aucune clé privée et n'envoie aucun ordre Polymarket.

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

Cette boucle paper évalue les opportunités en continu à chaque tick ; elle
n'est pas le script de scoring historique. Le backtest et l'enregistrement
d'évaluations Oracle utilisent `T-120s` par défaut.

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
`make backtest OUT_DIR=data/backtests`. Les cibles `help`, `clean` et
l'affichage d'`oracle-results` passent par `scripts/make_tools.py` (Python
standard, lancé avec `py -3`) plutôt que par `awk`/`find`/`rm`.