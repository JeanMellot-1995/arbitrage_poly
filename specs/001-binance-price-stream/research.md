# Recherche : flux de prix Binance

## Décision 1 : utiliser les flux publics Binance en WebSocket

- **Décision** : utiliser le flux public `BTCUSDT` `bookTicker` comme source de
  prix v1.
- **Rationale** : le plan racine impose Binance comme proxy CEX et `bookTicker`
  fournit le meilleur bid/ask sans clé privée ni authentification. Son midpoint
  est le prix courant consommé par l'Oracle.
- **Alternative considérée pour le live** :
  - REST périodique : rejeté pour la latence et la perte de granularité.
  - Chainlink Data Streams : hors périmètre v1, car credentials et facturation
    sont requis.
  - Coinbase WebSocket : hors périmètre, car Binance est la source retenue par
    les spécifications.

## Décision 2 : séparer transport, normalisation et consommation

- **Décision** : le transport réseau et la normalisation vivent dans
  `price_collection/binance_ws.py`. L'Oracle, dans `oracle/oracle.py`, consomme
  uniquement les ticks normalisés depuis une file asynchrone bornée ; le
  pricing, le TWAP et le stockage ne sont pas appelés par l'adaptateur.
- **Rationale** : cette séparation permet l'injection d'événements synthétiques,
  évite la croissance mémoire illimitée et garantit le partage live/replay.
- **Alternatives considérées** :
  - Écrire directement dans Parquet depuis le WebSocket : rejeté, car le
    stockage appartient à la Phase 4.
  - Appeler le pricing dans le callback réseau : rejeté, car le pricing doit
    rester pur et indépendant de l'I/O.

## Décision 5 : privilégier la simplicité

- **Décision** : les scripts Python utilisent l'implémentation la plus directe
  qui respecte les contrats, les tests et les métriques attendues.
- **Rationale** : limiter les abstractions réduit le coût de maintenance et
  rend les comportements de collecte et d'Oracle plus faciles à vérifier.
- **Conséquence** : une nouvelle couche ou dépendance doit être justifiée par
  une complexité réellement supprimée ou par une frontière technique nécessaire.

## Décision 3 : séquence et timestamps

- **Décision** : suivre les identifiants de séquence séparément pour chaque flux,
  car la séquence `bookTicker` est propre à ce flux. Le `Tick.ts_ns`
  utilise l'horloge corrigée locale ; le timestamp source est conservé dans le
  message normalisé pour mesurer la latence.
- **Rationale** : une seule séquence mélangerait deux domaines d'événements et
  créerait de faux gaps. L'horloge locale fournit une chronologie cohérente
  pour les consommateurs, tandis que le timestamp source permet l'audit.
- **Alternatives considérées** :
  - Ajouter `aggTrade` au flux live : différé pour le MVP, car le prix courant
    live de l'Oracle est défini par le midpoint `bookTicker`. `aggTrades` reste
    toutefois la source historique officielle du replay, dans un mode séparé.
  - Utiliser uniquement le timestamp Binance : rejeté, car la latence de
    réception et l'offset local doivent rester mesurables.

## Décision 4 : reconnexion bornée et backpressure explicite

- **Décision** : reconnecter avec un délai exponentiel plafonné, réinitialiser
  le heartbeat à la reconnexion et exposer chaque rupture de séquence. La file
  est bornée et toute saturation produit une métrique ou un événement explicite.
- **Rationale** : la disponibilité du collecteur ne doit pas dépendre d'une
  connexion permanente, mais aucune perte ne doit être silencieuse.
- **Alternatives considérées** :
  - Reconnexion immédiate infinie : rejetée, car elle peut aggraver une panne
    et masquer l'état réel du service.
  - File non bornée : rejetée, car elle viole la contrainte de mémoire bornée.

## Décision 6 : modèle de probabilité terminale de l'Oracle

- **Événement modélisé** : `UP` signifie que le prix de résolution à la fin de
  la fenêtre est supérieur au prix de référence `R` ; `DOWN` est son complément.
  Ce n'est pas la probabilité qu'un chemin ait franchi `R` à un moment
  quelconque avant l'échéance.
