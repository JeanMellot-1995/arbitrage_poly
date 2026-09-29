# Spécification de fonctionnalité : Flux de prix Binance

**Feature Branch**: `001-binance-price-stream`  
**Created**: 2026-09-18  
**Status**: In Progress
**Input**: User description: "/speckit.specify on utilisera dans un premier temps les WebSocket Binance pour récupérer les prix ; collecte dans `price_collection`, Oracle dans un module distinct"

## Scénarios utilisateur et tests

### User Story 1 - Recevoir les prix BTC en temps réel (Priorité : P1)

En tant qu'observateur du marché, je veux recevoir les mises à jour publiques du
prix BTC afin d'alimenter les calculs de juste valeur sans dépendre d'une
saisie manuelle ni d'un polling lent.

**Pourquoi cette priorité** : Le flux de prix est la donnée de base de toute
observation et doit être disponible avant les composants de pricing ou de paper
trading.

**Test indépendant** : Démarrer le lecteur pendant une période d'observation,
recevoir des événements BTC et vérifier que chaque événement contient un prix,
une quantité, une source et un timestamp exploitable.

**Scénarios d'acceptation** :

1. **Étant donné** un accès réseau disponible, **quand** le lecteur démarre,
   **alors** il s'abonne aux flux publics BTC attendus et publie les événements
   normalisés reçus.
2. **Étant donné** un événement de marché valide, **quand** il est reçu,
   **alors** le système conserve son prix, sa quantité, son timestamp UTC et son
   identifiant de séquence sans perte de précision.
3. **Étant donné** plusieurs événements successifs, **quand** ils sont publiés,
   **alors** leur ordre et leur source permettent de détecter une rupture de
   séquence.

### User Story 2 - Maintenir le flux malgré une interruption (Priorité : P1)

En tant qu'opérateur de collecte, je veux que le lecteur se reconnecte après une
coupure afin que les interruptions temporaires ne provoquent pas l'arrêt du
collecteur.

**Pourquoi cette priorité** : Une collecte silencieusement interrompue rend les
mesures de basis et les résultats de replay non fiables.

**Test indépendant** : Simuler une fermeture du WebSocket, observer la
reconnexion avec un délai borné, puis vérifier que les événements sont à nouveau
publiés lorsque la connexion revient.

**Scénarios d'acceptation** :

1. **Étant donné** une connexion interrompue, **quand** le lecteur détecte la
   fermeture ou l'absence de heartbeat, **alors** il signale l'état déconnecté
   et tente une reconnexion avec un délai progressif borné.
2. **Étant donné** une reconnexion réussie, **quand** le flux reprend,
   **alors** le lecteur reprend la publication des événements et remet à zéro
   l'état d'erreur récupérable.
3. **Étant donné** une rupture de séquence détectée, **quand** le lecteur la
   constate, **alors** il émet une alerte et expose cette rupture au collecteur
   au lieu de la masquer.

### User Story 3 - Transmettre les données sans effet de bord (Priorité : P2)

En tant que composant de pricing ou de stockage, je veux consommer des ticks
normalisés depuis une interface bornée afin de rester découplé du transport
réseau et de pouvoir tester le traitement hors ligne.

**Pourquoi cette priorité** : Le découplage permet de partager la même logique
entre observation en direct et replay, conformément à la constitution.

**Test indépendant** : Remplacer le transport réseau par une séquence synthétique
et vérifier que les mêmes événements normalisés sont consommés dans le même
ordre.

**Scénarios d'acceptation** :

1. **Étant donné** un flux d'événements valides, **quand** un consommateur les
   lit, **alors** il reçoit uniquement le contrat de données normalisé du
   lecteur.
2. **Étant donné** une file saturée, **quand** de nouveaux événements arrivent,
   **alors** le système applique une politique explicite de limitation et expose
   l'incident, sans croissance mémoire illimitée.
3. **Étant donné** le lecteur en version 1, **quand** il traite les données,
   **alors** il n'effectue aucune écriture vers Binance, Polymarket ou une
   blockchain.

### User Story 4 - Rejouer l'Oracle sur une collecte réelle (Priorité : P1)

En tant qu'analyste, je veux rejouer une collecte Binance archivée afin de
produire les `FairValue` de l'Oracle et de vérifier leur calibration sur des
fenêtres terminées, sans ouvrir de connexion réseau.

Le périmètre du backtest historique est une journée civile UTC complète,
découpée en 288 fenêtres contiguës de cinq minutes : `00:00-00:05`,
`00:05-00:10`, `00:10-00:15`, etc. L'archive d'entrée est le fichier journalier
Binance `aggTrades`. Ce mode est distinct du mode live `bookTicker` : il utilise
les prix de transactions et ne reconstitue pas le midpoint ni la profondeur du
carnet.

