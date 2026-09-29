# Spécification de fonctionnalité : Exclusion des tokens à prix trop incertain

**Feature Branch**: `003-exclude-uncertain-price-band`
**Created**: 2026-09-21
**Status**: Validé
**Input**: User description: "Dans la décision d'achat de token on élimine les tokens entre 0.45 et 0.55 pour éviter les situations trop incertaines."

## Scénarios utilisateur et tests

### User Story 1 - Ne jamais acheter un token proche d'un pile ou face (Priorité : P1)

En tant que mainteneur de la stratégie de backtest, je veux que la décision
d'achat d'un token rejette systématiquement les tokens dont le prix de marché
est trop proche de 0,50, afin de ne pas engager de capital sur une situation
que le marché considère lui-même comme quasiment indécidable, même lorsque
l'edge calculé par l'Oracle serait techniquement suffisant.

**Pourquoi cette priorité** : un edge positif calculé contre un prix proche de
0,50 repose sur une confiance forte dans l'Oracle et une confiance quasi nulle
du marché ; ce sont précisément les situations les plus susceptibles de
refléter du bruit plutôt qu'un véritable désaccord informé.

**Test indépendant** : rejouer un jeu de fenêtres dont certains prix de
marché tombent dans la bande exclue et vérifier qu'aucun pari n'est simulé
pour ces fenêtres, quel que soit l'edge Oracle par ailleurs, et que le motif
de rejet est explicite et distinct d'un rejet pour edge insuffisant.

**Scénarios d'acceptation** :

1. **Étant donné** un côté dont la probabilité Oracle dépasse 0,5 et dont
   l'edge sur le prix de marché dépasse le seuil minimum configuré, **quand**
   ce prix de marché se situe entre 0,45 et 0,55 inclus, **alors** aucun pari
   n'est placé pour cette fenêtre et le statut économique rapporté indique
   explicitement l'exclusion pour incertitude de prix.
2. **Étant donné** un côté dont le prix de marché se situe en dehors de la
   bande exclue et qui satisfait par ailleurs les conditions existantes
   (probabilité > 0,5 et edge minimum), **quand** la fenêtre est évaluée,
   **alors** le pari est placé normalement, sans changement de comportement.
3. **Étant donné** une fenêtre sans prix de marché exploitable (statut
   différent de « priced »), **quand** elle est évaluée, **alors** le
   nouveau filtre n'intervient pas et le statut économique existant
   (`missing_market`, `api_error`, etc.) reste inchangé.

### Edge Cases

- Un prix de marché exactement égal à 0,45 ou 0,55 doit être exclu (bornes
  incluses), pour éviter une ambiguïté sur les valeurs limites.
- Le filtre s'applique indépendamment du côté (UP ou DOWN) et indépendamment
  du mode de mise (mise fixe ou sizing dynamique) : il intervient avant tout
  calcul de mise.
- Le filtre doit rester configurable (bornes ajustables) sans changer le
  comportement par défaut des appels existants qui ne le configurent pas
  explicitement.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système DOIT exclure de la décision d'achat tout côté dont
  le prix de marché exécutable se situe entre 0,45 et 0,55 inclus, même si ce
  côté satisferait par ailleurs les conditions existantes de probabilité et
  d'edge minimum.
- **FR-002**: Le système DOIT distinguer, dans le statut économique rapporté,
  un rejet pour prix trop incertain d'un rejet pour edge insuffisant.
- **FR-003**: Les bornes de la bande d'exclusion DOIVENT être configurables,
  avec 0,45 et 0,55 comme valeurs par défaut.
- **FR-004**: Le filtre NE DOIT PAS modifier le comportement des fenêtres déjà
  exclues pour une autre raison (marché ou prix manquant, par exemple).
- **FR-005**: Dans le backtest Oracle lorsque le prix historique exécutable
  du token n'est pas disponible et que le prix d'entrée synthétique est la
  fair value Oracle du côté retenu, le filtre DOIT aussi s'appliquer à cette
  fair value synthétique. Cette adaptation DOIT être identifiée comme telle
  et NE DOIT PAS être présentée comme un prix historique Polymarket.
- **FR-006**: L'exclusion par bande de prix DOIT supprimer uniquement le pari
  simulé; elle NE DOIT PAS supprimer la prévision des métriques de scoring
  Oracle. Son motif DOIT être rapporté séparément des exclusions de scoring.

### Key Entities

- **Bande de prix incertaine** : intervalle `[borne basse, borne haute]` du
  prix de marché d'un côté, en dehors duquel un pari peut être considéré ;
  toute valeur à l'intérieur de cet intervalle est traitée comme trop proche
  d'un pile ou face pour être exploitée, indépendamment de l'edge Oracle.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Aucune fenêtre dont le prix de marché du côté retenu se situe
  entre 0,45 et 0,55 inclus n'apparaît avec un pari simulé (`pnl_usd` non
  nul) dans la sortie du backtest.
- **SC-002**: Le motif de rejet des fenêtres exclues par ce filtre est
  identifiable séparément des autres motifs de rejet existants dans le
  rapport de backtest.

## Assumptions

- La bande `[0,45, 0,55]` s'applique au prix de marché du côté évalué (pas à
  la probabilité Oracle), conformément à la formulation de la demande.
- Lorsque le prix d'entrée du backtest est synthétique faute de prix de token,
  la bande s'applique au prix synthétique du côté acheté; cela ne remplace pas
  l'application au vrai prix exécutable lorsque celui-ci sera disponible.
- Cette exclusion s'ajoute au garde-fou directionnel et au seuil d'edge
  minimum déjà existants ; elle ne les remplace pas.
