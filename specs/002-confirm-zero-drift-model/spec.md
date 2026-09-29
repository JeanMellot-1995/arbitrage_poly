# Spécification de fonctionnalité : Confirmation du modèle Oracle à dérive nulle

**Feature Branch**: `002-confirm-zero-drift-model`
**Created**: 2026-09-21
**Status**: Validé
**Input**: User description: "/speckit.specify gardons l'hypothèse de dérive nulle et mettons à jour les fichiers speckit en ce sens"

## Scénarios utilisateur et tests

### User Story 1 - Conserver un modèle Oracle par défaut validé empiriquement (Priorité : P1)

En tant que mainteneur de l'Oracle, je veux que le modèle de probabilité par
défaut (`terminal_lognormal_ewma`, dérive nulle) ne soit remplacé par une
alternative que si celle-ci démontre une meilleure calibration mesurée, afin
de ne jamais dégrader silencieusement la fiabilité des prédictions produites
en production ou en backtest.

**Pourquoi cette priorité** : le modèle Oracle alimente à la fois le scoring
(Brier score, log loss) et la décision de pari économique ; un changement de
modèle non validé se répercute directement sur la rentabilité simulée et
réelle.

**Test indépendant** : comparer, sur un même jeu de fenêtres historiques, le
Brier score et le log loss du modèle par défaut à ceux d'une alternative
candidate, et vérifier que l'alternative n'est retenue que si elle améliore
ces deux métriques de façon reproductible.

**Scénarios d'acceptation** :

1. **Étant donné** le modèle par défaut à dérive nulle, **quand** une
   alternative (par exemple un retour à la moyenne) est évaluée sur le même
   jeu de fenêtres, **alors** le système permet de comparer Brier score, log
   loss et table de fiabilité entre les deux sans modifier le comportement
   par défaut.
2. **Étant donné** une alternative qui dégrade le Brier score ou le log loss
   par rapport à la baseline, **quand** l'évaluation est terminée, **alors**
   le modèle par défaut reste inchangé et la décision est consignée dans le
   journal de décisions du projet.
3. **Étant donné** une alternative qui améliore légèrement les métriques,
   **quand** le gain est de faible ampleur (proche du bruit statistique sur
   l'échantillon disponible), **alors** le système ne l'active pas par défaut
   tant qu'une amélioration reproductible et significative n'est pas
   démontrée.

### Edge Cases

- Que se passe-t-il si une alternative améliore les métriques uniquement pour
  un réglage extrême de ses paramètres (par exemple une demi-vie de retour à
  la moyenne très courte) ? Le système doit permettre d'observer que ce
  réglage dégrade fortement la calibration plutôt que de l'accepter comme une
  amélioration ponctuelle.
- Que se passe-t-il si l'échantillon disponible est trop petit pour trancher
  entre deux modèles proches ? La décision documentée doit indiquer
  explicitement cette limite plutôt que de conclure de façon définitive.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Le système DOIT conserver le modèle log-normal à dérive nulle
  (`terminal_lognormal_ewma` / `terminal_lognormal_ewma_agg_trade`) comme
  modèle par défaut de calcul de `prob_up`/`prob_down`, en live comme en
  replay.
- **FR-002**: Le système DOIT permettre d'évaluer un modèle alternatif à
  retour à la moyenne via un paramètre explicite, désactivé par défaut, sans
  changer le comportement des appels existants qui ne le fournissent pas.
- **FR-003**: Toute activation d'un modèle alternatif en production DOIT être
  précédée d'une comparaison documentée de Brier score et de log loss contre
  la baseline, sur un échantillon multi-jours.
- **FR-004**: Le système DOIT documenter dans le journal de décisions
  (`research.md`) le résultat de toute évaluation de modèle alternatif à la
  probabilité Oracle, qu'elle soit positive ou négative.

### Key Entities

- **Modèle de probabilité Oracle** : fonction qui transforme prix courant,
  prix de référence, temps restant et volatilité en `prob_up`/`prob_down` ;
  le modèle par défaut n'a pas de paramètre de retour à la moyenne actif.
- **Évaluation de calibration** : ensemble de métriques (Brier score, log
  loss, table de fiabilité par tranche de probabilité) calculées sur un
  échantillon de fenêtres historiques complètes, utilisées pour comparer un
  modèle candidat au modèle par défaut.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Le Brier score du modèle par défaut reste égal ou meilleur que
  celui de toute alternative testée sur le jeu de données de référence
  (11–15 septembre 2026, 1 424 fenêtres scorées).
- **SC-002**: Aucune régression de calibration n'est introduite en production
  sans comparaison documentée préalable entre le modèle par défaut et
  l'alternative envisagée.
- **SC-003**: La décision de conserver ou remplacer le modèle par défaut est
  traçable a posteriori dans le journal de décisions du projet.

## Assumptions

- Le jeu de données de référence (5 jours, 1 440 fenêtres complètes, 1 424
  scorées) est suffisant pour une première évaluation comparative, mais pas
  pour une conclusion statistique définitive sur un horizon plus long.
- Le paramètre de retour à la moyenne reste disponible comme option
  expérimentale pour de futurs tests, sans devenir le modèle par défaut.
- Aucun changement de comportement par défaut n'est requis par cette
  fonctionnalité : le modèle à dérive nulle était déjà le réglage par défaut
  avant cette évaluation.