**Pourquoi cette priorité** : une probabilité non évaluée sur des observations
réelles ne permet pas de distinguer un modèle utile d'une simple intuition.

**Test indépendant** : fournir un fichier journalier `aggTrades`, exécuter le
replay historique et vérifier qu'une sortie contient une `FairValue` par tick
accepté ainsi que l'issue terminale de chaque fenêtre.

**Scénarios d'acceptation** :

1. **Étant donné** un fichier journalier `aggTrades`, **quand** le replay le lit,
  **alors** il reconstruit les ticks dans l'ordre du timestamp d'exécution et
  les injecte dans `PriceOracle.observe()` sans accès réseau.
2. **Étant donné** un fichier contenant des lignes de transactions valides,
  **quand** le replay historique le traite, **alors** seules les lignes
  `binance.agg_trade` sont transmises à l'Oracle historique et les autres sont
  comptées comme ignorées.
3. **Étant donné** une collecte qui commence après le début d'une fenêtre ou se
  termine avant sa fin, **quand** le replay prépare l'évaluation, **alors** la
  fenêtre est marquée incomplète et exclue des métriques de calibration.
  Le prix de référence est le premier `aggTrade` observé à partir du début de
  la fenêtre ; un trade à la nanoseconde exacte de la borne n'est pas requis.
  Le prix terminal est le dernier `aggTrade` strictement antérieur à la fin de
  la fenêtre.
4. **Étant donné** une fenêtre complète, **quand** son dernier prix est connu,
   **alors** le replay calcule l'issue terminale `UP` ou `DOWN` par comparaison
   avec le prix de référence ; une égalité produit `TIE`, comptabilisé mais
   exclu des métriques binaires.
5. **Étant donné** un replay reproductible, **quand** il est relancé sur le
  même fichier et les mêmes paramètres, **alors** il produit les mêmes
  probabilités, issues et métriques.
6. **Étant donné** une fenêtre complète, **quand** le replay calcule ses
  métriques, **alors** il retient une seule `FairValue`, choisie comme la
  dernière observation disponible à `T-60s` ou avant, et n'utilise aucune
  observation postérieure pour le scoring.

### Spécification détaillée du backtest

Le backtest est une évaluation historique locale de l'Oracle. Il ne simule pas
un carnet Polymarket, ne place pas d'ordre et ne mesure pas la rentabilité d'une
stratégie. Son objectif est de mesurer séparément la capacité directionnelle de
l'Oracle et la fiabilité de ses probabilités.

#### Données d'entrée

- L'entrée de référence est une archive journalière officielle Binance
  `aggTrades` pour `BTCUSDT`.
- Le fichier DOIT être identifié par sa date UTC, son symbole et sa source.
- Le replay DOIT utiliser le timestamp d'exécution Binance, converti en
  nanosecondes, pour ordonner les observations et affecter les fenêtres.
- Les lignes valides DOIVENT être traitées dans l'ordre temporel. Une ligne
  malformée, un prix non positif, une quantité négative ou un timestamp en
  recul DOIT être rejeté et comptabilisé avec son numéro de ligne.
- Le replay DOIT fonctionner sans connexion réseau et ne DOIT modifier le
  fichier d'entrée.
- Les observations `aggTrades` ne DOIVENT PAS être décrites comme des données
  `bookTicker` : elles représentent des transactions exécutées, pas un
  midpoint ni la profondeur disponible.

#### Découpage des fenêtres

- Une journée UTC complète est découpée en `288` fenêtres contiguës de cinq
  minutes, alignées sur les bornes UTC.
- Pour une fenêtre `[T, T+300s)`, le premier trade observé avec un timestamp
  `>= T` devient le prix de référence.
- Le dernier trade avec un timestamp `< T+300s` devient le prix terminal.
- Une fenêtre sans référence ou sans prix terminal est `partial` et est
  exclue des métriques binaires, mais reste dans le rapport.
- L'issue est `UP` si `terminal_price > reference_price`, `DOWN` si
  `terminal_price < reference_price` et `TIE` sinon.
- Les fenêtres `TIE` sont conservées dans les compteurs mais exclues du Brier
  score, du log loss et de la table de fiabilité binaires.

#### Production et sélection des prédictions

- L'Oracle DOIT produire des `FairValue` au fil des trades acceptés et
  conserver le modèle, la volatilité, le prix courant et le temps restant.
