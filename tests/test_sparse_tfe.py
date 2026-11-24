import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from sparse_tfe import TEXEL_TYPES, emit_fetch, status_register


class Recorder:
    def __init__(self):
        self.n = 0
        self.lines = []
        self.writes = []

    def tmp(self):
        self.n += 1
        return f"%r{self.n}"

    def cu(self, value):
        return f"%c{value}"

    def emit(self, line):
        self.lines.append(line)

    def dst(self, dest, value):
        self.writes.append((dest, value))


class SparseTfeTests(unittest.TestCase):
    def test_all_nonzero_masks(self):
        for mask in range(1, 16):
            for packed in (False, True):
                with self.subTest(mask=mask, packed=packed):
                    components = sum(bool(mask & (1 << i)) for i in range(4))
                    words = (components + 1) // 2 if packed else components
                    self.assertEqual(status_register(8, words + 1, mask, packed), 8 + words)

    def test_invalid_ranges(self):
        for args in ((8, 1, 0, False), (8, 2, 16, False), (8, 1, 1, False),
                     (8, 3, 1, False), (255, 2, 1, False), (-1, 2, 1, False)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                status_register(*args)
        self.assertEqual(status_register(254, 2, 1, False), 255)

    def test_typed_fetch_and_status_dataflow(self):
        for cls, ty in enumerate(TEXEL_TYPES):
            with self.subTest(ty=ty):
                g = Recorder()
                result = emit_fetch(g, "%image", "%coords", "%lod", ty, "%index", 12)
                self.assertEqual(g.lines, [
                    f"%r1 = OpImageSparseFetch %tfe_result_{cls} %image %coords Lod %lod",
                    "%r2 = OpCompositeExtract %uint %r1 0",
                    f"%r3 = OpCompositeExtract {ty} %r1 1",
                    "%r4 = OpImageSparseTexelsResident %bool %r2",
                    "%r5 = OpINotEqual %bool %index %c0",
                    "%r6 = OpLogicalAnd %bool %r4 %r5",
                    "%r7 = OpSelect %uint %r6 %c0 %c1",
                ])
                self.assertEqual(result, "%r3")
                self.assertEqual(g.writes, [({"k": "vgpr", "r": 12, "n": 1}, "%r7")])

    def test_unsupported_type_emits_nothing(self):
        g = Recorder()
        with self.assertRaises(ValueError):
            emit_fetch(g, "%image", "%coords", "%lod", "%float", "%index", 9)
        self.assertEqual(g.lines, [])
        self.assertEqual(g.writes, [])


if __name__ == "__main__":
    unittest.main()
