"""Tests for active-lane guarding of all image_atomic_* ops.

Every 2D and 3D storage-image atomic must guard OpAccessChain / OpImageTexelPointer / OpAtomic* inside
`if (active)` (matching image_atomic_cmpswap, buffer_atomic, global_atomic, and lds_op) so inactive lanes
never execute spurious RMWs or dereference uninitialized descriptors/coordinates.

Also checks glc write-back inside the guarded block, the extra 32-bit ops lowered via
`emit_image_atomic_extra` (`image_atomic_swap`, `image_atomic_inc`, `image_atomic_dec`) with `a16`/`glc`,
preservation of two-VGPR `dmask:0x3` standard ops for corpus compatibility, and invalid-form rejections.

Standard library only; validates emitted SPIR-V with spirv-as/spirv-val when on PATH. No GPU or game data.
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import support  # noqa: E402

rdna2spirv = support.rdna2spirv
SPIRV_AS = shutil.which("spirv-as")
SPIRV_VAL = shutil.which("spirv-val")

FSSM = bytes(0x138) + struct.pack("<4I", 0x88 | (12 << 1), 32, 1, 1)


def translate(lines, *, emulate=False):
    prev = rdna2spirv.WAVE64_EMULATE
    rdna2spirv.WAVE64_EMULATE = emulate
    try:
        return support.translate_lines("cs_img_atomic_guard", lines + ["s_endpgm"], fssm=FSSM)
    finally:
        rdna2spirv.WAVE64_EMULATE = prev


def validate_spirv(spv):
    if not (SPIRV_AS and SPIRV_VAL):
        return
    with tempfile.TemporaryDirectory() as tmp:
        asm = os.path.join(tmp, "s.spvasm")
        bin_ = os.path.join(tmp, "s.spv")
        with open(asm, "w", encoding="utf-8", newline="\n") as f:
            f.write(spv)
        r1 = subprocess.run([SPIRV_AS, "--target-env", "vulkan1.3", asm, "-o", bin_],
                            capture_output=True, text=True)
        if r1.returncode != 0:
            raise AssertionError(f"spirv-as failed:\n{r1.stderr}")
        r2 = subprocess.run([SPIRV_VAL, "--target-env", "vulkan1.3", bin_],
                            capture_output=True, text=True)
        if r2.returncode != 0:
            raise AssertionError(f"spirv-val failed:\n{r2.stderr}")


def parse_blocks(spv):
    """Map label -> list of instruction lines in that basic block."""
    blocks = {}
    cur = None
    for raw in spv.splitlines():
        s = raw.strip()
        if s.endswith("= OpLabel"):
            cur = s.split(" = ")[0]
            blocks[cur] = []
        elif cur is not None and s:
            blocks[cur].append(s)
    return blocks


def find_guarded_texel_block(spv):
    """Prove every image-atomic texel pointer is dominated by the active-true path.

    OpSelectionMerge guards a *region*, not necessarily a single block. An
    inc/dec CAS lowering nests a loop inside that region, so the pointer is
    legitimately in the loop body rather than the guard's direct true target.
    Compute CFG dominators instead of requiring a direct branch target.
    """
    blocks = parse_blocks(spv)
    active_loads = set(re.findall(r"^\s*(%\w+) = OpLoad %bool %active\b", spv, re.M))
    successors = {}
    for label, insns in blocks.items():
        targets = []
        if insns:
            term = insns[-1].split()
            if term[0] == "OpBranch" and len(term) >= 2:
                targets = [term[1]]
            elif term[0] == "OpBranchConditional" and len(term) >= 4:
                targets = [term[2], term[3]]
            elif term[0] == "OpSwitch" and len(term) >= 3:
                targets = [term[2], *term[4::2]]
        successors[label] = [t for t in targets if t in blocks]

    predecessors = {label: set() for label in blocks}
    for label, targets in successors.items():
        for target in targets:
            predecessors[target].add(label)

    # Multiple shader helper functions may occur in one module. The entry of
    # each connected component has no incoming CFG edges.
    roots = {label for label, incoming in predecessors.items() if not incoming}
    reachable = set(roots)
    pending = list(roots)
    while pending:
        for target in successors[pending.pop()]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)

    dominators = {label: ({label} if label in roots else set(reachable))
                  for label in reachable}
    changed = True
    while changed:
        changed = False
        for label in reachable - roots:
            incoming = predecessors[label] & reachable
            if not incoming:
                continue
            common = set.intersection(*(dominators[p] for p in incoming))
            new = {label} | common
            if new != dominators[label]:
                dominators[label] = new
                changed = True

    # With no EXEC-changing instruction the translator marks exec_static_true:
    # active_b() then returns %true instead of loading %active. Such a
    # compile-time guard is safe only if this module never stores a dynamic
    # value to %active; the negative regression below enforces that boundary.
    dynamic_active_store = re.search(
        r"^\s*OpStore %active (?!%true(?:\s|$))", spv, re.M
    ) is not None
    guarded_targets = set()
    for insns in blocks.values():
        for i, ln in enumerate(insns):
            if ln.startswith("OpSelectionMerge ") and i + 1 < len(insns):
                br = insns[i + 1]
                m = re.match(r"OpBranchConditional (%\w+) (%\w+) (%\w+)", br)
                if m and (m.group(1) in active_loads or
                          (m.group(1) == "%true" and not dynamic_active_store)):
                    guarded_targets.add(m.group(2))

    found = []
    for label, insns in blocks.items():
        if any("= OpImageTexelPointer " in ln for ln in insns):
            if label not in reachable or not any(
                target in dominators[label] for target in guarded_targets
            ):
                raise AssertionError(f"OpImageTexelPointer in unguarded block {label}")
            found.append((label, insns))
    if not found:
        raise AssertionError("No OpImageTexelPointer emitted")
    return found


def vgpr_ptrs(spv, reg_idx):
    """Return all SSA pointer IDs pointing to %vgpr[reg_idx]."""
    consts = {k: int(v) for k, v in re.findall(r"^\s*(%\w+) = OpConstant %uint (\d+)\b", spv, re.M)}
    return {
        ptr for ptr, idx in re.findall(r"^\s*(%\w+) = OpAccessChain %_ptr_Function_uint %vgpr (%\w+)\b", spv, re.M)
        if consts.get(idx) == reg_idx
    }


class GuardDominanceTests(unittest.TestCase):
    """Synthetic CFGs to keep the guard checker strict without a GPU."""

    def test_nested_cas_loop_remains_inside_active_guard(self):
        spv = """
