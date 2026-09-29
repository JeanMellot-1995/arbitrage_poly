# Contrat : flux de ticks Binance

## Producteur : `price_collection`

Le lecteur Binance est le producteur. Il accepte uniquement les messages
publics correspondant au flux `BTCUSDT` `bookTicker`. Il ne
requiert aucune clé privée et n'effectue aucune écriture réseau.

## Consommateur : `oracle`

Le module `oracle/oracle.py`, ainsi que les composants de stockage et de replay,
sont des consommateurs indépendants. En live, l'Oracle utilise les ticks
`bookTicker` pour mémoriser la référence de fenêtre, suivre le prix courant et
produire une `FairValue` probabiliste. Le replay historique peut utiliser des
ticks `agg_trade` issus des archives journalières Binance, mais ses sorties sont
étiquetées comme un modèle historique distinct et ne prétendent pas reproduire
le midpoint ou la profondeur du carnet.

Le module Oracle ne doit pas ouvrir de WebSocket ni importer le client Binance.

## Événement de données

Chaque événement publié contient au minimum :

```text
Tick(
  ts_ns: int,
  price: positive number,
  qty: non-negative number,
  source: "binance.book_ticker",
  seq: int | null,
  exchange_ts_ns: int | null,
  received_ts_ns: int
)
```

`ts_ns` est l'horodatage de référence du système. `exchange_ts_ns` permet de
calculer la latence. `seq` est l'identifiant de mise à jour du flux
`bookTicker`.

## Événements de contrôle

Le producteur doit exposer séparément les événements ou métriques suivants :

- connexion établie et connexion perdue ;
- heartbeat manquant ou timeout ;
- reconnexion tentée et réussie ;
- message rejeté ;
- doublon, recul ou rupture de séquence ;
- saturation de file.

Un consommateur peut décider d'exclure une période après un gap, mais le lecteur
ne doit pas fabriquer de données de rattrapage.

## Garanties et limites

- La file est bornée ; aucune garantie de livraison illimitée n'est promise.
- Toute perte due à la saturation est mesurable via `queue_overflow_count`.
- L'ordre de publication est l'ordre de réception après normalisation.
- Le contrat de collecte ne comprend ni `FairValue`, ni comparaison avec le
  carnet Polymarket, ni décision d'opportunité, ni persistance Parquet.
