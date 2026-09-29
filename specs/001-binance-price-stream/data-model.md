# Modèle de données : flux Binance

## Tick

Observation normalisée consommée par le reste du système.

| Champ | Type | Contraintes |
|---|---|---|
| `ts_ns` | entier 64 bits | timestamp UTC nanoseconde produit par l'horloge corrigée |
| `price` | décimal ou flottant validé | strictement positif |
| `qty` | décimal ou flottant validé | non négatif ; quantité du trade ou quantité représentative du meilleur niveau |
| `source` | chaîne | `binance.book_ticker` en live ou `binance.agg_trade` en replay historique ; les sources non sélectionnées sont ignorées |
| `seq` | entier nullable | identifiant de séquence de la source, conservé sans conversion lossy |
| `exchange_ts_ns` | entier nullable | timestamp source Binance converti en nanosecondes |
| `received_ts_ns` | entier | timestamp local de réception avant publication |

Les champs `ts_ns`, `received_ts_ns` et `exchange_ts_ns` sont toujours en
nanosecondes UTC. La différence entre les timestamps source et locaux sert à
mesurer la latence ; elle ne doit pas modifier l'ordre de consommation.

## État de connexion

| Champ | Type | Description |
|---|---|---|
| `connected` | booléen | connexion considérée saine ou non |
| `last_message_ts_ns` | entier nullable | dernier message reçu |
| `last_heartbeat_ts_ns` | entier nullable | dernier heartbeat observé ou envoyé |
| `reconnect_attempts` | entier | nombre d'essais depuis la dernière connexion saine |
| `last_error` | chaîne nullable | cause de la dernière interruption ou du dernier rejet |
| `sequence_gap_detected` | booléen | indique une anomalie non résolue |

## Métriques de flux

| Métrique | Description |
|---|---|
| `message_count` | nombre de messages reçus |
| `tick_count` | nombre de ticks publiés |
| `rejected_count` | nombre de messages invalides ou rejetés |
| `sequence_gap_count` | nombre de gaps détectés par flux |
| `reconnect_count` | nombre de reconnexions réussies |
| `queue_depth` | occupation courante de la file bornée |
| `queue_overflow_count` | nombre de saturations de file |
| `last_tick_age_ms` | âge du dernier tick au moment de la mesure |
| `latency_ms` | latence entre timestamp source et réception locale |

## FairValue

Valeur probabiliste produite par l'Oracle pour une fenêtre de marché de cinq
minutes. Elle est indépendante du carnet Polymarket et sert d'entrée au module
`pricing`.

| Champ | Type | Description |
|---|---|---|
| `ts_ns` | entier 64 bits | instant du calcul |
| `window_start_ns` | entier 64 bits | début de la fenêtre de cinq minutes |
| `reference_price` | nombre positif | prix à battre mémorisé au début de la fenêtre |
| `current_price` | nombre positif | dernier midpoint `bookTicker` accepté |
| `prob_up` | flottant | probabilité conditionnelle que le prix de résolution terminale soit au-dessus de `reference_price`, entre 0 et 1 |
| `prob_down` | flottant | complément de la probabilité de résolution terminale `UP`, entre 0 et 1 |
| `model` | chaîne | modèle utilisé, par exemple `terminal_lognormal_ewma` |
| `volatility` | flottant positif | volatilité utilisée par le modèle |
| `remaining_ns` | entier 64 bits | temps restant dans la fenêtre |

La v1 utilise une baseline lognormale sans drift estimé à très court horizon :
`prob_up = Phi(log(current_price / reference_price) / (volatility * sqrt(tau)))`,
avec `tau` exprimé en années et `volatility` annualisée. Les probabilités
doivent respecter `prob_up + prob_down = 1` à la précision numérique définie.
Cette probabilité concerne l'état terminal, pas un franchissement temporaire de
la référence pendant la fenêtre. Le module `pricing` compare cette valeur aux
prix du carnet Polymarket ; il ne recalcule pas la probabilité.

## Ligne de replay CSV

Le replay accepte les colonnes produites par `scripts/collect_binance_csv.py` :

| Colonne | Type | Règle |
|---|---|---|
| `ts_ns` | entier | obligatoire, strictement positif et non décroissant dans le fichier |
| `exchange_ts_ns` | entier nullable | timestamp Binance en nanosecondes |
| `received_ts_ns` | entier nullable | timestamp local de réception |
| `price` | nombre | obligatoire et strictement positif |
| `qty` | nombre | obligatoire et non négatif |
| `source` | chaîne | obligatoire ; `binance.book_ticker` en live ou `binance.agg_trade` en historique |
| `seq` | entier nullable | séquence conservée telle quelle |

Une ligne malformée est rejetée avec son numéro de ligne. Une source ignorée
est comptabilisée séparément d'une ligne malformée. En mode historique, le
timestamp d'exécution Binance devient `ts_ns` et `received_ts_ns` reste nul ou
absent ; en mode live, `ts_ns` reste le timestamp local de réception.

## Évaluation d'une fenêtre