- **Méthode v1 retenue** : modèle lognormal conditionnel de référence, sans
  drift estimé à très court horizon :

  $$P(UP \mid S_t, R, \tau, \sigma_t) =
  \Phi\left(\frac{\ln(S_t/R)}{\sigma_t\sqrt{\tau}}\right)$$

  où `S_t` est le midpoint courant, `R` le prix de référence, `\tau` le temps
  restant en années et `\sigma_t` la volatilité annualisée. `prob_down` est
  `1 - prob_up`. Si `\tau = 0`, la valeur est déterministe selon la convention
  de résolution documentée.
- **Volatilité** : utiliser d'abord une volatilité réalisée EWMA des
  log-returns de midpoints, avec un plancher numérique et une fenêtre
  configurable. Pour les `aggTrades`, les prix doivent d'abord pouvoir être
  échantillonnés sur une grille temporelle régulière, par exemple une seconde,
  car les intervalles entre trades sont irréguliers et peuvent créer des
  volatilités annualisées artificiellement élevées. Les estimateurs très
  sophistiqués ne sont pas justifiés avant d'avoir mesuré le bruit de
  microstructure.
- **Pourquoi cette baseline** : elle est explicable, rapide, reproductible,
  compatible avec le replay et fournit une probabilité plutôt qu'un simple
  signal directionnel. Elle constitue le test nul contre lequel toute méthode
  plus complexe doit être comparée.
- **Méthode candidate après collecte** : régression logistique réguliarisée sur
  des variables observables (`log(S_t/R)`, temps restant, volatilité, retours
  courts et déséquilibre bid/ask), avec validation temporelle walk-forward. La
  probabilité doit ensuite être calibrée sur des observations indépendantes
  (`sigmoid` en premier choix ; isotonic seulement avec suffisamment de
  données). Aucun modèle supervisé ne sera activé par défaut avant de battre
  la baseline sur Brier score et log loss hors échantillon.
- **Calibration des sorties** : une bonne accuracy directionnelle ne garantit
  pas des probabilités fiables. Les probabilités extrêmes doivent être
  contrôlées par une borne configurable et, lorsque suffisamment de données
  sont disponibles, par une calibration apprise hors échantillon. La calibration
  doit être évaluée séparément avec Brier score, log loss et une table de
  fiabilité ; elle ne doit jamais être ajustée sur la journée servant au score.
- **Baselines et robustesse** : comparer chaque variante à une probabilité
  constante de 50 % et à la fréquence empirique observée dans l'entraînement.
  Une seule journée de 288 fenêtres ne suffit pas pour conclure ; les résultats
  doivent être reproduits sur plusieurs journées et plusieurs horizons avant
  toute interprétation économique.
- **Alternatives rejetées pour la v1** :
  - drift historique estimé sur quelques minutes : variance élevée et coût de
    modèle supérieur au signal attendu ;
  - Monte Carlo GBM : inutile pour une résolution spot terminale et ajoute du
    bruit numérique ; à réserver à une résolution TWAP ou path-dependent ;
  - probabilité de premier passage : répond à un événement différent ;
  - réseau neuronal ou gradient boosting dès le départ : risque de fuite
    temporelle et de probabilités mal calibrées avec peu de marchés annotés ;
  - retour à la moyenne (Ornstein-Uhlenbeck) sur l'horizon de 5 minutes :
    testé empiriquement (voir validation ci-dessous) ; rejeté comme réglage
    par défaut faute d'amélioration reproductible.