- La métrique de référence utilise une seule prédiction par fenêtre.
- Pour la configuration v1, cette prédiction est la dernière `FairValue` dont
  le timestamp est inférieur ou égal à `T+240s`, soit `T-60s` avant la fin.
- Une observation postérieure à `T+240s` NE DOIT PAS influencer cette
  prédiction ni ses métriques.
- Le replay DOIT permettre de tester d'autres offsets, au minimum `T-120s`,
  `T-60s` et `T-30s`, en les rapportant séparément.
- Une fenêtre complète sans `FairValue` disponible avant l'offset est
  `excluded` du scoring et son motif est comptabilisé.

#### Volatilité et confiance

- En mode `agg_trade`, le calcul de volatilité DOIT agréger les prix sur une
  grille régulière d'une seconde par défaut avant de calculer les log-returns
  et l'EWMA. Le prix représentatif v1 d'une seconde est le dernier prix
  observé dans cette seconde ; l'intervalle sans trade conserve le dernier prix
  connu uniquement pour permettre une série temporelle régulière et ne produit
  pas de nouveau mouvement artificiel.
- Le rapport DOIT conserver la granularité, la fenêtre EWMA, le facteur de
  lissage et le plancher utilisés.
- Le système DOIT borner les probabilités finales avec un plancher et un
  plafond configurables. La configuration de référence est `[0.05, 0.95]`.
- Le bornage DOIT être appliqué après le calcul brut de l'Oracle et avant le
  scoring. `prob_down` DOIT rester le complément de la probabilité bornée.
- Le rapport DOIT exposer `prob_up_raw`, `prob_up` et les bornes utilisées, ou
  fournir une information équivalente permettant de mesurer l'effet du
  bornage sur Brier score et log loss.
- Le bornage ne DOIT PAS être présenté comme une calibration : il limite les
  erreurs extrêmes, mais ne démontre pas que les probabilités sont justes.

#### Métriques et baselines

Le rapport DOIT contenir, séparément pour chaque période évaluée et pour
l'agrégation globale :

- `scored_windows`, `complete_windows`, `partial_windows`, `excluded_windows`
  et `tie_windows` ;
- le taux d'acceptation des lignes et les lignes rejetées ;
- l'accuracy directionnelle obtenue avec la règle `prob_up >= 0.5` ;
- le Brier score, le log loss et une table de fiabilité par intervalles ;
- la moyenne, le minimum et le maximum de `prob_up` ;
- la répartition des probabilités extrêmes et des erreurs à forte confiance ;
- une baseline constante à `50 %` pour le Brier score et le log loss ;
- une baseline fondée sur la fréquence empirique `UP` de la période
  d'entraînement, lorsqu'une période d'entraînement est disponible.

Le rapport DOIT distinguer :

- la qualité directionnelle : le côté de `0.5` est-il généralement correct ?
- la qualité probabiliste : une prédiction à `70 %` se réalise-t-elle environ
  dans `70 %` des cas ?

#### Calibration et validation temporelle

- Une calibration `sigmoid` ou isotone ne peut être entraînée que sur une
  période historique antérieure à la période évaluée.
- La journée évaluée ne DOIT PAS servir à ajuster la calibration, le plafond,
  le choix de l'horizon ou les paramètres du modèle.
- La validation DOIT être walk-forward lorsque plusieurs journées sont
  disponibles : entraînement sur le passé, calibration sur une période
  ultérieure, évaluation sur une période encore non observée.
- Une seule journée de `288` fenêtres peut fournir un signal exploratoire,
  mais ne suffit pas à conclure sur la robustesse du modèle.
- Les résultats DOIVENT être comparés sur plusieurs journées et, si possible,
  sur des régimes de volatilité différents.

#### Limites économiques

Le backtest Oracle ne permet pas à lui seul de conclure à une opportunité
Polymarket. Une évaluation économique séparée est nécessaire et DOIT intégrer
les prix bid/ask exécutables, la profondeur, les frais, le slippage, la
latence, la probabilité de fill et un seuil minimal d'edge. Une amélioration du
Brier score ou de la calibration ne constitue pas une preuve de rentabilité.

#### Prix Polymarket historiques à T-60s

Pour chaque fenêtre complète, le replay DOIT pouvoir découvrir le marché
Polymarket correspondant, identifier ses tokens `UP` et `DOWN`, puis récupérer
leurs prix historiques auprès de l'API Polymarket. La cible temporelle est
`decision_ts = window_end - 60s`.