%entry = OpLabel
%flag = OpLoad %bool %active
OpSelectionMerge %merge None
OpBranchConditional %flag %guarded %merge
%guarded = OpLabel
OpBranch %loop
%loop = OpLabel
OpLoopMerge %loop_done %loop_next None
OpBranch %do
%do = OpLabel
%ptr = OpImageTexelPointer %ptr_image %img %coords %sample
OpBranch %loop_next
%loop_next = OpLabel
OpBranchConditional %retry %loop %loop_done
%loop_done = OpLabel
OpBranch %merge
%merge = OpLabel
OpReturn
"""
        self.assertEqual([label for label, _ in find_guarded_texel_block(spv)], ["%do"])

    def test_access_after_guard_merge_is_rejected(self):
        spv = """
%entry = OpLabel
%flag = OpLoad %bool %active
OpSelectionMerge %merge None
OpBranchConditional %flag %guarded %merge
%guarded = OpLabel
OpBranch %merge
%merge = OpLabel
%ptr = OpImageTexelPointer %ptr_image %img %coords %sample
OpReturn
"""
        with self.assertRaisesRegex(AssertionError, "unguarded block %merge"):
            find_guarded_texel_block(spv)

    def test_inactive_branch_access_is_rejected(self):
        spv = """
%entry = OpLabel
%flag = OpLoad %bool %active
OpSelectionMerge %merge None
OpBranchConditional %flag %guarded %inactive
%guarded = OpLabel
OpBranch %merge
%inactive = OpLabel
%ptr = OpImageTexelPointer %ptr_image %img %coords %sample
OpBranch %merge
%merge = OpLabel
OpReturn
"""
        with self.assertRaisesRegex(AssertionError, "unguarded block %inactive"):
            find_guarded_texel_block(spv)


    def test_static_true_guard_with_no_exec_write_is_valid(self):
        spv = """