- **Validation empirique du 2026-09-21 : retour à la moyenne testé comme
  challenger.** Un paramètre optionnel `reversion_speed` (exposé en CLI via
  `--mean-reversion-halflife-s`, désactivé par défaut) décale le déviation
  log observée d'un facteur `exp(-reversion_speed * tau_years)` avant le
  calcul de `z`, modélisant une fraction du mouvement courant qui "reviendrait"
  avant l'échéance. Comparé à la baseline à dérive nulle sur
  `data/BTCUSDT-aggTrades-2026-09-11_15.csv` (3 746 883 ticks, 1 424 fenêtres
  scorées) :

  | Demi-vie | Brier | Log loss | Bucket 0,8–1,0 : prédit / observé |
  |---|---|---|---|
  | 0 s (baseline) | 0,1145 | 0,3820 | 0,937 / 0,900 |
  | 15 s | 0,1835 | 0,5569 | 0,925 / 0,882 |
  | 30 s | 0,1315 | 0,4305 | 0,918 / 0,920 |
  | 60 s | 0,1165 | 0,3875 | 0,931 / 0,924 |
  | 120 s | 0,1139 | 0,3788 | 0,936 / 0,916 |
  | 240 s | 0,1138 | 0,3785 | 0,935 / 0,908 |
  | 600 s | 0,1141 | 0,3801 | 0,937 / 0,905 |

  Les demi-vies courtes (15–60 s), qui correspondent à un mécanisme de
  rebond de microstructure (bid-ask bounce), dégradent nettement le Brier
  score et le log loss. Les demi-vies proches ou supérieures à la durée de la
  fenêtre (120–600 s) n'apportent qu'un gain marginal (<1 % sur le Brier
  score), non distinguable du bruit sur 1 424 fenêtres. Séparément, sur le
  sous-ensemble des paris à edge élevé (edge ≥ 0,35, backtest économique),
  la probabilité Oracle moyenne (0,850) restait proche du taux de réussite
  réel (0,763) alors que le prix Polymarket moyen (0,314) en était très
  éloigné — l'écart observé provient donc principalement d'une sous-réaction
  du prix de marché, pas d'un défaut de calibration de l'Oracle que le retour
  à la moyenne corrigerait. **Décision : le modèle à dérive nulle reste le
  défaut** ; `reversion_speed` reste disponible comme option expérimentale
  (`reversion_speed=0.0` par défaut, aucun changement de comportement pour
  les appels existants). Voir aussi `specs/002-confirm-zero-drift-model/spec.md`.

**Références** : Cont (2001), *Empirical properties of asset returns* ;
Andersen, Bollerslev, Diebold et Labys (2003), *Modeling and forecasting
realized volatility* ; Cont, Stoikov et Talreja (2010), *A stochastic model
for order book dynamics* ; Niculescu-Mizil et Caruana (2005), *Predicting good
probabilities with supervised learning*. La documentation de calibration
scikit-learn rappelle que Brier score et log loss doivent être évalués sur des
prédictions hors échantillon et que l'isotonic regression est plus sujette au
sur-apprentissage sur de petits échantillons.

## Décision 7 : source historique du backtest

- **Décision** : utiliser les fichiers journaliers Binance `aggTrades` pour le
  backtest historique d'une journée complète, dans un mode distinct du mode
  live `bookTicker`.
- **Rationale** : Binance Public Data publie les `aggTrades` par symbole et par
  jour. Chaque ligne fournit un prix exécuté, une quantité et un timestamp,
  ce qui permet de calculer une référence, un prix terminal et une volatilité
  sur 288 fenêtres UTC de cinq minutes.
- **Limite** : un trade historique ne contient ni bid, ni ask, ni spread, ni
  profondeur. Le replay historique ne prétend donc pas reproduire le midpoint
  `bookTicker` ou la liquidité du carnet.
- **Convention** : le timestamp d'exécution Binance aligne les fenêtres ; le
  timestamp local de collecte n'intervient pas. Une fenêtre sans trade est
  partielle.
- **Modèle identifié** : les sorties historiques utilisent
  `terminal_lognormal_ewma_agg_trade`, afin d'empêcher une comparaison
  silencieuse avec les sorties `bookTicker`.
- **Alternative conservée** : une archive live `bookTicker` reste la référence
  de production, mais elle doit être collectée en continu et ne peut pas être
  reconstruite rétroactivement depuis l'API Binance.

## Décision 8 : calcul du prix limite acceptable

- **Décision** : calculer d'abord la volonté maximale de payer pour une part
  binaire, puis arrondir cette limite vers le bas au `tick_size`. Pour l'issue
  `UP` ou `DOWN`, `fair_probability` est respectivement `prob_up` ou
  `prob_down` :

  ```text
  max_limit_price = fair_probability
                    - fee_per_share
                    - slippage_buffer
                    - min_net_edge
  limit_price = floor_to_tick(max_limit_price)
  ```

  Les trois coûts soustraits sont exprimés en points de prix par part. Le
  `limit_price` est rejeté s'il est inférieur ou égal à zéro, supérieur à un,
  ou incompatible avec les limites de risque.
