# Engineering benchmark: Leonida and mature recompilation projects

**Reviewed:** 2026-11-23 (UTC-03). **Scope:** public project documentation and code visible in the fork; no retail executable, game content or console keys were used.

This is a **capability-gap analysis**, not a ranking by GitHub stars or lines of code. Different consoles and game engines are not drop-in compatible. A working game is substantially more mature than a tool that can translate and validate shaders but has not booted its target.

## Reference implementations

| Reference | Verified project-level capability | Transferable engineering practice | Not transferable directly |
| --- | --- | --- | --- |
| [Unleashed Recompiled](https://github.com/hedge-dev/UnleashedRecomp) | Native Sonic Unleashed port, Windows/Linux releases, installer, mod support, user settings and a production renderer | Installation/integrity checks, actionable error messages, graceful unsupported-GPU handling, real hardware validation | Its Xbox 360 GPU/CPU interfaces and game-specific patches |
| [Zelda 64: Recompiled](https://github.com/Zelda64Recomp/Zelda64Recomp) | Working Majora's Mask recompilation using RT64, configurable graphics/input, releases for multiple desktop OSes | Compatibility-focused tests, repeatable releases, input mapping and config UX | The N64/RT64 graphics implementation |
| [ReXGlue SDK](https://github.com/rexglue/rexglue-sdk) | Xbox 360 recompilation SDK with stable/nightly builds across multiple architectures | CI matrix, stable vs nightly artifacts, clear SDK/runtime boundaries | PowerPC-to-C++ translation and Xbox 360 kernel/API layout |
| [shadPS4](https://github.com/shadps4-emu/shadPS4) | Experimental PS4 emulator with ongoing compatibility engineering and GPU validation | Graphics troubleshooting, driver-specific regression tracking and CI | Its PS4 emulator ABI and shader translation logic |
| [Project ReCity](https://github.com/pixl27/ProjectReCity) | Separate Leonida-derived effort, also not playable, with explicit [quality-track roadmap](https://github.com/pixl27/ProjectReCity/blob/main/ROADMAP.md) | Evidence-backed milestones, changelog, user-facing build/troubleshooting guide | Its branding and outdated implementation claims |
| [Leonida](https://github.com/ykauttiz/Leonida) | Public README reports 26,503/26,503 shaders translated and validated and a stand-in executable test, **but not a GTA VI boot** | Already has a large test suite, progressive diagnostics, build checks and a progress dashboard | Unverified runtime or real-game compatibility cannot be claimed |

The Unleashed Recompiled project explicitly prohibits AI-assisted pull requests to **its own** repository. No source code from that project or any other reference has been copied into this fork.

## Verified baseline and gaps

- **Game boot / playability:** not implemented or verified. `README.md` explicitly states the target does not boot. Do not label this project playable.
- **Shader coverage:** 26,503/26,503 translated and `spirv-val` passed **as reported by project documentation**; this audit did not reproduce the full corpus on hardware.
- **Loader:** has a stand-in executable path and tests; no successful boot with the retail target is established.
- **D3D12-to-Vulkan:** partial implementation. See `docs/d3d12_vulkan.md`; prioritize contract-driven command tests and real GPU validation instead of feature-count claims.
- **Pipeline build reliability:** [fork PR #3](https://github.com/ykauttiz/Leonida/pull/3) proposes incremental Linux checker refresh; full repository tests remain pending.
- **Documentation correctness:** [fork PR #1](https://github.com/ykauttiz/Leonida/pull/1) updates the FSPX index; [fork PR #2](https://github.com/ykauttiz/Leonida/pull/2) fixes Markdown fence handling with synthetic regression cases.
- **Mod packaging and persistence:** [fork PR #4](https://github.com/ykauttiz/Leonida/pull/4) adds bounded, cross-platform ZIP preflight; [fork PR #5](https://github.com/ykauttiz/Leonida/pull/5) adds transactional `mod.json` writes.
- **CI:** the fork's GitHub Actions API showed zero runs at the time of this audit. Draft PRs are **not** evidence of passing CI.
- **Upstream:** the connected GitHub integration cannot currently access `ProjectLeonida/Leonida` (404). Work has only been published in `ykauttiz/Leonida` and has not been merged upstream.

## Quality gates to adopt (in order)

### Q0. Reproducible pull-request checks

**Exit criterion:** two clean full GitHub Actions runs on the fork against the same head SHA, including Linux/Python, Windows runtime build/test, no-game-data scanner and source-format validation. Record workflow URLs, runner OS and toolchain versions. Do not mark a draft ready until failures are triaged.

Minimum local commands (from the repository root; no proprietary files required):

```sh
python -m compileall -q make_game.py tools tests
python -I tests/test_docs_links.py
# Run the next test only on the branch containing fork PR #2, until merged:
# python -I tests/test_docs_fence_regression.py
python -I tests/test_make_game_pipeline_preflight.py
python -I tests/test_leonida_mods.py
python -I scripts/check_no_game_data.py
```

The standalone tests use Python's standard library. Source PR branches should be merged **only after** review and CI; do not merge unrelated drafts together just to obtain a green indicator.

### Q1. Deterministic installation / recoverability

**Exit criterion:** synthetic installation tests cover oversize and corrupt archives, normalized filename collisions, conflicting directory/file paths, Unicode paths, a failed JSON serialization, a failed file replacement, and recovery without touching the previous installation. Run on NTFS (Windows) and an ordinary case-sensitive Linux filesystem.

**Evidence:** archived test logs from both platforms and a negative test for each failure mode. Ensure all test fixtures are synthetic.

### Q2. Cross-platform rendering validation

**Exit criterion:** a reproducible synthetic scene with the expected frame buffer, shader pipeline cache and explicit GPU/driver capabilities; compare output with a reference or tolerance contract. Run at least two different GPU vendors when hardware is available. Keep raw game data out of the repository and its CI artifacts.

**Evidence:** command lines, logs, GPU/driver identity and pass/fail by shader stage. Distinguish SPIR-V validation from correct on-screen output.

### Q3. Runtime compatibility contract

**Exit criterion:** every known import/shim method on the critical boot path has either a synthetic positive/negative contract test or a documented reason that real target input is needed. Keep unsupported APIs detectable and fail closed rather than returning fabricated success.

**Evidence:** mapped ABI table in `docs/d3d12_vulkan.md` / `docs/design.md`, deterministic stand-in fixture, and test results.

### Q4. Real target validation (authorized environment only)

**Exit criterion:** evidence from a user's lawfully obtained, already readable copy, with private logs scrubbed of proprietary content. No game data, executable, generated shaders, files, encryption workarounds, credentials, or links to obtain them may be committed, uploaded to issues or stored in CI.

A successful stand-in does **not** count as a real boot. A shader count does **not** count as playability.

### Q5. Release engineering

**Exit criterion:** reproducible builds and checksums for safe, redistributable tools; CPU/GPU requirements documented; a rollback strategy, versioned changelog and clear compatibility matrix. Never distribute game-derived output or claim an executable is ready for players before Q4.

## Current evidence ledger

| Item | Published where | Verified by this work | Missing to consider complete |
| --- | --- | --- | --- |
| FSPX documentation | Fork PR #1 | Diff reviewed against `docs/fspx.md` | Upstream freshness and repository CI |
| Markdown link checker | Fork PR #2 | Isolated parser regression tests | Full docs scan, Windows and Linux CI |
| Linux Vulkan checker refresh | Fork PR #3 | Static diff review, isolated reasoning | Real preflight suite, CMake and Linux run |
| ZIP archive safety | Fork PR #4 | 11 isolated extraction tests, including positive and negative cases | Full mod-manager tests on Linux and Windows |
| Atomic manifest | Fork PR #5 | 4 isolated persistence tests | Full mod-manager tests and Windows replacement semantics |
| Actual game boot | None | No | Entire authorized runtime integration path |

## Decision rules

1. Prefer **one measured fix plus a failing regression test** over broad, speculative edits.
2. Keep changes in separate branches, and retain the `main` branch as the untouched baseline until checks pass.
3. Do not copy implementation code from other projects without reviewing its license and compatibility; architectural ideas are not code.
4. Put a concrete failure reproduction, expected result, observed result, test environment and fallback in every PR.
5. Only claim superiority on a defined and measured dimension (for example, installer path validation), **never** superiority of the entire port while the target does not boot.
6. Review the active upstream coordination claims and existing PRs before posting. The GitHub integration may be unable to write to that upstream.

Related local documentation: [progress dashboard](progress_dashboard.md), [design contract](design.md), [D3D12-to-Vulkan plan](d3d12_vulkan.md), and [modding guide](modding.md).
