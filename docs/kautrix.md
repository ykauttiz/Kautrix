# Kautrix engineering roadmap

Kautrix is a GPL-3.0-or-later fork of Leonida. Its primary objective is verifiable engineering, not claims of playable compatibility.

## Integration queue

Snapshot inspected on 2026-11-24. Follow the linked PR and exact-commit checks for current state.

| Workstream | Review | Acceptance gate |
| --- | --- | --- |
| Cross-platform baseline | [#13](https://github.com/ykauttiz/Kautrix/pull/13) | Relevant synthetic suites pass on Linux, Windows and macOS |
| Mod archive limits | [#4](https://github.com/ykauttiz/Kautrix/pull/4) | Bounds and path regression tests |
| Atomic mod manifests | [#5](https://github.com/ykauttiz/Kautrix/pull/5) | Failure/rollback tests |
| Mod staging | [#7](https://github.com/ykauttiz/Kautrix/pull/7) | Correctness and measured performance |
| MIMG analysis | [#25](https://github.com/ykauttiz/Kautrix/pull/25) | Semantic regression evidence |
| Identity and viewer | [#26](https://github.com/ykauttiz/Kautrix/pull/26) | Reconcile documentation already integrated and verify remaining code changes |
| Integration branch | [#27](https://github.com/ykauttiz/Kautrix/pull/27) | Review scope, dependencies and failures |
| Sparse TFE experiment | [#28](https://github.com/ykauttiz/Kautrix/pull/28) | Independent validation; keep opt-in until verified |

## Acceptance policy

Record the exact SHA, commands, platform, toolchain and outcomes. Static inspection is not a successful execution; synthetic execution is not proof of hardware correctness. No compatibility or performance claim without relevant reproducible evidence.

Prioritize CI and reproducible synthetic tests before broad refactors. Preserve the [technical contract](design.md), [license](../LICENSE), original attribution and [security boundaries](../SECURITY.md).
No retail data is required for ordinary development or documentation changes.

## Branding and structure

The public identity is Kautrix. Internal executable names, environment variables, modules, output directories and report schemas remain compatible until a separately tested migration.
The [repository map](repository-map.md) explains the existing layout. The [static website](index.html) is source code only; GitHub Pages deployment is not confirmed.