- **Frais proportionnels** : si les frais sont un taux `fee_rate` appliqué au
  prix payé, la forme non arrondie doit plutôt être :

  ```text
  max_limit_price = (fair_probability
                     - slippage_buffer
                     - min_net_edge) / (1 + fee_rate)
  ```

  Le contrat doit donc indiquer si `fees` est un montant par part ou un taux ;
  la v1 utilise un coût par part explicite pour rester déterministe et éviter
  toute ambiguïté.
- **Profondeur et slippage** : la profondeur du carnet ne modifie pas la
  limite économique. Elle sert ensuite à calculer le prix moyen pondéré, la
  quantité réellement disponible et le risque de fill partiel. Le
  `slippage_buffer` couvre uniquement le slippage futur ou l'incertitude entre
  le snapshot et l'exécution ; le slippage déjà mesuré dans le carnet ne doit
  pas être soustrait une seconde fois.
- **Publication** : en mode passif, le prix proposé est au plus
  `min(limit_price, best_bid + tick_size)` ; en mode agressif, seules les asks
  dont le prix est inférieur ou égal à `limit_price` sont consommées. Dans les
  deux cas, aucun prix coté ne peut dépasser `limit_price`.
- **Rationale** : le prix limite est une contrainte économique dérivée de la
  FairValue, pas une estimation du meilleur ask ni une décision d'exécution.
  Séparer `max_limit_price`, `limit_price`, `quoted_price` et
  `executable_price` permet d'auditer l'effet des frais, de l'arrondi, de la
  profondeur et du mode de cotation.
- **Alternative rejetée** : définir la limite à partir du meilleur ask ou du
  prix moyen du carnet. Cela transforme la limite en prix de marché et peut
  accepter une transaction dont la valeur attendue nette est insuffisante.

## Décision 9 : mise en dollars et paiement d'une issue binaire

- **Décision** : `Q` représente la mise en dollars, `p` le prix d'achat d'une
  part et une part gagnante paie `$1`. Une mise `Q` achète `Q / p` parts ; le
  paiement brut en cas de succès est donc `Q / p` et le rendement brut sur la
  mise vaut :

  ```text
  A = (1 - p) / p
  paiement_gagnant = Q * (1 + A) = Q / p
  profit_gagnant_brut = Q * A
  ```

- **Conséquence** : `A` est observable à partir du prix exécutable, et non une
  constante fournie indépendamment du marché. Frais, slippage et fill partiel
  doivent être appliqués au nombre de parts et au prix moyen réellement
  exécutés.
- **Sizing** : le montant `Q` peut dépendre de la probabilité Oracle, mais
  uniquement après validation du prix limite et de l'edge net. Le sizing
  dynamique envisagé est une fraction de Kelly plafonnée par le bankroll, la
  mise maximale par marché, la profondeur disponible et les kill switches.
  Pour la v1, une mise fixe plafonnée reste acceptable comme baseline afin de
  mesurer séparément le pricing et le sizing.
- **Rationale** : séparer `probability`, `p`, `A`, `Q`, `limit_price` et
  `executable_notional` évite de confondre probabilité de succès, rendement
  conditionnel et montant risqué.

## Décision 10 : prix Polymarket historiques à T-60s

- **Décision** : le replay économique doit récupérer séparément les prix des
  deux tokens binaires Polymarket à l'échéance de décision
  `decision_ts = window_end - 60s`. Gamma sert à découvrir le marché et ses
  métadonnées ; l'API CLOB sert à récupérer l'historique de prix des tokens.
  Le replay ne doit pas déduire les prix Polymarket des trades Binance.
- **Sélection temporelle** : pour chaque token `UP` et `DOWN`, sélectionner le
  dernier point de prix dont `timestamp <= decision_ts`. Un point postérieur
  est interdit pour éviter la fuite de données. Conserver le timestamp source,
  le type de prix (`trade`, `midpoint` ou `last`) et l'écart à `decision_ts`.
- **Fenêtre de fraîcheur** : la récupération doit couvrir une petite fenêtre
  autour de `decision_ts`, mais la sélection reste causale. Si aucun point
  n'est disponible dans la fraîcheur maximale configurée, le prix est absent
  et la simulation économique est exclue pour cette fenêtre.
