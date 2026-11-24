"""tools/leonida_mods.py: local mod manifests, folders and ZIP packages. No game data.

    python -I tests/test_leonida_mods.py -v
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import leonida_mods  # noqa: E402

MANDY = {
    "name": "Mandy in the prison intro",
    "enabled": True,
    "game_version": "1.0.2.1",
    "archetype_swaps": [
        {"from": "player_story000_00", "to": "cs_mandy", "when": "always"}
    ],
}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = leonida_mods.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ManifestTests(unittest.TestCase):
    def test_hashes_match_the_runtime_contract(self):
        self.assertEqual(leonida_mods.joaat("player_story000_00"), 0xE472B687)
        self.assertEqual(leonida_mods.joaat("cs_mandy"), 0xD7C0CDFF)
        self.assertEqual(leonida_mods.parse_model_id("0xD7C0CDFF"), 0xD7C0CDFF)
        with self.assertRaises(ValueError):
            leonida_mods.parse_model_id("0x")
        with self.assertRaises(ValueError):
            leonida_mods.parse_model_id("0x123456789")

    def test_manifest_validation_matches_runtime_rules(self):
        mod = leonida_mods.validate_manifest(MANDY, "mandy_prison_intro")
        self.assertEqual(mod.name, "Mandy in the prison intro")
        self.assertEqual(mod.swaps[0].from_id, 0xE472B687)
        self.assertEqual(mod.swaps[0].to_id, 0xD7C0CDFF)

        def bad(data, needle):
            with self.assertRaises(leonida_mods.ModsError) as cm:
                leonida_mods.validate_manifest(data, "bad")
            self.assertIn(needle, str(cm.exception))

        bad({"enabled": "yes"}, '"enabled" must be true or false')
        bad({"archetype_swaps": [{"from": "a", "to": "b", "when": "mission:intro"}]},
            '"when" can only be "always"')
        bad({"archetype_swaps": [{"from": "cs_mandy", "to": "0xD7C0CDFF"}]}, "with itself")
        bad({"archetype_swaps": "all"}, '"archetype_swaps" must be a list')
        bad([1, 2], "not a JSON object")


class ManagerTests(unittest.TestCase):
    def test_new_list_enable_disable_and_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = os.path.join(tmp, "mods")
            code, out, err = cli("--dir", mods, "new", "lucia_to_mandy", "--name", "Mandy",
                                 "--game-version", "1.0.2.1",
                                 "--swap", "player_story000_00=cs_mandy")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("created lucia_to_mandy", out)
            manifest = Path(mods, "lucia_to_mandy", "mod.json")
            self.assertEqual(read_json(manifest)["archetype_swaps"][0]["to"], "cs_mandy")

            code, out, err = cli("--dir", mods, "disable", "lucia_to_mandy")
            self.assertEqual((code, err), (0, ""))
            self.assertFalse(read_json(manifest)["enabled"])

            code, out, err = cli("--dir", mods, "list", "--json")
            self.assertEqual((code, err), (0, ""))
            report = json.loads(out)
            self.assertEqual(report["mods"][0]["folder"], "lucia_to_mandy")
            self.assertFalse(report["mods"][0]["enabled"])

            code, out, err = cli("--dir", mods, "uninstall", "lucia_to_mandy", "--yes")
            self.assertEqual((code, err), (0, ""))
            self.assertFalse(manifest.exists())

    def test_install_folder_package_and_pack_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp, "package", "mandy_prison_intro")
            write_json(package / "mod.json", MANDY)
            mods = os.path.join(tmp, "mods")
            code, out, err = cli("--dir", mods, "install", str(Path(tmp, "package")))
            self.assertEqual((code, err), (0, ""))
            self.assertIn("installed mandy_prison_intro", out)
            installed = Path(mods, "mandy_prison_intro", "mod.json")
            self.assertEqual(read_json(installed), MANDY)

            packed = Path(tmp, "mandy.leonidamod")
            code, out, err = cli("--dir", mods, "pack", "mandy_prison_intro", "--out", str(packed))
            self.assertEqual((code, err), (0, ""))
            self.assertTrue(packed.is_file())
            with zipfile.ZipFile(packed) as archive:
                self.assertEqual(archive.namelist(), ["mandy_prison_intro/mod.json"])

            other_mods = os.path.join(tmp, "other-mods")
            code, out, err = cli("--dir", other_mods, "install", str(packed), "--disable")
            self.assertEqual((code, err), (0, ""))
            installed_copy = Path(other_mods, "mandy_prison_intro", "mod.json")
            self.assertFalse(read_json(installed_copy)["enabled"])

    def test_zip_install_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp, "bad.zip")
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("mod.json", json.dumps(MANDY))
                archive.writestr("../escape.txt", "nope")
            code, out, err = cli("--dir", os.path.join(tmp, "mods"), "install", str(package))
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("unsafe ZIP member", err)
            self.assertFalse(Path(tmp, "escape.txt").exists())

    def test_check_reports_broken_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp, "mods", "broken")
            broken.mkdir(parents=True)
            broken.joinpath("mod.json").write_text("{not json", encoding="utf-8")
            code, out, err = cli("--dir", os.path.join(tmp, "mods"), "check")
            self.assertEqual(code, 1)
            self.assertIn("skipped broken:", out)
            self.assertEqual(err, "")

    def test_run_print_builds_gta6rt_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = Path(tmp, "port")
            game = Path(tmp, "game")
            port.mkdir()
            game.mkdir()
            port.joinpath("gta6rt.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            port.joinpath("game_path.txt").write_text(str(game) + "\n", encoding="utf-8")
            image = game / leonida_mods.EXPECTED_EXE
            image.write_bytes(b"MZ")
            code, out, err = cli("--port", str(port), "run", "--dry-run", "--print",
                                 "--archetype-lookup", "1A2B3C0", "--", "--windowed")
            self.assertEqual((code, err), (0, ""))
            self.assertIn(" --dry-run ", " " + out)
            self.assertIn("--mods", out)
            self.assertIn(os.path.join(str(port), "mods"), out)
            self.assertIn("--archetype-lookup 1A2B3C0", out)
            self.assertTrue(out.rstrip().endswith("--windowed"))


class ArchiveStagingTests(unittest.TestCase):
    """A ZIP is renamed within staging, while a source directory stays intact."""

    def test_root_manifest_zip_installs_without_copytree(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp, "simple.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("mod.json", json.dumps(MANDY))
                archive.writestr("notes.txt", "test")
            mods = Path(tmp, "mods")
            with mock.patch.object(leonida_mods.shutil, "copytree",
                                   side_effect=AssertionError("ZIP staging must not duplicate the tree")):
                code, out, err = cli("--dir", str(mods), "install", str(archive_path))
            self.assertEqual((code, err), (0, ""))
            self.assertIn("installed simple", out)
            self.assertEqual(Path(mods, "simple", "notes.txt").read_text(), "test")

    def test_nested_manifest_zip_installs_without_copytree(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp, "nested.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("subfolder/mod.json", json.dumps(MANDY))
                archive.writestr("subfolder/notes.txt", "inside")
            mods = Path(tmp, "mods")
            with mock.patch.object(leonida_mods.shutil, "copytree",
                                   side_effect=AssertionError("ZIP staging must not duplicate the tree")):
                code, out, err = cli("--dir", str(mods), "install", str(archive_path))
            self.assertEqual((code, err), (0, ""))
            self.assertIn("installed subfolder", out)
            self.assertEqual(Path(mods, "subfolder", "notes.txt").read_text(), "inside")

    def test_directory_install_preserves_original_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "source", "keep-original")
            write_json(source / "mod.json", MANDY)
            (source / "notes.txt").write_text("original", encoding="utf-8")
            mods = Path(tmp, "mods")
            code, out, err = cli("--dir", str(mods), "install", str(source))
            self.assertEqual((code, err), (0, ""))
            self.assertIn("installed keep-original", out)
            self.assertEqual(read_json(source / "mod.json"), MANDY)
            self.assertEqual((source / "notes.txt").read_text(), "original")
            self.assertEqual(Path(mods, "keep-original", "notes.txt").read_text(), "original")

class SamplesAndGuiTests(unittest.TestCase):
    def test_bundled_basic_mods_are_valid_and_safe(self):
        mods, errors = leonida_mods.load_mods(str(ROOT / "mods"))
        self.assertEqual(errors, [])
        by_folder = {m.folder: m for m in mods}
        self.assertIn("mandy_prison_intro", by_folder)
        self.assertIn("monalisa_player_model", by_folder)
        self.assertIn("lucia_cutscene_model", by_folder)
        self.assertIn("mandy_to_monalisa", by_folder)
        self.assertTrue(by_folder["mandy_prison_intro"].enabled)
        self.assertFalse(by_folder["monalisa_player_model"].enabled)
        self.assertFalse(by_folder["lucia_cutscene_model"].enabled)
        self.assertFalse(by_folder["mandy_to_monalisa"].enabled)
        active = leonida_mods.active_swaps(mods)
        self.assertEqual(active, {0xE472B687: by_folder["mandy_prison_intro"].swaps[0]})

    def test_gui_is_an_explicit_lazy_tkinter_entry_point(self):
        source = (ROOT / "tools" / "leonida_mods.py").read_text(encoding="utf-8")
        self.assertIn('commands.add_parser("gui"', source)
        self.assertIn("import tkinter as tk", source)
        self.assertLess(source.index("def run_gui"), source.index("import tkinter as tk"))
        args = leonida_mods.build_parser().parse_args(["--dir", "mods", "gui"])
        self.assertEqual(args.command, "gui")

    def test_windows_launcher_opens_the_gui(self):
        launcher = (ROOT / "Mods.bat").read_text(encoding="utf-8")
        self.assertIn("leonida_mods.py", launcher)
        self.assertIn(" gui ", launcher + " ")


if __name__ == "__main__":
    unittest.main()
