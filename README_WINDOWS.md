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

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```