%entry = OpLabel
OpSelectionMerge %merge None
OpBranchConditional %true %guarded %merge
%guarded = OpLabel
%ptr = OpImageTexelPointer %ptr_image %img %coords %sample
OpBranch %merge
%merge = OpLabel
OpReturn
"""
        self.assertEqual([name for name, _ in find_guarded_texel_block(spv)], ["%guarded"])

    def test_static_true_guard_cannot_hide_dynamic_exec_write(self):
        spv = """
%entry = OpLabel
OpStore %active %false
OpSelectionMerge %merge None
OpBranchConditional %true %guarded %merge
%guarded = OpLabel
%ptr = OpImageTexelPointer %ptr_image %img %coords %sample
OpBranch %merge
%merge = OpLabel
OpReturn
"""
        with self.assertRaisesRegex(AssertionError, "unguarded block %guarded"):
            find_guarded_texel_block(spv)

class ImageAtomicsGuardTests(unittest.TestCase):
    def test_all_image_atomics_2d_and_3d_guarded_and_valid(self):
        simple_ops = [
            ("image_atomic_add", "OpAtomicIAdd"),
            ("image_atomic_sub", "OpAtomicISub"),
            ("image_atomic_umin", "OpAtomicUMin"),
            ("image_atomic_umax", "OpAtomicUMax"),
            ("image_atomic_smin", "OpAtomicSMin"),
            ("image_atomic_smax", "OpAtomicSMax"),
            ("image_atomic_and", "OpAtomicAnd"),
            ("image_atomic_or", "OpAtomicOr"),
            ("image_atomic_xor", "OpAtomicXor"),
            ("image_atomic_swap", "OpAtomicExchange"),
        ]
        for op, spv_op in simple_ops:
            for dim, addr, arr_var in (
                ("dim:SQ_RSRC_IMG_2D", "v[1:2]", "%simages"),
                ("dim:SQ_RSRC_IMG_3D", "v[1:3]", "%simages3d"),
            ):
                for glc in ("", " glc"):
                    with self.subTest(op=op, dim=dim, glc=bool(glc)):
                        lines = [
                            "v_mov_b32_e32 v0, 5",
                            "v_mov_b32_e32 v1, 1",
                            "v_mov_b32_e32 v2, 2",
                            "v_mov_b32_e32 v3, 3",
                            f"{op} v0, {addr}, s[4:11] dmask:0x1 {dim} unorm{glc}",
                        ]
                        spv = translate(lines)
                        validate_spirv(spv)
                        guarded = find_guarded_texel_block(spv)
                        self.assertEqual(len(guarded), 1)
                        insns = guarded[0][1]
                        self.assertTrue(any(f"OpAccessChain " in ln and arr_var in ln for ln in insns),
                                        "OpAccessChain must sit inside the active-guarded block")
                        self.assertTrue(any(f"= {spv_op} %uint " in ln for ln in insns))
                        v0_ptrs = vgpr_ptrs(spv, 0)
                        has_glc_store = any(
                            ln.startswith("OpStore ") and ln.split()[1] in v0_ptrs for ln in insns
                        )
                        self.assertEqual(has_glc_store, bool(glc),
                                         f"glc={bool(glc)} mismatch for v0 write-back in guarded block")

    def test_image_atomic_inc_and_dec_cas_2d_3d_and_a16(self):
        cases = [
            ("image_atomic_inc", "dim:SQ_RSRC_IMG_2D", "v[1:2]", "", "OpUGreaterThanEqual"),
            ("image_atomic_dec", "dim:SQ_RSRC_IMG_2D", "v[1:2]", " glc", "OpUGreaterThan "),
            ("image_atomic_inc", "dim:SQ_RSRC_IMG_3D", "v[1:3]", " glc", "OpUGreaterThanEqual"),
            ("image_atomic_dec", "dim:SQ_RSRC_IMG_3D", "v[1:2]", " a16", "OpUGreaterThan "),
            ("image_atomic_swap", "dim:SQ_RSRC_IMG_2D", "v1", " a16 glc", "OpAtomicExchange"),
            ("image_atomic_swap", "dim:SQ_RSRC_IMG_3D", "v[1:2]", " a16", "OpAtomicExchange"),
        ]
        for op, dim, addr, extra, marker in cases:
            with self.subTest(op=op, dim=dim, extra=extra):
                lines = [
                    "v_mov_b32_e32 v0, 7",
                    "v_mov_b32_e32 v1, 0x00020001",
                    "v_mov_b32_e32 v2, 3",
                    "v_mov_b32_e32 v3, 4",
                    f"{op} v0, {addr}, s[4:11] dmask:0x1 {dim} unorm{extra}",
                ]
                spv = translate(lines)
                validate_spirv(spv)
                find_guarded_texel_block(spv)
                self.assertIn(marker, spv)
                if op in ("image_atomic_inc", "image_atomic_dec"):
                    self.assertIn("OpAtomicLoad %uint ", spv)
                    self.assertIn("OpAtomicCompareExchange %uint ", spv)
                    self.assertIn("OpLoopMerge ", spv)

    def test_cmpswap_and_bad_data_width_rejected(self):
        # Valid 2-VGPR cmpswap (2D and 3D) stays guarded and valid.
        for dim, addr in (("dim:SQ_RSRC_IMG_2D", "v[1:2]"), ("dim:SQ_RSRC_IMG_3D", "v[1:3]")):
            spv = translate([
                "v_mov_b32_e32 v4, 1", "v_mov_b32_e32 v5, 2",
                "v_mov_b32_e32 v1, 0", "v_mov_b32_e32 v2, 0", "v_mov_b32_e32 v3, 0",
                f"image_atomic_cmpswap v[4:5], {addr}, s[4:11] dmask:0x3 {dim} unorm glc",
            ])
            validate_spirv(spv)
            find_guarded_texel_block(spv)

        # Two-VGPR dmask:0x3 standard ops (64-bit corpus forms, e.g. image_atomic_umax) stay accepted
        # and active-guarded so corpus translation coverage does not regress before Int64ImageEXT lands.
        spv64 = translate([
            "v_mov_b32_e32 v4, 1", "v_mov_b32_e32 v5, 0",
            "v_mov_b32_e32 v1, 0", "v_mov_b32_e32 v2, 0",
            "image_atomic_umax v[4:5], v[1:2], s[4:11] dmask:0x3 dim:SQ_RSRC_IMG_2D unorm",
        ])
        validate_spirv(spv64)
        find_guarded_texel_block(spv64)

        # Wrong-width data operands on extra 32-bit ops and cmpswap must still raise Unsupported.
        for bad in (
            "image_atomic_swap v[0:1], v[1:2], s[4:11] dmask:0x3 dim:SQ_RSRC_IMG_2D unorm",
            "image_atomic_inc v[0:1], v[1:2], s[4:11] dmask:0x3 dim:SQ_RSRC_IMG_2D unorm",
            "image_atomic_cmpswap v0, v[1:2], s[4:11] dmask:0x1 dim:SQ_RSRC_IMG_2D unorm",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(rdna2spirv.Unsupported):
                    translate(["v_mov_b32_e32 v0, 0", "v_mov_b32_e32 v1, 0", "v_mov_b32_e32 v2, 0", bad])


if __name__ == "__main__":
    unittest.main()
