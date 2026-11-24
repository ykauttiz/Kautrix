"""Verify the dictionary joaat domain and the separately documented runtime-mod hash domain.

    python -I tests/test_joaat_agreement.py

The dictionary uses lowercase Latin-1; the C++ mod runtime hashes UTF-8 bytes with ASCII case-folding.
These are distinct contracts for non-ASCII names. Keep the Python mod manager in sync with its runtime,
not with the dictionary, without weakening agreement checks for all other tools.

Other tools carry their own copy of Jenkins one-at-a-time, so nothing stopped one of them from encoding
the name differently. Two did: audio_config hashed the lowercased UTF-8 bytes, and world_names encoded
to UTF-8 first and then lower-cased the bytes (bytes.lower() only folds ASCII). Both gave a different
32-bit hash for any non-ASCII character, which surfaces as a stream id no bank stores or a name hash
that never resolves, with no error anywhere. This test imports every copy in tools/ and compares it
with the dictionary the rest of the pipeline is pinned to.

Synthetic names only; no game data.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
for path in (ROOT, TOOLS):
    if path not in sys.path:
        sys.path.insert(0, path)

import hashdict  # noqa: E402

# ASCII names, then names with a non-ASCII character: "é" is one Latin-1 byte (0xE9) and two UTF-8
# bytes (0xC3 0xA9), and "ÉLÉGIE" also tells text.lower() apart from lower-casing the encoded bytes.
NAMES = [
    "adder", "plain_name", "burn", "boardwalk",
    "café", "Café", "Señor", "Crème Brûlée", "ÉLÉGIE", "Radio_LosSantos_Café",
]

# The modules whose name a reviewer would expect here: a rename must not turn the loop below into a
# test of nothing.
EXPECTED = ("anim_index", "audio_config", "awc_names", "hashdict", "leonida_mods", "mission_tables", "world_names")


def string_hash_copies():
    """{module name: function} for every ``def joaat(``/``def joaat32(`` that takes a string.

    ``tools/save_keys.py`` defines ``joaat(data, seed=0)`` over bytes with a seed for the composition
    probe; it is a different hash and is covered by tests/test_save_keys.py.
    """
    copies = {}
    for filename in sorted(os.listdir(TOOLS)):
        if not filename.endswith(".py"):
            continue
        with open(os.path.join(TOOLS, filename), encoding="utf-8") as handle:
            source = handle.read()
        for match in re.finditer(r"^def (joaat32|joaat)\(\s*([A-Za-z_]\w*)", source, re.M):
            if match.group(2) == "data":
                continue
            module = __import__(filename[:-3])
            copies[filename[:-3]] = getattr(module, match.group(1))
    return copies


def runtime_mod_joaat_reference(name):
    """Independent numeric oracle for runtime/src/mods.h's ASCII-folded UTF-8 byte contract."""
    data = bytes(byte + 32 if 65 <= byte <= 90 else byte for byte in name.encode("utf-8"))
    value = 0
    for byte in data:
        value = ((value + byte) * 1025) & 0xFFFFFFFF
        value ^= value >> 6
    value = (value * 9) & 0xFFFFFFFF
    value ^= value >> 11
    return (value * 32769) & 0xFFFFFFFF


class JoaatAgreementTest(unittest.TestCase):
    def test_every_copy_matches_the_pinned_dictionary(self):
        copies = string_hash_copies()
        for module in EXPECTED:
            self.assertIn(module, copies)
        for module, function in sorted(copies.items()):
            for name in NAMES:
                with self.subTest(copy=module, name=name):
                    expected = (runtime_mod_joaat_reference(name) if module == "leonida_mods"
                                else hashdict.joaat(name))
                    self.assertEqual(function(name), expected,
                                     f"{module} hashes {name!r} differently from its documented contract")

    def test_mod_name_has_runtime_bytewise_encoding(self):
        # Pinned non-ASCII fixture: a 32-bit result from the C++ runtime contract,
        # not the Latin-1 dictionary domain. "Café" and "café" must still agree.
        import leonida_mods
        self.assertEqual(runtime_mod_joaat_reference("Café"), 2425794034)
        self.assertEqual(leonida_mods.joaat("Café"), 2425794034)
        self.assertEqual(leonida_mods.joaat("café"), 2425794034)
        self.assertNotEqual(leonida_mods.joaat("Café"), hashdict.joaat("Café"))

    def test_a_stream_id_is_the_name_hash_of_the_same_name(self):
        # The consequence of the bug: audio_config's id and the id awc_names recovers from a bank were
        # computed with two different encodings, so they could never match for an accented title.
        import audio_config
        import awc_names

        for title in ("Café", "Crème Brûlée", "Radio Los Santos", "plain_song"):
            with self.subTest(title=title):
                self.assertEqual(audio_config.title_stream_id(title),
                                 awc_names.joaat(audio_config.normalized_name(title)) & 0x1FFFFFFF)


if __name__ == "__main__":
    unittest.main()