Le prix retenu pour chaque token DOIT être le dernier prix dont le timestamp
est inférieur ou égal à `decision_ts`. Un prix reçu après cette échéance NE
DOIT PAS être utilisé. Le replay DOIT conserver le `market_id`, les token IDs,
les prix retenus, leurs timestamps, leur fraîcheur et la source de chaque prix.

Si le marché, un token ou un prix valide est absent, la fenêtre DOIT être
exclue du calcul d'edge, de sizing et de P/L, mais rester incluse dans les
métriques Oracle lorsque sa `FairValue` est évaluable. Le rapport DOIT
différencier les fenêtres scorées par l'Oracle des fenêtres tarifées par
Polymarket.

#### Mise, paiement et sizing

Pour une position acheteuse sur une issue binaire, `Q` désigne la mise en
dollars effectivement engagée et `p` le prix d'achat par part. Une part paie
`$1` si l'issue gagne et `$0` sinon. Le nombre de parts achetées est donc
`N = Q / p`, sous réserve des frais et de l'exécution réelle.

Le coefficient `A` n'est pas une constante connue à l'avance : il dépend du
prix d'exécution et des frais. Sans frais, le paiement brut en cas de succès
est `Q / p = Q * (1 + A)`, avec :

```text
A = (1 - p) / p
```

Le profit brut est `Q * A`. Après frais, slippage et exécution partielle, le
profit réalisé doit être calculé à partir des parts effectivement acquises et
du prix moyen réellement payé ; il ne doit pas être déduit d'un `A` fixe.

La gestion du risque DOIT pouvoir calculer une quantité `Q` dynamique, mais
elle ne doit être calculée qu'après validation de l'edge net. Le sizing
dynamique utilise une fraction prudente d'un sizing de type Kelly, plafonnée par le
capital disponible, une mise maximale par marché et la liquidité exécutable :

```text
b = (1 - p) / p
kelly_fraction = max(0, (probability * (1 + b) - 1) / b)
Q = min(bankroll * kelly_fraction * kelly_fraction_cap,
  max_stake_per_market,
  executable_notional)
```

`probability` est la probabilité Oracle de l'issue et `p` est le prix limite
ou le prix moyen d'exécution retenu selon le mode. La v1 DOIT également
imposer un plancher de taille et retourner `Q = 0` lorsque l'edge net est sous
le seuil, lorsque le prix est invalide, ou lorsque la profondeur est
insuffisante. Le sizing ne doit jamais augmenter le prix limite ni contourner
un kill switch.

Le premier mode opérationnel peut rester à mise fixe pour isoler la qualité
du pricing ; il doit toutefois exposer `Q`, la raison du sizing et les plafonds
appliqués afin que le passage au sizing dynamique soit mesurable.

### Cas limites

- Le service de flux est indisponible au démarrage : le lecteur reste dans un
  état observable et réessaie selon une stratégie bornée.
- Le flux envoie un message inattendu, incomplet ou invalide : le message est
  rejeté, l'erreur est journalisée et la connexion n'est pas considérée comme
  saine sans preuve contraire.
- Plusieurs messages portent le même identifiant de séquence : le doublon est
  détectable et ne doit pas produire un faux tick accepté.
- Une rupture de séquence est observée après reconnexion : l'incident est
  signalé afin que les données concernées puissent être exclues ou resynchronisées.
- L'horloge locale présente un offset supérieur au seuil configuré : les
  événements restent identifiés mais le système déclenche le kill switch prévu.
- La consommation est plus lente que la réception : la mémoire reste bornée et
  l'opérateur peut mesurer les événements perdus ou retardés.

## Exigences

### Exigences fonctionnelles

- **FR-001** : Le système DOIT recevoir les mises à jour publiques du marché
  BTC/USDT via les WebSocket Binance configurés pour la v1.
- **FR-002** : Le système DOIT traiter le flux `bookTicker` afin de fournir un
  prix courant calculé comme le midpoint du meilleur bid et du meilleur ask.
- **FR-003** : Le système DOIT normaliser chaque tick avec un timestamp UTC en
  nanosecondes, un prix, une quantité, une source et un identifiant de séquence
  lorsqu'il est fourni par la source.
- **FR-004** : Le système DOIT publier les ticks via une interface asynchrone
  bornée et indépendante du consommateur.
- **FR-005** : Le système DOIT maintenir la connexion par heartbeat et détecter
  les fermetures, timeouts et absences de messages au-delà du délai configuré.
- **FR-006** : Le système DOIT tenter une reconnexion automatique avec un délai
  progressif, un plafond de délai et une visibilité sur le nombre de tentatives.
- **FR-007** : Le système DOIT détecter et exposer les doublons, ruptures ou
  incohérences de séquence observables dans les messages reçus.
