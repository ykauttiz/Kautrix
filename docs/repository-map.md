# Repository map

This map documents the existing boundaries rather than moving files for cosmetic reasons.
Baseline: `de4c37383cad386eaa851047628d7b92c51009c5` (2,366 tracked blobs, 71 directories and one submodule).
The inventory is not a full source audit.

| Area | Responsibility |
| --- | --- |
| [runtime/](../runtime/) | Native host runtime, graphics integration and CMake targets |
| [tools/](../tools/) | Python, .NET and Rust research tools |
| [tests/](../tests/) | Synthetic regressions and semantic fixtures |
| [scripts/](../scripts/) | Build, validation and maintenance entry points |
| [viewer/](../viewer/) | Local visualization application |
| [docs/](README.md) | Technical contracts, experiments and project documentation |
| [assets/](../assets/) | Tracked project artwork only |
| [game/](../game/) | Reference manifests, not game binaries |
| [mods/](../mods/) | Mod interface and supporting source |
| [gpu-test-results/](../gpu-test-results/) | Historical validation reports |
| [third_party/](../third_party/) | External components with their own license and provenance |
| [.github/](../.github/) | Workflows, templates and dependency configuration |

## Entry points

- [README](../README.md): public project overview.
- [Build.bat](../Build.bat) and [make_game.py](../make_game.py): existing build entry points.
- [runtime/README.md](../runtime/README.md): runtime development.
- [Testing guide](testing.md): toolchain and validation.
- [Design contract](design.md): technical specifications and limitations.
- [AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md): contributor rules.

## Maintenance boundaries

- Keep generated files outside tracked source; do not commit proprietary artifacts.
- Keep submodule changes and license updates separately reviewable.
- Preserve compatibility names, test fixtures and relative links until all callers are audited.
- Do not merge runtime or translator changes merely to consolidate branches. Require the relevant checks at the exact commit.
- Prefer a single focused commit per concern and record failures, skipped checks and untested behavior.
- Retain the original logo and technical history as upstream attribution; use the Kautrix mark for new public-facing material.

## Review order

See the [engineering roadmap](kautrix.md) and the live [pull request queue](https://github.com/ykauttiz/Kautrix/pulls).
The documentation/branding work can be reviewed separately from runtime and translator changes.
