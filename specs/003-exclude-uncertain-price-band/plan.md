# Implementation Plan: Exclusion des tokens à prix trop incertain

**Branch**: `003-exclude-uncertain-price-band` | **Date**: 2026-09-21 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-exclude-uncertain-price-band/spec.md`

## Summary

Ajouter, dans la décision d'achat de token du backtest (`_simulate_pnl`), un
garde-fou supplémentaire qui exclut tout côté dont le prix de marché
exécutable se situe entre 0,45 et 0,55 inclus, indépendamment de l'edge
Oracle calculé par ailleurs. Le motif de rejet correspondant
(`uncertain_price_band`) est distinct du motif existant
(`edge_below_threshold`), conformément au principe IV de la constitution
(traçabilité des rejets). Les bornes sont configurables via
`--uncertain-price-band-low` / `--uncertain-price-band-high` (CLI) avec
0,45/0,55 comme valeurs par défaut, sans changer le comportement des appels
existants qui ne les fournissent pas explicitement.

## Technical Context

**Language/Version**: Python 3.12 (venv du projet, `arbitrage_poly/.venv`)
**Primary Dependencies**: bibliothèque standard uniquement pour ce filtre
(aucune nouvelle dépendance) ; s'intègre dans `arbitrage_poly.apps.replay`
**Storage**: fichiers CSV/JSON existants (aucun nouveau schéma de stockage)
**Testing**: `pytest`, suite existante dans
`tests/unit/oracle/test_replay.py` et `tests/unit/oracle/test_replay_polymarket.py`
**Target Platform**: CLI locale (Mac), identique au reste du projet
**Project Type**: Single project (bibliothèque + CLI Python)
**Performance Goals**: aucune contrainte nouvelle ; le filtre est une
comparaison de bornes en O(1) par fenêtre, sans appel réseau supplémentaire
**Constraints**: doit rester une opération métier pure et déterministe, sans
I/O (principe III de la constitution) ; ne doit pas modifier le score Oracle,
seulement la simulation économique (P/L)
**Scale/Scope**: modification ciblée d'une seule fonction (`_simulate_pnl`)
et de son câblage CLI/rapport ; aucun nouveau module

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Principe III (logique métier pure)** : le filtre est une comparaison
  arithmétique pure sur des valeurs déjà résolues (prix, probabilité), sans
  I/O ni dépendance au transport réseau. ✅ Conforme.
- **Principe IV (aucun changement silencieux, rejets tracés)** : une fenêtre
  exclue par ce filtre reste présente dans la sortie avec un
  `economic_status` explicite (`uncertain_price_band`), distinct des autres
  motifs de rejet. Aucune fenêtre n'est supprimée silencieusement. ✅ Conforme.
- **Principe V (risque et périmètre conservateurs)** : ce filtre réduit
  strictement l'ensemble des paris acceptés (il ne peut qu'exclure des paris
  déjà qualifiés), ce qui est cohérent avec une posture conservatrice. ✅ Conforme.
- **Principe VII (simplicité)** : implémenté comme deux bornes float
  configurables réutilisant le style existant (`min_edge`,
  `reversion_speed`), sans nouvelle abstraction ni nouveau module. ✅ Conforme.

Aucune violation identifiée ; aucune entrée requise dans Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/003-exclude-uncertain-price-band/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
arbitrage_poly/
├── src/arbitrage_poly/apps/replay.py   # _simulate_pnl, run_replay, CLI (modifié)
└── tests/unit/oracle/
    ├── test_replay.py                  # tests du garde-fou directionnel/edge existants
    └── test_replay_polymarket.py       # tests avec pricing Polymarket simulé
```

**Structure Decision**: Projet unique existant (`arbitrage_poly/`), aucune
nouvelle structure. La modification reste localisée à
`src/arbitrage_poly/apps/replay.py`, cohérente avec le principe VII
(simplicité, pas de couche supplémentaire).

## Complexity Tracking

*Aucune violation de la Constitution Check ci-dessus ; section non applicable.*