- **FR-008** : Le système DOIT appliquer une politique de file bornée lorsque la
  consommation ne suit pas la réception, sans fuite mémoire silencieuse.
- **FR-009** : Le système DOIT journaliser les états de connexion, reconnexion,
  erreur de décodage, perte de données et reprise du flux avec des timestamps
  exploitables.
- **FR-010** : Le système NE DOIT effectuer aucune opération d'écriture réseau
  vers Binance, Polymarket ou une blockchain.
- **FR-011** : Le système DOIT permettre l'injection d'événements synthétiques
  afin de tester la normalisation et la détection des séquences sans réseau.
- **FR-012** : Le système DOIT rendre mesurables la latence de réception, l'âge
  du dernier tick, le nombre de reconnexions et le nombre d'événements rejetés.
- **FR-013** : Pour chaque fenêtre de cinq minutes, l'Oracle DOIT mémoriser le
  prix de référence de début, suivre le prix courant, la volatilité et le temps
  restant, puis produire une `FairValue` avec `prob_up` et `prob_down`.
- **FR-014** : Le module `pricing` DOIT comparer la `FairValue` au carnet
  Polymarket, calculer l'edge net et produire une décision d'opportunité.
- **FR-015** : Le système DOIT fournir un replay historique local lisant un
  fichier journalier Binance `aggTrades` et reconstruisant les `Tick` à partir
  du timestamp d'exécution, sans connexion réseau.
- **FR-016** : Le replay DOIT signaler les lignes malformées ou non monotones
  avec leur numéro de ligne et DOIT traiter le reste du fichier sans
  interruption silencieuse.
- **FR-017** : Le replay historique DOIT transmettre à l'Oracle uniquement les
  ticks dont `source` vaut `binance.agg_trade`; les autres sources DOIVENT être
  ignorées et comptabilisées. Le replay live `bookTicker` reste séparé.
- **FR-018** : Le replay DOIT identifier les fenêtres complètes uniquement si
  un tick est observable dans la fenêtre à partir de son début ainsi qu'avant
  sa fin. Le premier tick observé devient le prix de référence et le dernier
  tick strictement antérieur à la borne de fin devient le prix terminal. Les
  fenêtres partielles DOIVENT être exclues des métriques de calibration et
  rester comptabilisées dans le rapport.
- **FR-019** : Pour chaque fenêtre complète, le replay DOIT calculer l'issue
  terminale `UP` si le prix de fin est strictement supérieur au prix de
  référence, `DOWN` s'il lui est inférieur, et `TIE` en cas d'égalité. Les
  `TIE` sont comptabilisés mais exclus des métriques binaires.
- **FR-020** : Le replay DOIT produire une sortie contenant au minimum le
  timestamp, la fenêtre, le prix de référence, le prix courant, `prob_up`,
  `prob_down`, le modèle, la volatilité, le temps restant et le statut de la
  fenêtre.
- **FR-021** : Le rapport de replay DOIT calculer au minimum le nombre de
  fenêtres complètes et partielles, le Brier score, le log loss et une table de
  fiabilité par intervalle de probabilité.
- **FR-022** : Le replay DOIT être déterministe à fichier d'entrée et paramètres
  identiques, et NE DOIT effectuer aucune écriture réseau.
- **FR-023** : Le backtest DOIT accepter une archive couvrant une journée
  civile UTC et évaluer les 288 fenêtres de cinq minutes attendues, en
  signalant la date UTC, les bornes couvertes et toute fenêtre manquante.
- **FR-024** : Le workflow de backtest DOIT identifier explicitement la source
  utilisée (`agg_trade` historique ou `book_ticker` live) et NE DOIT PAS mélanger
  silencieusement leurs observations dans une même évaluation.
- **FR-025** : Le replay DOIT calculer les métriques de calibration avec au plus
  une prédiction par fenêtre complète. La prédiction de référence DOIT être la
  dernière `FairValue` dont le timestamp est inférieur ou égal à `fin - 60s`.
  Une fenêtre complète sans observation avant cette échéance est exclue du
  scoring et comptabilisée séparément.
- **FR-026** : L'estimation de volatilité utilisée par le mode historique
  `agg_trade` DOIT agréger les prix sur une grille temporelle régulière avant
  de calculer les log-returns et l'EWMA. La granularité DOIT être configurable
  et sa valeur par défaut DOIT être d'une seconde. Chaque intervalle contenant
  plusieurs trades DOIT utiliser une règle déterministe de prix représentatif,
  définie dans le rapport ; la règle v1 est le dernier prix observé dans
  l'intervalle. Le rapport DOIT identifier la granularité et la règle utilisées.
