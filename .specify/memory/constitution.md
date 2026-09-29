<!--
Sync Impact Report
- Changement de version : 1.0.0 -> 1.1.0
- Principes modifiés : ajout d'une exigence de simplicité et séparation collecte/Oracle
- Sections ajoutées : Contraintes de sécurité et d'exploitation ; Développement et portes de qualité
- Sections supprimées : aucune
- Modèles nécessitant une mise à jour : aucun ; les modèles existants restent compatibles
- Actions de suivi : aucune
-->

# Constitution d'Arbitrage Poly

## Principes fondamentaux

### I. Paper trading uniquement
La version 1 DOIT rester un système d'observation et de paper trading. Elle NE
DOIT envoyer aucun ordre, signer aucune transaction, utiliser aucune clé privée,
gérer aucune allowance, ni effectuer aucune autre opération d'écriture vers
Polymarket ou une blockchain. La couche d'exécution DOIT simuler les exécutions
FAK à partir de la profondeur publique du carnet, enregistrée ou en direct, en
intégrant la latence et le slippage. Cette limite protège le capital pendant
que l'existence et la qualité de l'edge sont établies.

### II. Les faits avant les hypothèses
Le système DOIT considérer les métadonnées de marché en direct comme la
référence pour les règles de résolution, la source de l'oracle, la cadence des
marchés, les frais, le tick size, la taille minimale d'ordre et le comportement
de l'API. Aucun modèle de pricing NE DOIT être choisi à partir d'une affirmation
non vérifiée du document source. Les sondes de la Phase 0 DOIVENT archiver les
preuves brutes, indiquer si chaque fait est confirmé ou infirmé, et interrompre
le développement du pricing jusqu'à détermination de la règle de résolution.
Un NO-GO documenté constitue un résultat valide.

### III. Logique métier pure et sémantique d'exécution partagée
Le pricing, le calcul de l'edge, le calcul du TWAP, l'estimation de la
volatilité, le sizing, les décisions de risque et les exécutions simulées DOIVENT
être des opérations métier déterministes et sans I/O. L'observation en direct
et le replay DOIVENT appeler le même code de pricing, d'edge et d'exécution
simulée afin que le replay reproduise exactement les opportunités observées.
Les simulations de Monte Carlo DOIVENT accepter une seed explicite.

### IV. Aucun changement silencieux de données ou de règles
Chaque timestamp DOIT être représenté en nanosecondes UTC et produit par
l'horloge monotone corrigée. Le système DOIT mesurer et exposer l'offset de
l'horloge, les trous de données de l'oracle, la connectivité WebSocket, la
latence et les trous de séquence. Toute modification du hash de description du
marché DOIT déclencher un avertissement et un kill switch. Les opportunités
rejetées DOIVENT être persistées avec leur motif de rejet, et non supprimées.

### V. Risque et périmètre conservateurs
Pendant la version 1, le système DOIT cibler uniquement les marchés BTC
up/down à la cadence vérifiée de cinq minutes, avec une taille maximale simulée
de 20 USD par tentative. Une opportunité DOIT être rejetée lorsque l'horizon
résiduel est inférieur au budget de latence mesuré, lorsque l'edge net ne
dépasse pas les seuils configurés après prise en compte des frais et de la
profondeur, ou lorsqu'un kill switch est actif. La version 1 DOIT fonctionner
sur l'environnement Mac local ; le déploiement sur VPS, le hedging,
l'exécution réelle et les marchés non supportés sont hors périmètre.

### VI. Contrats explicites et dépendances minimales
Les modèles et les schémas Parquet DOIVENT être explicites, typés et stables
entre la collecte en direct et le replay. Le stockage DOIT utiliser des fichiers
Parquet horodatés, avec des schémas PyArrow explicites, une compression zstd,
un partitionnement horaire, des écritures bufferisées et un flush garanti à
l'arrêt. Les dépendances DOIVENT être pinnées lorsque la compatibilité avec une
API beta est en jeu ; `py-clob-client` NE DOIT PAS être introduit.

### VII. Simplicité des scripts Python

Les scripts Python DOIVENT privilégier la solution la plus simple qui respecte
les contrats, la testabilité et les exigences de mesure. Une abstraction, une
dépendance ou une couche supplémentaire NE DOIT être ajoutée que si elle
supprime une complexité réelle ou protège une frontière d'architecture
nécessaire. La collecte des prix et l'Oracle DOIVENT rester dans des modules
distincts : `price_collection` gère le transport et la normalisation Binance ;
`oracle` consomme les ticks normalisés et porte la logique Oracle.

## Contraintes de sécurité et d'exploitation

Aucune clé privée, aucun identifiant de wallet et aucun secret NE DOIT être
commité ou requis par l'observateur de la version 1. Les endpoints publics de
Polymarket et Binance sont des entrées en lecture seule. Les clients réseau
DOIVENT gérer les reconnexions bornées, des limites de débit conservatrices,
les heartbeats et la détection des trous de séquence. Toute capacité future
d'envoi d'ordres réels nécessite un amendement distinct de la constitution et
un périmètre de version explicite ; elle NE DOIT PAS être introduite
subrepticement dans le chemin de paper trading.

## Développement et portes de qualité

L'implémentation DOIT suivre les dépendances entre phases de `plan.md` : reality
check empirique, puis scaffolding et readers, stockage, pricing, paper trading,
et enfin replay et évaluation. Toute modification du pricing, des schémas, de
la reconstruction du carnet ou des contrôles de risque DOIT inclure des tests
ciblés. Le projet n'est pas prêt à dépasser le stade de l'observation tant que
les éléments suivants ne sont pas mesurés et documentés : règle de résolution,
basis du proxy, calibration du modèle, edge net de frais/profondeur/latence et
non-divergence entre le direct et le replay.

La porte de qualité minimale comprend une suite de tests réussie couvrant le
TWAP, le pricing et la reconstruction du carnet ; une exécution stable du
collecteur pendant 24 heures ; la parité exacte du replay ; un Brier score
évalué sur au moins 200 marchés ; et une analyse du code prouvant l'absence
d'appels de placement d'ordres et de `py-clob-client`.

## Gouvernance

Cette constitution régit les décisions d'implémentation du projet. En cas de
conflit avec `arbitrage_poly.md`, `specifications.md` prévaut comme source
factuelle et comportementale, tandis que cette constitution définit les
contraintes d'ingénierie non négociables. `plan.md` est la checklist d'exécution
et DOIT rester cohérent avec les deux autres documents.

Tout amendement DOIT préciser sa raison, les principes concernés, son impact
sur la compatibilité et les travaux de migration ou de validation nécessaires.
Le versionnage suit l'intention sémantique : MAJOR pour supprimer ou redéfinir
un principe, MINOR pour ajouter un principe ou une contrainte substantielle, et
PATCH pour les clarifications qui ne modifient pas le comportement. La version
et la date d'amendement DOIVENT être mises à jour pour chaque amendement accepté.
Toute revue d'implémentation DOIT vérifier la conformité aux principes et
consigner toute exception approuvée avec un responsable et une condition
d'expiration.

**Version**: 1.1.0 | **Ratified**: 2026-09-18 | **Last Amended**: 2026-09-18
