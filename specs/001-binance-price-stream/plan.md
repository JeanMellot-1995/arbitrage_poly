# Plan d'implémentation : Flux de prix Binance

**Branch**: `feat/inital` | **Date**: 2026-09-20 | **Spec**: [spec.md](spec.md)
**Input**: Spécification issue de `specs/001-binance-price-stream/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Résumé

Construire un lecteur asynchrone en lecture seule pour le flux public Binance
`BTCUSDT` `bookTicker`, afin de fournir des ticks normalisés au reste du système.
Le lecteur détecte les interruptions et les anomalies de séquence, applique une
file bornée et expose des métriques de santé.
La collecte Binance fournit uniquement des ticks `bookTicker`. Le module Oracle
mémorise la référence de début de fenêtre, calcule la probabilité `UP/DOWN` et
produit une `FairValue`. La comparaison avec le carnet Polymarket, l'edge et
la décision d'opportunité restent dans `pricing`.

## Contexte technique

**Langage/Version** : Python >= 3.11  
**Dépendances principales** : `websockets`, `asyncio`, modèles typés de la
bibliothèque standard  
**Stockage** : CSV/JSON pour les exports et le cache de replay ; la persistance
Parquet reste hors du périmètre implémenté  
**Tests** : `pytest`, `pytest-asyncio`  
**Plateforme cible** : macOS local en v1  
**Type de projet** : bibliothèque Python asynchrone consommée par les apps de
collecte et d'observation  
**Objectifs de performance** : reprendre le flux dans un délai maximal de 30 s
après le retour du service ; maintenir une file de capacité bornée ; ne perdre
aucun message valide sans métrique ou erreur explicite  
**Contraintes** : lecture seule, timestamps en nanosecondes UTC, reconnexion
bornée, heartbeat, détection des trous de séquence, aucun secret  
**Échelle/périmètre** : un symbole (`BTCUSDT`), le flux `bookTicker`, une file
  par lecteur, des fenêtres de 5 minutes et une collecte locale v1

## Vérification de constitution

*GATE : doit réussir avant la Phase 0 de recherche et être réévaluée après la
conception de Phase 1.*

- **Paper trading uniquement** : PASS. Le lecteur est strictement en lecture
  seule et n'envoie aucun ordre.
- **Les faits avant les hypothèses** : PASS. Les paramètres réseau et les
  comportements observés sont isolés et mesurables ; aucune règle de résolution
  Polymarket n'est codée ici.
- **Logique métier pure** : PASS. La normalisation et la détection de séquence
  sont séparables du transport ; les tests peuvent injecter des événements
  synthétiques.
- **Aucun changement silencieux** : PASS. Les timestamps, gaps, reconnexions,
  latences et rejets sont exposés.
- **Risque et périmètre conservateurs** : PASS. Un seul symbole, aucun ordre,
  aucun wallet et exécution locale.
- **Contrats explicites** : PASS. Les modèles `Tick`, `ConnectionState` et
  `FlowMetrics` sont définis dans [data-model.md](data-model.md) et le contrat
  de sortie est documenté dans [contracts/binance_tick_stream.md](contracts/binance_tick_stream.md).

**Résultat avant recherche** : PASS, aucune clarification bloquante.

## Structure du projet

### Documentation de cette fonctionnalité

```text
specs/[###-feature]/
  ├── plan.md              # Ce fichier
  ├── research.md          # Décisions de recherche de Phase 0
  ├── data-model.md        # Modèle de données de Phase 1
  ├── quickstart.md        # Vérification locale de Phase 1
  ├── contracts/           # Contrats d'interface de Phase 1
  └── tasks.md             # Produit ultérieurement par /speckit.tasks
```

### Code source à la racine du dépôt

```text
src/
└── arbitrage_poly/
  ├── clock.py               # offset NTP et horloge monotone
  ├── models.py              # contrats de données partagés
  ├── price_collection/
  │  └── binance_ws.py       # ticks Binance bookTicker
  ├── oracle/
  │  ├── reference.py        # prix de référence de la fenêtre
  │  ├── volatility.py       # volatilité du modèle
  │  ├── probability.py      # probabilités UP/DOWN
  │  └── oracle.py           # production de FairValue
  ├── polymarket/
  │  ├── discovery.py        # découverte Gamma API
  │  ├── historical_prices.py # historique CLOB
  │  ├── economic_pricing.py # tarification à T-120s par défaut
  │  ├── models.py            # contrats Polymarket
  │  ├── cache.py             # cache JSON hors réseau
  │  └── rest.py              # GET, rate limiter et retries bornés
  ├── pricing/
  │  └── sizing.py           # sizing fixe/Kelly plafonné à 20 $
  └── apps/
    └── replay.py            # replay journalier depuis CSV et P/L simulé
```

**Décision de structure** : bibliothèque sous `src/arbitrage_poly/`, avec la
collecte Binance dans `price_collection/binance_ws.py` et l'Oracle dans
`oracle/`. L'Oracle mémorise la référence de début de fenêtre, le prix courant,
la volatilité et le temps restant, puis produit `FairValue` avec les
probabilités `UP/DOWN`. Le module `pricing/` compare cette valeur au carnet
Polymarket, calcule l'edge net et produit une décision d'opportunité. Les
modules communiquent par des contrats typés (`Tick`, `FairValue`, `Opportunity`).

Les scripts Python privilégient une implémentation directe et lisible à une
abstraction supplémentaire lorsque les deux répondent au même contrat.

## Stratégie d'implémentation

1. Définir les modèles immuables et les erreurs de transport dans le périmètre
  existant de `models.py`.
2. Implémenter dans `price_collection` l'adaptateur WebSocket Binance `bookTicker`, parsing strict,
  timestamps normalisés et publication dans une file bornée.
3. Ajouter à `price_collection` la machine d'état de connexion, le heartbeat, la reconnexion
  exponentielle plafonnée et la détection de séquence par flux.
4. Ajouter dans `oracle` la référence de début de fenêtre, le prix courant, la
  volatilité, le temps restant et le modèle de probabilité terminale `UP/DOWN`.
  Commencer par la baseline lognormale sans drift avec volatilité EWMA, puis
  produire une `FairValue` sans dépendance au transport.
5. Tester séparément la collecte et l'Oracle, puis vérifier la référence de
  fenêtre, les bornes de probabilité, le cas d'échéance, la reconnexion
  simulée, la saturation de file et l'absence d'opérations d'écriture.
6. Implémenter puis tester `pricing` séparément avec une `FairValue` et un
  carnet Polymarket synthétiques afin de vérifier l'edge net et les décisions
  acceptées ou rejetées. Cette chaîne est maintenant présente dans `edge.py`
  et `opportunity.py`; son intégration à une observation live reste à faire.
7. Après collecte de données annotées, comparer la baseline à une régression
  logistique réguliarisée enrichie par les retours courts et le déséquilibre
  bid/ask. Évaluer en walk-forward avec Brier score, log loss, calibration
  reliability et comparaison à la probabilité implicite du marché.

  Avant toute comparaison économique, stabiliser la volatilité historique en
  agrégeant les prix sur une grille temporelle régulière configurable (par
  défaut une seconde, en conservant le dernier prix de chaque intervalle.
  Borner ensuite les probabilités, par défaut dans `[0.05, 0.95]`, afin que
  quelques observations extrêmes ne dominent pas le log loss. Cette borne est
  appliquée avant scoring, documentée dans le rapport et constitue une mesure
  de prudence ; elle ne remplace pas la calibration.

  La calibration doit être ajustée sur une période historique antérieure,
  puis évaluée sur une période distincte en walk-forward. Comparer au minimum
  le modèle brut, le modèle calibré, une probabilité constante de 50 % et la
  fréquence empirique `UP`. Rapporter séparément l'accuracy directionnelle et
  la qualité probabiliste.

8. Ajouter un workflow de backtest historique : télécharger ou vérifier le
  fichier journalier Binance `aggTrades`, générer les 288 fenêtres de cinq
  minutes, injecter chaque transaction acceptée dans un Oracle historique,
  puis comparer la probabilité terminale au dernier prix observé de chaque
  fenêtre.

9. Étendre le backtest à plusieurs journées UTC. Produire les scores par jour,
   les intervalles de confiance ou une estimation de leur variabilité, et une
   agrégation globale. Tester au minimum    les horizons de prédiction `T-120s` (défaut), `T-60s` et `T-30s` sans
   réutiliser les données de calibration dans le score.

10. Ne comparer la FairValue aux marchés Polymarket qu'après validation
    probabiliste hors échantillon. Cette étape doit intégrer bid/ask, frais,
    slippage, probabilité de fill et seuil minimal d'edge ; les scores Oracle
    seuls ne constituent pas une preuve de rentabilité.

11. Séparer la mise en dollars `Q`, le prix par part `p`, le paiement
  conditionnel et le rendement `A`. Pour une issue binaire, une part gagnante
  paie `$1`, donc `A = (1 - p) / p` avant coûts. Autoriser ensuite un sizing
  dynamique dépendant de la probabilité Oracle et de l'edge, plafonné par le
  bankroll, la mise maximale par marché, la profondeur et les kill switches.
  Conserver une mise fixe comme baseline de comparaison.

12. Enrichir le replay avec les prix historiques Polymarket au même cutoff que
    l'Oracle (`T-120s` par défaut). Le
    workflow doit découvrir le marché correspondant à la fenêtre via Gamma,
    résoudre les tokens `UP` et `DOWN`, puis demander à l'API CLOB l'historique
    de prix de chaque token autour de `window_end - offset`. Le prix retenu doit
    être le dernier prix observable à cette échéance ou avant, avec son
    timestamp et sa source. L'absence de marché, de token ou de prix
    suffisamment proche exclut uniquement la simulation économique de la
    fenêtre ; elle ne doit pas supprimer la métrique Oracle.

### Contrat du backtest journalier

- L'intervalle d'évaluation est `[day_start_utc, day_start_utc + 24h)`.
- Les fenêtres sont alignées sur UTC et numérotées de 0 à 287.
- La référence d'une fenêtre historique est le premier `aggTrade` observé à
  partir de sa borne de début ; l'absence d'un trade exactement sur la borne
  n'est pas une erreur.
- Le prix terminal historique est le dernier `aggTrade` strictement avant la
  borne de fin.
- Une fenêtre sans référence ou sans observation terminale est `partial` et
  exclue des métriques de calibration.
- Le rapport doit indiquer la journée UTC, les 288 fenêtres attendues, les
  fenêtres complètes, partielles et absentes, ainsi que la couverture de
  l'archive.
- Une archive `aggTrades` ou de klines ne peut pas être présentée comme une
  archive `bookTicker` ; le rapport doit conserver cette distinction de source.

## Plan de travail : module pricing

Le module `pricing` transforme une `FairValue` Oracle et un snapshot de carnet
Polymarket en une évaluation d'opportunité. Il reste pur et en lecture seule :
il ne découvre pas les marchés, ne maintient pas la connexion au CLOB et ne
place aucun ordre. La décision produite est une recommandation destinée au
paper trading et au module de risque.

### Contrats d'entrée

Le module doit recevoir deux contrats typés et immuables :

- `FairValue`, contenant au minimum `prob_up`, `prob_down`, `ts_ns`, le modèle
  et le temps restant ;
- `OrderBookSnapshot`, contenant les meilleurs niveaux achetables et vendables,
  leur quantité, le timestamp de marché et une profondeur suffisante pour le
  montant évalué.

Le snapshot doit distinguer les deux issues binaires (`UP` et `DOWN`) et les
  côtés exécutables : pour acheter une issue, le pricing utilise l'ask ; pour
  la vendre ou calculer une sortie, il utilise le bid. Un niveau absent, nul,
  négatif ou sans quantité disponible rend le côté concerné non évaluable.

### Calcul de l'edge

Pour chaque issue, le module convertit la probabilité Oracle en valeur
attendue et la compare au prix exécutable du carnet. Le calcul doit intégrer :

- le prix moyen réellement disponible pour la quantité demandée, et non
  seulement le meilleur niveau ;
- les frais explicites configurés ; pour Polymarket, ces frais valent `0`
  tant que l'ordre reste maker (mode passif qui ne croise pas le carnet) et
  `0.07 %` du montant exécuté dès qu'il devient taker (mode agressif, ou un
  ordre passif qui croiserait immédiatement le carnet à sa soumission) ; ce
  n'est donc pas une configuration indépendante du mode de cotation
  (voir [Décision 13](research.md)) ;
- le slippage estimé entre le meilleur prix et le prix moyen de profondeur ;
- la latence ou l'âge du snapshot ;
- un seuil minimal d'edge configurable.

### Garde-fou directionnel (implémenté en v1 dans le replay)

Avant tout calcul d'edge, chaque issue doit d'abord être qualifiée
directionnellement : le système ne doit jamais acheter une issue dont
l'Oracle n'est pas majoritairement convaincu, même si son prix est très bas.

```text
up_qualifies   = prob_up   > 0.5 and up_price   < prob_up
down_qualifies = prob_down > 0.5 and down_price < prob_down
```

Une issue qui ne remplit pas sa propre condition n'est jamais achetée, même si
l'autre issue est elle aussi disqualifiée. Il ne s'agit pas de choisir le
« meilleur edge entre les deux côtés » : cette comparaison peut faire acheter
un côté minoritaire (`prob <= 0.5`) au seul motif qu'il serait moins cher que
l'autre, ce qui revient à parier contre la conviction directionnelle de
l'Oracle. Comme `prob_up + prob_down = 1`, au plus une issue peut qualifier à
la fois.

Ce garde-fou est implémenté dans `apps/replay.py::_simulate_pnl` pour la
simulation P/L historique (voir [Décision 11](research.md)) et DOIT être
conservé comme condition préalable lors de l'implémentation de
`pricing/opportunity.py` : le calcul de l'edge net et du prix limite ne
s'applique qu'à une issue déjà qualifiée par cette règle.

### Calcul du prix limite

Le pricing doit calculer le prix limite acceptable avant de décider comment
le publier dans le carnet. Ce prix est une limite économique, pas
nécessairement le prix affiché ni le prix d'exécution.

Pour une position acheteuse sur une issue, la v1 définit :

```text
max_limit_price = fair_probability - fees - slippage_buffer - min_net_edge
```

où :

- `fair_probability` vaut `prob_up` ou `prob_down` selon l'issue ;
- `fees` est le coût estimé de la transaction pour cette issue : `0` en mode
  passif tant que l'ordre reste maker, `0.07 %` du montant exécuté en mode
  agressif ou dès qu'un ordre initialement passif croise immédiatement le
  carnet (voir [Décision 13](research.md)) ;
- `slippage_buffer` est une réserve de prudence configurable ;
- `min_net_edge` est le gain minimal exigé après coûts.

Le prix limite doit ensuite être arrondi vers le bas au tick size Polymarket :

```text
limit_price = floor_to_tick(max_limit_price)
```

Le système doit rejeter la proposition si le prix limite est inférieur ou
égal à zéro, supérieur à un, ou incompatible avec la configuration de risque.
Il doit conserver séparément `max_limit_price` avant arrondi et `limit_price`
après arrondi.

Le choix du prix effectivement affiché dépend du mode de cotation :

- **passif** : proposer au meilleur bid disponible, éventuellement augmenté
  d'un tick pour améliorer la priorité, mais jamais au-dessus de
  `limit_price` ;
- **agressif** : proposer `limit_price` et accepter uniquement les asks dont
  le prix est inférieur ou égal à cette limite ;
- **sans contrepartie disponible** : ne pas produire d'opportunité exécutable.

La profondeur du carnet ne modifie donc pas la limite économique calculée à
partir de la FairValue. Elle détermine le prix moyen exécutable, la quantité
disponible, le risque d'exécution partielle et le choix entre une cotation
passive et agressive.

La convention v1 est :

```text
gross_edge = fair_probability - executable_price
net_edge = gross_edge - fees - slippage_penalty
```

Le contrat doit conserver séparément `gross_edge`, `fees`, `slippage_penalty`,
`net_edge`, `max_limit_price`, `limit_price`, le prix de cotation proposé, le
prix exécutable, `Q`, le nombre de parts, le paiement gagnant théorique, `A`,
la quantité évaluée et la cause éventuelle du rejet. Pour le replay économique,
il doit aussi conserver `market_id`, les token IDs `UP` et `DOWN`, les prix
Polymarket à `T-120s` par défaut, leurs timestamps, la latence par rapport à l'échéance et
le motif d'absence éventuel. Aucun
arrondi intermédiaire ne doit modifier la comparaison au seuil.

### Décision d'opportunité

`opportunity.py` applique les garde-fous dans un ordre déterministe :

1. vérifier que l'issue est qualifiée directionnellement (`prob_up > 0.5` et
   `up_price < prob_up`, ou symétriquement pour `DOWN`) ; une issue non
   qualifiée est écartée sans calcul d'edge, sans être remplacée par l'autre
   issue au seul motif d'un prix plus bas ;
2. vérifier que la `FairValue` et le snapshot sont valides ;
3. vérifier que leurs timestamps sont dans la fenêtre de fraîcheur configurée ;
4. vérifier que la profondeur couvre la quantité demandée ;
5. calculer le prix moyen, les frais, le slippage et l'edge net ;
6. accepter uniquement si l'edge net est supérieur ou égal au seuil ;
7. produire une `Opportunity` avec `accepted`, le côté (`UP` ou `DOWN`), le
   motif, les paramètres de calcul et les timestamps utilisés.

Les motifs de rejet doivent être stables et exploitables, au minimum :
`stale_fair_value`, `stale_order_book`, `missing_side`, `insufficient_depth`,
`invalid_price`, `invalid_probability`, `edge_below_threshold` et
`configuration_error`.

### Ordre d'implémentation

1. Définir les modèles `OrderBookLevel`, `OrderBookSnapshot`, `PricingConfig`,
  `EdgeEstimate` et `Opportunity` dans `models.py` ou dans un module pricing
  dédié, en conservant des validations strictes. `PricingConfig` doit inclure
  `min_net_edge`, `slippage_buffer`, `fees`, `tick_size`, le mode de cotation
  et la fraîcheur maximale.
2. Implémenter dans `pricing/edge.py` le calcul du prix moyen par profondeur,
  des frais, du slippage, du prix limite et de l'edge net pour une issue.
3. Implémenter dans `pricing/opportunity.py` la validation de fraîcheur, de
  profondeur, des probabilités, du prix limite et du seuil, puis la décision
  finale.
4. Ajouter un adaptateur historique Polymarket séparant la découverte Gamma,
  la résolution des tokens binaires et la lecture CLOB des prix à une
  échéance. Le replay doit pouvoir fonctionner en mode strict (échec si les
  prix Polymarket manquent) ou en mode Oracle-only (simulation économique
  désactivée, scores Oracle conservés).
5. Ajouter des tests unitaires synthétiques couvrant une opportunité positive,
   un edge sous le seuil, un carnet trop peu profond, un snapshot périmé, des
  frais et un slippage qui annulent l'edge, l'arrondi au tick size et les
  modes passif/agressif.
6. Ajouter un test d'intégration hors réseau avec réponses Gamma/CLOB
  enregistrées : sélection du dernier prix `<= T-120s` par défaut, séparation UP/DOWN,
  marché absent, token absent, prix absent et réponse périmée.
7. Vérifier que le module pricing n'importe aucun client d'exécution, ne fait
   aucune écriture réseau et reste déterministe pour les mêmes contrats et
   paramètres.

### Critères de validation pricing

- Le prix utilisé pour une quantité donnée est le prix moyen pondéré des
  niveaux consommés, avec rejet si la profondeur est insuffisante.
- Le prix limite maximal est calculé depuis la probabilité FairValue et les
  coûts avant toute décision de cotation.
- Le prix limite est arrondi vers le bas au tick size et ne dépasse jamais le
  prix limite maximal avant arrondi.
- Le mode passif ne dépasse jamais le prix limite et le mode agressif ne
  consomme aucune ask au-dessus de cette limite.
- Un edge brut positif peut devenir négatif après frais et slippage ; la
  décision doit alors être rejetée.
- Les frais appliqués correspondent au barème réel Polymarket : `0` en mode
  passif tant que l'ordre reste maker, `0.07 %` (catégorie Crypto) dès qu'il
  s'exécute comme taker, y compris un ordre passif qui croiserait
  immédiatement le carnet à sa soumission. Un taux ou montant de frais
  constant indépendant du mode d'exécution ne doit pas être utilisé.
- Un snapshot au-delà de la fraîcheur maximale est rejeté sans calculer une
  opportunité acceptée.
- Un prix Polymarket historique postérieur au cutoff configuré ne doit jamais
  être utilisé pour une fenêtre ; le prix retenu est le dernier prix antérieur
  ou égal au cutoff (`T-120s` par défaut).
- L'absence d'un marché, d'un token ou d'un prix Polymarket est tracée et
  exclut la simulation P/L de la fenêtre sans exclure le scoring Oracle.
- Une probabilité `FairValue` invalide ou qui ne respecte pas le complément
  `prob_up + prob_down = 1` est rejetée explicitement.
- Un edge exactement égal au seuil suit une convention documentée et testée ;
  la convention v1 est l'acceptation avec `>=`.
- Une issue n'est jamais achetée si sa probabilité Oracle n'est pas
  strictement majoritaire (`> 0.5`) et strictement supérieure au prix
  exécutable de cette même issue ; le camp opposé n'est jamais substitué pour
  ce seul motif d'un prix plus bas.
- La sortie pricing ne constitue pas un ordre et ne doit contenir ni signature,
  ni identifiant d'exécution, ni appel à Polymarket.

## Plan de travail : module sizing

Ce plan dimensionne `Q`, la mise en dollars, une fois qu'une `Opportunity` a
déjà été acceptée par `pricing/opportunity.py` (issue qualifiée
directionnellement, edge net évalué, seuil franchi). Le sizing ne doit jamais
rouvrir ni contourner cette décision d'achat : une `Opportunity` rejetée
DOIT produire `Q = 0` sans même évaluer la formule de Kelly.

### Contrats d'entrée

- `Opportunity` acceptée, contenant au minimum le côté, la probabilité
  Oracle de ce côté, le prix exécutable retenu et l'edge net ;
- `SizingConfig`, contenant `bankroll`, `kelly_fraction_cap`,
  `max_stake_per_market`, `min_stake_usd`, le notionnel exécutable dérivé de
  la profondeur du carnet, et l'état des kill switches.

Valeurs par défaut verrouillées pour la v1 (voir [Décision 12](research.md)) :
`kelly_fraction_cap = 0.5`, `bankroll` fourni par une constante de
configuration, `max_stake_per_market = 20 $`, `min_stake_usd = 1 $`. Le
plafond `executable_notional` est ignoré pour la v1 (volumes trop faibles
pour que la liquidité soit limitante), et `kill_switch_active` est un simple
booléen tant que `execution/risk.py` n'existe pas.

### Calcul du sizing

La formule reprend la [Décision 9](research.md) sans la modifier :

```text
b = (1 - p) / p
kelly_fraction = max(0, (probability * (1 + b) - 1) / b)
Q = min(bankroll * kelly_fraction * kelly_fraction_cap,
  max_stake_per_market,
  executable_notional)
```

où `probability` est la probabilité Oracle du côté déjà accepté et `p` le
prix exécutable retenu par le pricing (jamais recalculé par le sizing, et
toujours brut de frais : les frais restent soustraits séparément du edge et
du P/L). Si `Q` résultant est inférieur à `min_stake_usd` (`1 $`), ou si un
kill switch est actif, `Q` DOIT valoir `0` avec un motif explicite
(`below_min_stake`, `kill_switch_active`, `opportunity_rejected` ou
`insufficient_liquidity`). `Q` est ensuite arrondi au `tick_size` Polymarket,
selon la même convention que `limit_price`.

### Ordre d'implémentation

1. Définir `SizingConfig` et `SizingDecision` (`Q`, `kelly_fraction`, chaque
   plafond appliqué, le motif et le mode `fixed` ou `dynamic`) avec des
   validations strictes, dans `models.py` ou un module `pricing/sizing.py`
   dédié.
2. Implémenter le calcul de la fraction de Kelly plafonnée et les trois
   plafonds (`bankroll`, mise maximale par marché, notionnel exécutable),
   avec retour explicite à `Q = 0` pour une `Opportunity` rejetée, un kill
   switch actif ou une mise sous le plancher.
3. Ajouter des tests unitaires synthétiques couvrant : `Opportunity` rejetée
   (aucun calcul de Kelly effectué), chaque plafond individuellement,
   `kill_switch_active`, la mise sous le plancher et une probabilité égale au
   prix (edge nul, `kelly_fraction = 0`).
4. Ajouter au replay un mode de sizing dynamique optionnel, comparable à la
   mise fixe existante (`--stake-usd`), avec les paramètres `bankroll`,
   `kelly_fraction_cap`, `max_stake_per_market` et `min_stake_usd` exposés en
   ligne de commande.
5. Exporter `Q`, `kelly_fraction`, chaque plafond appliqué, le motif et le
   mode de sizing dans le CSV et le rapport de replay, aux côtés des champs
   P/L existants.
6. Vérifier que le sizing dynamique et la mise fixe restent comparables sur
   le même jeu de fenêtres acceptées, afin de mesurer séparément l'effet du
   pricing et l'effet du sizing sur le P/L.

### Critères de validation sizing

- `Q` n'est jamais calculé par la formule de Kelly pour une `Opportunity`
  rejetée ou une issue non qualifiée directionnellement ; le résultat est
  directement `Q = 0` avec le motif `opportunity_rejected`.
- `Q` ne dépasse jamais `bankroll * kelly_fraction_cap`, `max_stake_per_market`
  ni le notionnel exécutable, même lorsque la fraction de Kelly brute serait
  plus grande.
- Un kill switch actif produit `Q = 0` indépendamment de l'edge ou de la
  probabilité.
- Une mise dynamique résultante sous `min_stake_usd` produit `Q = 0` plutôt
  qu'une mise résiduelle non significative.
- Le mode à mise fixe reste disponible comme baseline de comparaison et ne
  doit jamais être supprimé par l'ajout du sizing dynamique.
- `probability` et `p` utilisés par le sizing sont exactement ceux déjà
  validés par le pricing ; le sizing ne recalcule ni ne modifie l'edge net.

## Vérification de constitution après conception

- **Paper trading uniquement** : PASS. Le contrat de sortie ne contient aucun
  ordre, signature ou écriture réseau.
- **Les faits avant les hypothèses** : PASS. Les décisions de transport sont
  documentées dans [research.md](research.md) et les hypothèses de disponibilité
  restent explicitement mesurables.
- **Logique métier pure** : PASS. Le contrat sépare le message brut, le `Tick`
  normalisé et les consommateurs ; les tests synthétiques ne nécessitent pas le
  réseau.
- **Aucun changement silencieux** : PASS. Les gaps, rejets, latences,
  reconnexions et saturations sont exposés par l'état et les métriques.
- **Risque et périmètre conservateurs** : PASS. Le périmètre reste limité à
  BTCUSDT, aux flux publics et à l'environnement local v1.
- **Contrats explicites** : PASS. Le modèle et le contrat documentent les types,
  validations, séquences et limites de livraison.

**Résultat après conception** : PASS, aucune violation ni migration requise.

## Suivi de complexité

> Aucune violation de constitution n'est présente ; aucun suivi supplémentaire
> n'est requis.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