- **FR-027** : Le système DOIT pouvoir appliquer une borne configurable aux
  probabilités publiées afin d'éviter les valeurs extrêmes injustifiées. La
  borne v1 DOIT être appliquée après le calcul brut de l'Oracle et avant toute
  métrique ou comparaison économique. La configuration par défaut DOIT être
  `[0.05, 0.95]` pour `prob_up`, avec `prob_down = 1 - prob_up`. Le système
  DOIT conserver la probabilité brute et la probabilité bornée, ou rendre leur
  différence reconstructible dans le rapport.
- **FR-028** : La calibration des probabilités DOIT être entraînée uniquement
  sur des observations historiques antérieures et indépendantes de la période
  évaluée. Une méthode de calibration ne DOIT PAS être ajustée sur les mêmes
  fenêtres que celles utilisées pour son score final.
- **FR-029** : Le rapport DOIT comparer le modèle calibré à au moins une
  baseline naïve, notamment la probabilité constante de `50 %` et la fréquence
  empirique `UP` de l'échantillon d'entraînement.
- **FR-030** : La validation DOIT pouvoir couvrir plusieurs journées UTC et
  produire des métriques séparées par journée ainsi qu'une agrégation globale.
  Le rapport DOIT indiquer la période, le nombre de fenêtres et la couverture
  de chaque journée.
- **FR-031** : Le rapport DOIT distinguer la qualité directionnelle, mesurée
  par la décision `prob_up >= 0.5`, de la qualité probabiliste, mesurée au
  minimum par Brier score, log loss et fiabilité par intervalle.
- **FR-032** : Le système NE DOIT PAS présenter une amélioration de calibration
  comme une preuve de rentabilité. L'évaluation économique DOIT rester séparée
  et nécessiter les prix exécutables Polymarket, les frais, le slippage et le
  risque de non-exécution.
- **FR-033** : Le pricing DOIT représenter séparément la mise en dollars `Q`,
  le prix par part `p`, le nombre de parts, le paiement gagnant théorique et
  le rendement conditionnel `A`. Pour une part binaire gagnante à `$1`, il
  DOIT appliquer `N = Q / p` et `A = (1 - p) / p` avant frais.
- **FR-034** : Le système DOIT obtenir `p` depuis le prix limite ou le prix
  moyen d'exécution correspondant au mode de cotation ; `A` NE DOIT PAS être
  configuré comme une constante indépendante du prix du marché.
- **FR-035** : Le sizing DOIT être évalué après l'edge net et DOIT retourner
  `Q = 0` lorsque l'opportunité est rejetée, la profondeur est insuffisante,
  le prix est invalide ou un kill switch est actif. Il NE DOIT dépasser ni la
  mise maximale par marché, ni le capital disponible, ni le notionnel
  exécutable.
- **FR-036** : Le système DOIT supporter une baseline à mise fixe et un sizing
  dynamique dépendant de la probabilité Oracle et de l'edge. Le choix du mode,
  la quantité calculée et chaque plafond appliqué DOIVENT être auditables.
- **FR-037** : Le replay DOIT pouvoir produire une simulation P/L par fenêtre
  lorsqu'un prix d'entrée Polymarket synthétique, une mise `Q` et les frais
  sont fournis. Cette simulation DOIT être séparée des métriques Brier, log
  loss et calibration.
- **FR-038** : La sortie P/L DOIT contenir au minimum le côté choisi, l'issue,
  le prix d'entrée, `Q`, le nombre de parts, le payout, les frais, le P/L de
  la fenêtre et le P/L cumulé. Le rapport DOIT fournir au minimum le nombre de
  paris, les gains, les pertes, la mise totale, le payout total, le P/L total
  et le drawdown maximal.
- **FR-039** : Le replay NE DOIT PAS présenter cette simulation comme une
  rentabilité historique Polymarket réelle : les données Binance `aggTrades`
  ne contiennent ni prix d'entrée Polymarket, ni carnet, ni fill historique.
- **FR-040** : Le replay DOIT pouvoir découvrir le marché Polymarket et les
  tokens `UP` et `DOWN` associés à chaque fenêtre via l'API de découverte
  configurée.
- **FR-041** : Le replay DOIT pouvoir récupérer l'historique de prix des deux
  tokens via l'API Polymarket et sélectionner, pour chacun, le dernier point
  dont `timestamp <= window_end - 60s`.
- **FR-042** : Le replay DOIT rejeter les points postérieurs à l'échéance et
  appliquer une fraîcheur maximale configurable aux points antérieurs. Il DOIT
  conserver la source, le timestamp et l'écart à l'échéance.
