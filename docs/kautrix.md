# Kautrix engineering roadmap

Identity introduced 2026-11-24. GPL-3.0-or-later fork of [Vyzerythe/Leonida](https://github.com/Vyzerythe/Leonida). Original contributors and notices remain credited.

## Baseline audit

Inventory at `de4c37383cad386eaa851047628d7b92c51009c5`: 2,366 tracked blobs, including 767 under runtime, 1,099 under tests, 322 under tools, 90 under docs, 21 under scripts and 3 under viewer. Inventory is not a line-by-line review. No corpus or game data was fetched for this audit.

| Work | Evidence / acceptance |
|---|---|
| [PR #13](https://github.com/ykauttiz/Leonida/pull/13) | PERMLANE FI/BC patch on 96c776a597d6; No Game Data passed, CI and Runtime queued at inspection. Require exact-commit cross-platform results. |
| [PR #25](https://github.com/ykauttiz/Leonida/pull/25) | MIMG TFE status accounting on e407f8edf5ca; No Game Data passed, CI and Runtime queued. Require full semantic suite. |
| [Sparse TFE experiment](https://github.com/ykauttiz/Leonida/tree/experiment/sparse-tfe-image-load) | Prototype at c5cad3853cbd, disabled by default. Require SPIR-V validation, feature negotiation and resident/nonresident GPU cases before enabling. |
| Mesh wave information | Historical Linux mismatch. Isolate expected subgroup behavior and compare to hardware. |
| Viewer CLI | Bound numeric conversion for unusually long port arguments; add regression cases. |

## Review sequence

1. Establish a green synthetic baseline for Linux, Windows and macOS.
2. Review translator and decoder contracts, EXEC behavior, image types, TFE residency and error paths.
3. Review runtime ABI, Vulkan feature gating, resource lifetimes and synchronization.
4. Review tooling path handling, subprocess return codes, input validation, CI permissions and dependency provenance.
5. Improve viewer accessibility, portability and diagnostics without changing compatibility identifiers prematurely.

Keep each change reviewable. Record exact SHA, tool versions, commands and pass/fail/skip status. No universal compatibility or performance claim without reproducible measurement.

## Branding migration

Public-facing documentation and viewer names change first. Legacy executable names, CMake targets, Python modules, environment variables, GitHub remote and historical references remain intentionally compatible until their callers are audited. Keep original upstream logos and credits as historical material, not the new project mark. Before a repository rename, update automation configuration, remote URLs and any dependent action references.

## References

- [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)
- [GitHub repository best practices](https://docs.github.com/en/repositories/creating-and-managing-repositories/best-practices-for-repositories)
- [GitHub repository rename considerations](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository)
- [SPIR-V specification](https://registry.khronos.org/SPIR-V/specs/unified1/SPIRV.html)
- [Vulkan device features](https://docs.vulkan.org/refpages/latest/refpages/source/VkPhysicalDeviceFeatures.html)