| Champ | Valeur |
|---|---|
| `window_status` | `complete`, `partial` ou `excluded` |
| `outcome` | `UP`, `DOWN`, `TIE` ou nul si non disponible |
| `reference_observation_ts_ns` | premier timestamp de la source observé dans la fenêtre |
| `outcome_price` | dernier prix observable strictement avant la fin de la fenêtre complète |
| `exclusion_reason` | motif renseigné pour une fenêtre non évaluable |

Le rapport de calibration contient également `scored_windows` et
`prediction_offset_ns`. Pour la v1, `prediction_offset_ns` vaut 60 secondes.
Une seule prédiction par fenêtre est retenue : la dernière observation dont le
timestamp est inférieur ou égal à `window_end - prediction_offset_ns`.

Le rapport DOIT également pouvoir exposer les paramètres qui influencent la
confiance du modèle :

| Champ | Valeur |
|---|---|
| `volatility_sampling_interval_ns` | granularité temporelle de l'échantillonnage, si activée |
| `volatility_sampling_rule` | règle de prix représentatif ; par défaut `last_trade_per_interval`, soit le dernier prix observé dans chaque intervalle |
| `probability_floor` | borne basse appliquée à `prob_up` |
| `probability_ceiling` | borne haute appliquée à `prob_up` |
| `prob_up_raw` | probabilité produite avant bornage |
| `calibration_method` | `none`, `sigmoid` ou `isotonic` |
| `calibration_train_period` | période historique utilisée pour ajuster la calibration |
| `evaluation_period` | période distincte utilisée pour mesurer les scores |
| `directional_accuracy` | proportion des décisions `prob_up >= 0.5` correctes |
| `baseline_brier_50pct` | Brier score de la prédiction constante à 50 % |
| `baseline_log_loss_50pct` | log loss de la prédiction constante à 50 % |

Lorsque la simulation économique est activée, chaque prédiction retenue peut
également exposer :

| Champ | Valeur |
|---|---|
| `pnl_side` | issue choisie par l'Oracle, `UP` ou `DOWN` |
| `entry_price` | prix Polymarket synthétique utilisé pour la simulation |
| `stake_usd` | mise engagée `Q` en dollars |
| `shares` | `stake_usd / entry_price` |
| `fees_usd` | frais simulés |
| `payout_usd` | paiement brut si l'issue est gagnante |
| `pnl_usd` | résultat de la fenêtre après frais |
| `cumulative_pnl_usd` | résultat cumulé dans l'ordre des fenêtres |

Un pari n'est simulé que si l'issue retenue est qualifiée directionnellement :
`prob_up > 0.5` et `entry_price(UP) < prob_up`, ou symétriquement pour `DOWN`.
Si aucune issue ne remplit sa propre condition, `pnl_side` conserve l'issue
favorite de l'Oracle à titre diagnostique, mais `entry_price`, `stake_usd`,
`shares`, `fees_usd`, `payout_usd` et `pnl_usd` restent nuls et
`economic_status` vaut `edge_below_threshold`.

Pour une fenêtre enrichie par l'API Polymarket, les champs suivants sont
également requis :

| Champ | Valeur |
|---|---|
| `market_id` | identifiant Polymarket découvert pour la fenêtre |
| `up_token_id` / `down_token_id` | identifiants des deux issues binaires |
| `up_price` / `down_price` | dernier prix historique retenu à `T-60s` ou avant |
| `up_price_ts_ns` / `down_price_ts_ns` | timestamps des prix retenus |
| `up_price_age_ns` / `down_price_age_ns` | écart entre la décision et chaque prix |
| `polymarket_price_source` | type de prix et endpoint/cache utilisé |
| `economic_status` | `priced`, `missing_market`, `missing_token`, `missing_price`, `stale_price` ou `api_error` |

Le rapport distingue `oracle_scored_windows` de
`economic_priced_windows`. Un statut économique non tarifé ne rend pas la
fenêtre Oracle invalide.

Cette simulation est paramétrée et ne constitue pas un historique de prix ou
de fills Polymarket.

Par défaut, une fenêtre UTC de cinq minutes est complète si elle contient au
moins un tick de la source sélectionnée à partir de sa borne de début et au
moins un tick strictement avant sa borne de fin. Le premier tick de la fenêtre
est la référence et le dernier tick de la fenêtre est l'observation terminale.
Une archive journalière `aggTrades` attendue contient 288 fenêtres alignées ;
les fenêtres partielles ou absentes ne sont pas incluses dans Brier score, log
loss ou la table de fiabilité. Les métriques restent étiquetées par source.

## Règles de validation

- Un prix nul, négatif, non numérique ou absent est rejeté.
- Une quantité négative est rejetée.
- Un message sans identifiant de flux connu est rejeté.
- Un doublon ou un recul de séquence est signalé ; il ne doit pas être présenté
  comme une observation valide sans décision explicite du consommateur.
- Une rupture de séquence ne déclenche pas de resynchronisation implicite dans
  ce lecteur ; elle est exposée au collecteur pour traitement ultérieur.
- La capacité de la file est configurable et ne peut pas être infinie.