- **FR-043** : L'absence d'un marché, d'un token ou d'un prix Polymarket valide
  DOIT exclure la fenêtre des métriques économiques uniquement. Le rapport
  DOIT exposer séparément `oracle_scored_windows` et
  `economic_priced_windows`.
- **FR-044** : Les appels API Polymarket DOIVENT être limités en débit,
  paginés si nécessaire, réessayables selon une politique bornée et
  enregistrer les erreurs HTTP, JSON, réponses vides et correspondances
  ambiguës.
- **FR-045** : Le système NE DOIT acheter l'issue `UP` que si `prob_up > 0.5`
  et que le prix exécutable `UP` est strictement inférieur à `prob_up` ; la
  règle symétrique s'applique à `DOWN` avec `prob_down` et son propre prix
  exécutable. Si aucune des deux issues ne remplit sa propre condition, le
  système NE DOIT simuler ni proposer aucun pari pour cette fenêtre, même si
  l'une des deux issues affiche un prix exécutable plus bas que l'autre.
- **FR-046** : Le système DOIT calculer les frais Polymarket selon le type
  d'exécution réel : `0` pour un ordre qui reste maker (ne croise pas
  immédiatement le carnet au moment de sa soumission), et le taux de la
  catégorie Crypto (`0.07 %` du montant exécuté) pour un ordre taker, y
  compris un ordre initialement passif qui croiserait immédiatement le
  carnet. Le système NE DOIT PAS appliquer un taux ou montant de frais
  constant indépendant du mode d'exécution effectif.
- **FR-047** : Le sizing dynamique v1 DOIT utiliser les valeurs par défaut
  suivantes, sauf configuration explicite : `kelly_fraction_cap = 0.5`,
  `max_stake_per_market = 20 $`, `min_stake_usd = 1 $`. Le plafond de
  profondeur (`executable_notional`) PEUT être ignoré tant que le montant par
  pari reste dans cet ordre de grandeur et que `pricing/edge.py` n'expose pas
  encore de profondeur de carnet réelle.

### Entités principales

- **Tick** : observation normalisée d'un prix BTC, comprenant `ts_ns`, `price`,
  `qty`, `source` et `seq`.
- **État de connexion** : état observable du transport, comprenant la connexion,
  le dernier heartbeat, le dernier message, les tentatives et la cause de la
  dernière interruption.
- **Métrique de flux** : mesure de santé du flux, comprenant la latence, l'âge
  du dernier tick, les ruptures de séquence, les rejets et la pression de file.
- **Évaluation Oracle** : observation d'une `FairValue` enrichie du statut de
  fenêtre (`complete`, `partial` ou `excluded`), de l'issue terminale lorsqu'elle
  est disponible et du motif d'exclusion éventuel.
- **Évaluation de mise** : résultat déterministe du sizing comprenant `Q`, le
  prix par part, le nombre de parts, `A`, le paiement gagnant théorique, le
  mode de sizing et les plafonds appliqués. Cette évaluation ne constitue pas
  un ordre.
- **Prix Polymarket historique** : association d'une fenêtre à un marché,
  aux tokens `UP` et `DOWN`, aux prix retenus à `T-60s`, à leurs timestamps, à
  leur fraîcheur et à un motif d'absence éventuel. Cette entité ne constitue
  pas un snapshot complet du carnet.
- **Rapport de replay** : agrégats déterministes du replay comprenant les
  compteurs de lignes, ticks acceptés, sources ignorées, lignes rejetées,
  fenêtres complètes ou partielles, métriques de calibration et, si activé,
  métriques P/L de la simulation économique paramétrée.

## Critères de succès

### Résultats mesurables

- **SC-001** : Sur une session réseau saine d'au moins 10 minutes, au moins
  99,9 % des messages valides reçus sont transformés en ticks normalisés ou
  associés à une erreur explicite.
- **SC-002** : Après une interruption simulée, le flux reprend automatiquement
  dans un délai inférieur ou égal à 30 secondes lorsque le service redevient
  disponible.
- **SC-003** : Sur une séquence synthétique contenant des doublons et une
  rupture, 100 % des anomalies sont détectées et signalées dans les tests.
- **SC-004** : La file de transmission reste dans sa capacité configurée pendant
  un test de charge où le consommateur est plus lent que le producteur.
- **SC-005** : Chaque tick accepté possède un timestamp UTC en nanosecondes,
  un prix strictement positif et une source identifiable.
- **SC-006** : Une revue du code confirme l'absence d'appel d'envoi d'ordre,
  de signature de transaction et d'écriture réseau.
