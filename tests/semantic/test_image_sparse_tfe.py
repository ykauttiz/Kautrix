"""Structural checks of the opt-in sparse TFE translator path, not GPU residency evidence."""
import unittest
from unittest.mock import patch

import support
import rdna2spirv


def translate(mods, dest):
    return support.translate_lines("ps_sparse_tfe", [
        f"image_load {dest}, v[20:21], s[8:15] dmask:0x1 dim:SQ_RSRC_IMG_2D unorm {mods}",
        "s_waitcnt vmcnt(0)", "exp mrt0 v8, v9, v8, v9 done vm", "s_endpgm"])


class SparseTfeTranslationTests(unittest.TestCase):
    def test_opt_in_typed_sparse_fetch(self):
        with patch.object(rdna2spirv, "SPARSE_TFE_EXPERIMENT", True):
            for mods in ("tfe", "tfe d16"):
                with self.subTest(mods=mods):
                    asm = translate(mods, "v[8:9]")
                    self.assertIn("OpCapability SparseResidency", asm)
                    self.assertEqual(asm.count(" = OpImageSparseFetch "), 3)
                    self.assertEqual(asm.count(" = OpImageSparseTexelsResident "), 3)
                    for i in range(3):
                        self.assertIn(f"%tfe_result_{i} = OpTypeStruct %uint ", asm)

    def test_plain_load_not_changed_by_opt_in(self):
        with patch.object(rdna2spirv, "SPARSE_TFE_EXPERIMENT", True):
            asm = translate("", "v8")
        self.assertNotIn("SparseResidency", asm)
        self.assertNotIn("OpImageSparseFetch", asm)

    def test_bad_range_and_coherent_tfe_refused(self):
        with patch.object(rdna2spirv, "SPARSE_TFE_EXPERIMENT", True):
            with self.assertRaises(rdna2spirv.Unsupported):
                translate("tfe", "v8")
            with self.assertRaises(rdna2spirv.Unsupported):
                translate("tfe glc", "v[8:9]")


if __name__ == "__main__":
    unittest.main()