- **Correspondance des marchés** : la découverte doit utiliser l'identifiant
  de la fenêtre, sa borne UTC, le symbole et les métadonnées du marché. Le
  contrat doit vérifier que les deux tokens représentent bien les issues
  `UP` et `DOWN`, conserver le `market_id` et refuser une correspondance
  ambiguë.
- **Résilience** : pagination, limitation de débit, réponses vides, erreurs
  HTTP et JSON invalide doivent être observables. Les réponses brutes ou un
  cache local déterministe doivent pouvoir être conservés pour rejouer sans
  réseau et auditer la sélection temporelle.
- **Séparation des métriques** : un prix Polymarket manquant exclut le P/L,
  l'edge et le sizing de la fenêtre, mais ne retire pas sa `FairValue` ni son
  Brier score du rapport Oracle. Le rapport doit distinguer
  `oracle_scored_windows` et `economic_priced_windows`.
- **Alternative rejetée** : utiliser le dernier prix API disponible sans
  vérifier son timestamp. Cette méthode introduit une fuite de données et peut
  attribuer au modèle une information apparue après la décision.
- **Correction validée sur données réelles (2026-09-18)** : une requête Gamma
  réelle (`/markets?limit=1`) a confirmé le schéma `clobTokenIds`/`outcomes`
  encodés en chaîne JSON, mais a aussi révélé que `startDate` ne correspond
  **pas** au début de la fenêtre (observé ~24h avant la résolution, probable
  heure de création du marché). Seul `endDate` correspond exactement à
  `window_end`. Le vrai identifiant de fenêtre est le `slug`, au format
  `btc-updown-{5m|15m|1h}-{window_start_unix_seconds}`, partagé par les
  variantes de durée démarrant au même instant. La découverte interroge donc
  Gamma par `slug` reconstruit, puis vérifie `endDate == window_end` en
  défense en profondeur, au lieu de comparer `startDate`/`endDate` bruts.
- **Endpoint déprécié** : `/markets` répond avec un en-tête
  `warning: 299 - "use /markets/keyset"` et un `sunset` déjà dépassé lors du
  test. Il fonctionne encore mais pourrait être retiré ; envisager
  `/markets/keyset` si `/markets` cesse de répondre.