- **SC-007** : Sur un fichier de replay synthétique contenant des fenêtres
  complètes, partielles, des sources ignorées et une ligne malformée, 100 % des
  cas sont classés dans le rapport sans arrêt silencieux.
- **SC-008** : Deux exécutions du replay avec le même fichier et les mêmes
  paramètres produisent des sorties identiques, à représentation numérique
  identique.
- **SC-009** : Les métriques Brier score, log loss et fiabilité sont calculées
  uniquement sur les fenêtres complètes et sont vérifiables sur un jeu de
  données synthétique dont les issues sont connues.
- **SC-010** : Sur une archive UTC complète et continue d'une journée, le
  backtest produit 288 fenêtres alignées de cinq minutes et distingue
  explicitement les fenêtres complètes des fenêtres incomplètes.
- **SC-011** : Sur un jeu synthétique dont les issues sont connues, le replay
  sélectionne exactement une prédiction par fenêtre à l'offset configuré et
  rejette toute prédiction postérieure à cet offset.
- **SC-012** : Sur un jeu synthétique contenant des trades irréguliers, le
  replay produit une volatilité à partir de la grille temporelle configurée et
  expose cette granularité dans le rapport.
- **SC-013** : Une probabilité calculée en dehors des bornes configurées est
  bornée avant scoring, et les bornes utilisées sont présentes dans le rapport.
- **SC-014** : Une calibration entraînée sur une période antérieure ne lit
  aucune issue de la période évaluée avant de produire les probabilités
  évaluées.
- **SC-015** : Le rapport compare le modèle aux baselines `50 %` et fréquence
  empirique, avec des métriques calculées sur exactement le même ensemble de
  fenêtres scorées.
- **SC-016** : Sur au moins deux journées, le rapport produit des métriques par
  journée et une agrégation globale sans mélanger les sources ni les périodes.
- **SC-017** : Le rapport indique explicitement que ses métriques Oracle ne
  contiennent ni frais, ni slippage, ni exécution, ni rentabilité Polymarket.
- **SC-018** : Sur un jeu de réponses Gamma/CLOB enregistrées contenant des
  prix antérieurs et postérieurs à `T-60s`, le replay ne retient jamais un prix
  postérieur, expose `oracle_scored_windows` et `economic_priced_windows`
  séparément, et exclut du P/L toute fenêtre sans prix Polymarket valide sans
  retirer sa `FairValue` du score Oracle.

## Hypothèses

- La v1 utilise Binance comme proxy CEX de l'oracle de résolution ; la fidélité
  de ce proxy sera mesurée séparément et ne constitue pas une hypothèse validée.
- Le symbole BTC/USDT et les flux publics Binance restent disponibles depuis
  l'environnement Mac local.
- Le lecteur ne décide d'aucune opportunité, ne calcule aucune juste valeur et
  ne persiste pas directement les fichiers Parquet ; ces responsabilités restent
  dans les composants prévus par l'architecture du projet.
- Les règles de résolution Polymarket restent indéterminées jusqu'à la Phase 0
  et ne sont pas codées dans cette fonctionnalité.
- Les paramètres de heartbeat, de reconnexion, de capacité de file et de délai
  d'inactivité sont configurables et seront calibrés par les mesures de Phase 0.
- Le CSV de replay contient au minimum les colonnes documentées dans
  [data-model.md](data-model.md), avec des timestamps en nanosecondes et des
  prix positifs.
- La résolution v1 est considérée comme terminale pour l'évaluation de
  l'Oracle ; si Polymarket est finalement confirmé comme utilisant un TWAP,
  cette fonctionnalité devra être révisée avant toute conclusion de calibration.

## Organisation des responsabilités

- `price_collection` récupère uniquement `bookTicker`, puis effectue le parsing,
  la normalisation et la publication des `Tick` dans une file bornée.
- `oracle` est distinct de la collecte. Il mémorise la référence du début de
  chaque fenêtre de 5 minutes, suit le prix courant, la volatilité et le temps
  restant, puis produit une `FairValue` avec les probabilités `UP/DOWN`.
- `pricing` compare la `FairValue` au carnet Polymarket, calcule l'edge net et
  décide si une opportunité est retenue.
- Les scripts Python DOIVENT privilégier la solution la plus simple qui respecte
  les contrats, la testabilité et les métriques ; toute abstraction ou
  dépendance supplémentaire doit avoir une nécessité démontrée.
- `replay` lit les données locales et orchestre l'Oracle, mais ne modifie ni le
  collecteur ni les calculs purs de `oracle/`.