- **`closed=true` requis pour les marchés passés** : sans ce paramètre, Gamma
  ne retourne que les marchés actifs/non clôturés, donc une recherche par
  `slug` seul renvoie une liste vide pour toute fenêtre déjà résolue (le cas
  général d'un replay historique). `GammaMarketDiscovery` envoie désormais
  systématiquement `closed=true`, cette adaptation étant scopée au replay
  historique où la fenêtre est toujours dans le passé au moment de la requête.

## Décision 11 : garde-fou directionnel avant tout calcul d'edge

- **Décision** : une issue binaire n'est jamais achetée si l'Oracle ne lui
  attribue pas une probabilité majoritaire. La condition d'achat est
  `prob_up > 0.5 and up_price < prob_up` pour `UP`, et symétriquement
  `prob_down > 0.5 and down_price < prob_down` pour `DOWN`. Si aucune des deux
  conditions n'est remplie, aucun pari n'est simulé ou proposé, même si l'une
  des deux issues affiche un prix plus bas que l'autre.
- **Rationale** : comparer uniquement `edge_UP = prob_up - up_price` à
  `edge_DOWN = prob_down - down_price` et retenir le plus grand des deux peut
  faire acheter un côté minoritaire (`prob <= 0.5`) simplement parce qu'il est
  bon marché. Ce comportement a été observé sur des données réelles du
  09-11-2026 : un pari `DOWN` était accepté alors que l'Oracle attribuait 95 %
  de probabilité à `UP`, uniquement parce que le prix `DOWN` (0.035) était très
  inférieur à son propre `prob_down` (0.05). Le garde-fou directionnel élimine
  cette classe de paris.
- **Conséquence mesurée** : sur le backtest 2026-09-11 à 2026-09-14 avec le
  cache Polymarket enregistré, le nombre de paris passe de 1071 à 687, le taux
  de réussite passe d'environ 57 % à environ 83 %, et le P/L total simulé
  passe de +11 082 $ à +6 249 $ pour une mise totale bien plus faible
  (6 870 $ contre 10 710 $).
- **Portée** : ce garde-fou est implémenté dans `apps/replay.py::_simulate_pnl`
  pour la simulation P/L historique et DOIT être conservé comme condition
  préalable dans le futur `pricing/opportunity.py`, avant tout calcul d'edge
  net, de prix limite ou de sizing.
- **Alternative rejetée** : choisir le côté au meilleur edge brut entre `UP`
  et `DOWN` sans exiger `prob > 0.5`. Rejetée car elle revient à parier contre
  la conviction directionnelle de l'Oracle lui-même.

## Décision 12 : sizing dynamique comparé à la mise fixe

- **Décision** : implémenter le sizing dynamique de la [Décision 9](#décision-9-mise-en-dollars-et-paiement-dune-issue-binaire)
  comme un mode optionnel du replay, activable en plus de la mise fixe
  existante (`--stake-usd`), sans jamais rouvrir la décision d'achat prise par
  le pricing (garde-fou directionnel puis edge net).
- **Rationale** : la mise fixe isole déjà la qualité du pricing du reste du
  système ; conserver les deux modes permet de mesurer séparément l'effet du
  choix directionnel, de l'edge et du sizing sur le P/L simulé, plutôt que de
  mélanger les trois effets dans un seul chiffre.
- **Plafonnement** : la fraction de Kelly brute est systématiquement
  plafonnée (`kelly_fraction_cap`) avant d'être appliquée au bankroll, en plus
  des plafonds de mise maximale par marché et de notionnel exécutable. Un
  Kelly plein est rejeté pour la v1 car il maximise la croissance
  logarithmique attendue sans borner la variance, ce qui est incompatible avec
  un périmètre de risque conservateur.
- **Alternative rejetée** : appliquer le sizing dynamique avant la
  qualification directionnelle ou le seuil d'edge net, dans l'espoir qu'un `Q`
  faible atténuerait un edge insuffisant. Rejetée car cela mélange deux
  garde-fous indépendants et rendrait `edge_below_threshold` et
  `insufficient_liquidity` indiscernables du résultat final.
- **Valeurs verrouillées (clarifiées le 2026-09-20)** :
  - `kelly_fraction_cap = 0.5` (demi-Kelly) ;
  - `bankroll` provient d'une constante de configuration, pas d'une variable
    d'environnement ni d'un flag CLI obligatoire ;
  - `max_stake_per_market = 20 $`, reprenant la décision v1 déjà validée
    (« 20 $ max par tentative ») ;
  - `min_stake_usd = 1 $` : un `Q` dynamique résultant sous ce plancher
    devient `Q = 0` ;
  - `executable_notional` (plafond de profondeur) est **ignoré** pour la v1 :
    aux volumes traités (1 à 20 $ par pari), la liquidité Polymarket n'est pas
    un facteur limitant mesurable, et `pricing/edge.py` (qui exposerait une
    vraie profondeur de carnet) n'existe pas encore. Ce point DOIT être
    réévalué si la mise maximale augmente significativement ;
  - `kill_switch_active` est un simple booléen porté par `SizingConfig` dès la
    v1, sans attendre `execution/risk.py` ;
  - `p` reste le prix exécutable brut dans la formule de Kelly ; les frais
    sont soustraits séparément du edge et du P/L, jamais intégrés dans `p` ;
  - `Q` est arrondi au `tick_size` Polymarket, selon la même convention que
    `limit_price` (Décision 8).

## Décision 13 : frais Polymarket conditionnés au type d'exécution (maker/taker)

- **Décision** : reprendre le barème réel Polymarket au lieu d'un frais
  constant configuré arbitrairement. Un ordre limit qui ne croise pas
  immédiatement le carnet au moment de sa soumission reste **maker** et n'est
  **pas facturé**. Un ordre qui correspond immédiatement à un ordre déjà
  présent dans le carnet devient **taker** et est facturé au taux de la
  catégorie Crypto, actuellement **0.07 %** du montant exécuté. Cette
  distinction s'applique même à un ordre initialement conçu comme passif : si
  son prix croise le carnet au moment où il est placé, il est exécuté comme
  taker et facturé.
- **Rationale** : cette règle répond directement à l'ambiguïté laissée
  ouverte par la [Décision 8](#décision-8-calcul-du-prix-limite-acceptable)
  sur la nature de `fees` (montant par part ou taux). Les frais Polymarket
  sont un taux, mais conditionné par le résultat réel de l'exécution, pas une
  constante indépendante du mode de cotation.
- **Correspondance avec les modes de cotation** : le mode **passif** défini
  dans [plan.md](plan.md#calcul-du-prix-limite) (coter au meilleur bid, sans
  jamais dépasser `limit_price`) ne consomme aucune ask existante et reste
  donc maker par construction, sauf si les conditions de marché changent entre
  la cotation et son exécution. Le mode **agressif** consomme explicitement
  des asks existantes en dessous ou égales à `limit_price` et est donc
  systématiquement taker.
- **Conséquence sur le replay historique** : `apps/replay.py` expose
  aujourd'hui `--fee-per-share` comme un montant constant par part, appliqué
  à toutes les fenêtres sans distinction maker/taker, car le backtest ne
  modélise ni carnet ni priorité d'exécution — seul le dernier prix
  Polymarket observé à `T-60s` est connu. Ce paramètre reste une
  simplification de sensibilité et DOIT être révisé lorsque
  `pricing/opportunity.py` distinguera réellement les modes passif et
  agressif.
- **Alternative rejetée** : appliquer 0.07 % à toute exécution sans
  distinction. Rejetée car elle surestimerait systématiquement le coût du
  mode passif et fausserait la comparaison entre les deux modes de cotation.
- **Implémenté (clarifié le 2026-09-20)** : le replay traite désormais toute
  exécution simulée comme taker. `apps/replay.py` remplace l'ancien
  `--fee-per-share` (montant constant par part) par `--fee-rate` (taux sur le
  notionnel `stake_usd`, défaut `0.0007`) : `fees_usd = stake_usd * fee_rate`.
  Ce choix reste une simplification assumée du backtest, documentée comme
  telle tant qu'aucun carnet réel n'est disponible pour distinguer maker et
  taker fenêtre par fenêtre.
- **Validation sur un marché résolu réel (2026-09-11 00:10-00:15 UTC)** : le
  lookup par `slug` (`btc-updown-5m-1789085400`) avec `closed=true` retourne
  bien le marché fermé attendu, confirmant que l'historique reste interrogeable
  au moins une semaine après résolution. `eventMetadata.finalPrice` et
  `priceToBeat` montrent que Polymarket résout via le **TWAP Chainlink 60s**
  du flux `btc-usd-twap-60s-streams`, et non le dernier prix spot brut ; ceci
  renforce la mise en garde déjà présente sur Binance comme proxy imparfait
  de la vérité terrain de résolution.
- **CLOB `startTs`/`endTs` non garanti côté serveur** : une requête sur un
  token de marché quasi neuf (très peu de trades) a renvoyé un unique point
  situé ~23,5 h avant la plage demandée, montrant que le serveur ne filtre pas
  toujours strictement par `startTs`/`endTs`. Cela ne casse pas la garantie
  anti-fuite : la sélection causale (`ts_ns <= decision_ts_ns`) et le contrôle
  de fraîcheur (`stale_price` au-delà de `max_age_ns`) sont appliqués
  côté client dans `historical_prices.py`, indépendamment de ce que renvoie
  le serveur. Un marché avec un véritable historique de trading (marché
  résolu, pas fraîchement ouvert) doit être utilisé pour confirmer que la
  plage est habituellement respectée en pratique.
- **Validation CLOB confirmée sur un marché réellement tradé (2026-09-18)** :
  la même requête sur le token `UP` du marché résolu `2026-09-11 00:10-00:15`
  a renvoyé 5 points, tous strictement contenus dans `[startTs, endTs]`. Le
  point causal le plus proche de `T-60s` a un âge de 48 s, largement sous le
  seuil de fraîcheur par défaut. L'anomalie précédente est donc spécifique aux
  marchés quasi neufs sans historique, pas un défaut général de l'API. Le
  pipeline découverte (`slug` + vérification `endDate`) et lecture CLOB est
  validé de bout en bout sur données réelles.
