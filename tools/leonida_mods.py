#!/usr/bin/env python3
"""Install and manage Leonida mods (docs/modding.md).

A mod is a folder containing `mod.json`; `gta6rt` reads those folders from `mods/` next to itself. This tool manages
that folder without touching the game package or the generated work files. It can create a manifest, install a mod
folder or a ZIP/`.leonidamod` package, enable or disable installed mods, pack one for sharing, and start `gta6rt`
with the managed `--mods` folder.

Standard library only. On Windows, `Mods.bat` opens the Tkinter GUI; the same actions stay available from the CLI:

    python -I tools/leonida_mods.py gui
    python -I tools/leonida_mods.py --port "D:\\Games\\Leonida" list
    python -I tools/leonida_mods.py --port "D:\\Games\\Leonida" install mandy.zip
    python -I tools/leonida_mods.py --port "D:\\Games\\Leonida" new lucia_to_mandy \
        --name "Mandy" --game-version 1.0.2.1 --swap player_story000_00=cs_mandy
    python -I tools/leonida_mods.py --port "D:\\Games\\Leonida" run --archetype-lookup 1A2B3C0

`--port` is the built port folder (the one with `gta6rt.exe` and `game_path.txt`). `--dir` names a mods folder
directly, which is useful for the repository's `mods/` examples or tests. With neither, the current folder and the
usual `Documents/Leonida` and `~/Leonida` build outputs are tried.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import SimpleNamespace

EXPECTED_EXE = "game_scarlett_master.exe"
MANIFEST = "mod.json"
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


class ModsError(Exception):
    """A user-facing failure: bad input, an unsafe package or a filesystem problem."""


@dataclass
class Swap:
    from_name: str
    to_name: str
    from_id: int
    to_id: int
    when: str = "always"


@dataclass
class ModInfo:
    folder: str
    path: str
    name: str
    enabled: bool
    game_version: str
    swaps: list[Swap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def joaat(name: str) -> int:
    """RAGE's joaat over the name's UTF-8 bytes, ASCII-lowercased like the C++ runtime."""
    h = 0
    for byte in name.encode("utf-8"):
        if 0x41 <= byte <= 0x5A:
            byte += 0x20
        h = (h + byte) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    return (h + (h << 15)) & 0xFFFFFFFF


def parse_model_id(text: str) -> int:
    if not isinstance(text, str) or not text:
        raise ValueError("empty model name")
    if text.startswith(("0x", "0X")):
        digits = text[2:]
        if not digits:
            raise ValueError("0x has no hash digits")
        if len(digits) > 8 or any(c not in "0123456789abcdefABCDEF" for c in digits):
            raise ValueError(f"{text} is not a 32-bit hash")
        return int(digits, 16)
    return joaat(text)


def validate_manifest(data: object, folder: str, path: str = "") -> ModInfo:
    if not isinstance(data, dict):
        raise ModsError(f"{folder}: {MANIFEST} is not a JSON object")
    mod = ModInfo(folder=folder, path=path, name=folder, enabled=True, game_version="", raw=data)
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        mod.name = name
    elif name is not None:
        mod.warnings.append('"name" is not a string; the folder name is shown instead')
    version = data.get("game_version")
    if isinstance(version, str):
        mod.game_version = version
    elif version is not None:
        mod.warnings.append('"game_version" is not a string; gta6rt ignores it')
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ModsError(f'{folder}: "enabled" must be true or false')
    mod.enabled = enabled
    swaps = data.get("archetype_swaps")
    if swaps is None:
        mod.warnings.append("no archetype_swaps; this mod currently changes nothing")
        return mod
    if not isinstance(swaps, list):
        raise ModsError(f'{folder}: "archetype_swaps" must be a list')
    seen = {}
    for index, item in enumerate(swaps):
        label = f"archetype_swaps[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("from"), str) or \
                not isinstance(item.get("to"), str):
            raise ModsError(f'{folder}: {label} needs "from" and "to" model names')
        swap = Swap(item["from"], item["to"], 0, 0)
        try:
            swap.from_id = parse_model_id(swap.from_name)
            swap.to_id = parse_model_id(swap.to_name)
        except ValueError as exc:
            raise ModsError(f"{folder}: {label} {swap.from_name} -> {swap.to_name}: {exc}") from exc
        when = item.get("when", "always")
        if when != "always":
            raise ModsError(f'{folder}: {label} "when" can only be "always" until a game hook provides mission '
                            "state (docs/modding.md)")
        if swap.from_id == swap.to_id:
            raise ModsError(f"{folder}: {label} swaps a model with itself")
        if swap.from_id in seen:
            mod.warnings.append(f'{label} repeats {seen[swap.from_id]}; the later swap wins')
        seen[swap.from_id] = label
        mod.swaps.append(swap)
    if not mod.swaps:
        mod.warnings.append("archetype_swaps is empty; this mod currently changes nothing")
    return mod


def read_manifest(path: str, folder: str) -> ModInfo:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except UnicodeDecodeError as exc:
        raise ModsError(f"{folder}: {MANIFEST} is not UTF-8 ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise ModsError(f"{folder}: {MANIFEST} is not valid JSON ({exc.msg} at line {exc.lineno}, "
                        f"column {exc.colno})") from exc
    except OSError as exc:
        raise ModsError(f"{folder}: cannot read {MANIFEST}: {exc}") from exc
    return validate_manifest(data, folder, path)


def load_mods(mods_dir: str) -> tuple[list[ModInfo], list[str]]:
    mods, errors = [], []
    if not os.path.isdir(mods_dir):
        return mods, errors
    try:
        names = sorted(os.listdir(mods_dir))
    except OSError as exc:
        raise ModsError(f"cannot list {mods_dir}: {exc}") from exc
    for folder in names:
        path = os.path.join(mods_dir, folder)
        manifest = os.path.join(path, MANIFEST)
        if not os.path.isdir(path) or not os.path.isfile(manifest):
            continue
        try:
            mods.append(read_manifest(manifest, folder))
        except ModsError as exc:
            errors.append(str(exc))
    return mods, errors


def active_swaps(mods: list[ModInfo]) -> dict[int, Swap]:
    active = {}
    for mod in mods:
        if mod.enabled:
            for swap in mod.swaps:
                active[swap.from_id] = swap
    return active


def default_port() -> str:
    candidates = [os.getcwd(),
                  os.path.join(os.path.expanduser("~"), "Documents", "Leonida"),
                  os.path.join(os.path.expanduser("~"), "Leonida")]
    for candidate in candidates:
        if any(os.path.isfile(os.path.join(candidate, name))
               for name in ("gta6rt.exe", "gta6rt.sh", "game_path.txt")):
            return candidate
    raise ModsError("no built port found: pass --port <folder with gta6rt.exe> or --dir <mods folder>")


def mods_dir_for(args: argparse.Namespace) -> str:
    if args.port and args.mods_dir:
        raise ModsError("use either --port or --dir, not both")
    if args.mods_dir:
        return os.path.abspath(args.mods_dir)
    port = os.path.abspath(args.port) if args.port else os.path.abspath(default_port())
    if not os.path.isdir(port):
        raise ModsError(f"{port} is not a built port folder")
    return os.path.join(port, "mods")


def port_for(args: argparse.Namespace) -> str:
    if args.port:
        return os.path.abspath(args.port)
    if args.mods_dir:
        parent = os.path.dirname(os.path.abspath(args.mods_dir))
        if os.path.basename(os.path.abspath(args.mods_dir)).lower() == "mods" and \
                any(os.path.isfile(os.path.join(parent, name))
                    for name in ("gta6rt.exe", "gta6rt.sh", "game_path.txt")):
            return parent
        raise ModsError("--dir does not name a built port's mods folder; pass --port for run")
    return os.path.abspath(default_port())


def safe_folder_name(text: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ord(c) < 32 or c in bad else c for c in text.strip())
    cleaned = " ".join(cleaned.split()).rstrip(" .")
    if not cleaned or cleaned in (".", "..") or cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = "mod"
    cleaned = cleaned[:80].rstrip(" .")
    return cleaned or "mod"


def simple_selector(name: str) -> str:
    if not name or name in (".", "..") or os.path.basename(name) != name:
        raise ModsError(f"{name!r} is not an installed mod folder name")
    return name


def is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(directory)]) == \
            os.path.abspath(directory)
    except ValueError:
        return False


def find_manifest_root(directory: str) -> str:
    if os.path.isfile(os.path.join(directory, MANIFEST)):
        return directory
    roots = []
    try:
        children = sorted(os.listdir(directory))
    except OSError as exc:
        raise ModsError(f"cannot list {directory}: {exc}") from exc
    for child in children:
        path = os.path.join(directory, child)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, MANIFEST)):
            roots.append(path)
    if len(roots) == 1:
        return roots[0]
    if roots:
        raise ModsError(f"{directory} has more than one top-level {MANIFEST}; keep one mod folder per package")
    raise ModsError(f"{directory} has no {MANIFEST} at its top level or in one top-level folder")


def reject_symlinks(root: str) -> None:
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = os.path.join(base, name)
            if os.path.islink(path):
                raise ModsError(f"{path} is a symbolic link; mod packages must contain regular files")


def zip_parts(name: str) -> tuple[str, ...]:
    if not name or "\\" in name or "\x00" in name:
        raise ModsError(f"unsafe ZIP member name {name!r}")
    parts = PurePosixPath(name).parts
    if not parts or parts[0] == "/" or ".." in parts or "." in parts:
        raise ModsError(f"unsafe ZIP member name {name!r}")
    for part in parts:
        if safe_folder_name(part) != part:
            raise ModsError(f"unsafe ZIP member name {name!r}")
    return parts


def extract_zip(source: str, destination: str) -> None:
    os.makedirs(destination, exist_ok=True)
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise ModsError(f"{source} is not a readable ZIP package") from exc
    with archive:
        for info in archive.infolist():
            parts = zip_parts(info.filename)
            target = os.path.join(destination, *parts)
            if not is_within(target, destination):
                raise ModsError(f"unsafe ZIP member name {info.filename!r}")
            mode = info.external_attr >> 16
            if mode and stat.S_IFMT(mode) == stat.S_IFLNK:
                raise ModsError(f"ZIP member {info.filename!r} is a symbolic link")
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def write_manifest(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def set_enabled_in_manifest(manifest_path: str, enabled: bool) -> None:
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModsError(f"cannot update {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModsError(f"{manifest_path} is not a JSON object")
    data["enabled"] = enabled
    write_manifest(manifest_path, data)


def publish_mod(staged: str, target: str, force: bool) -> None:
    backup = None
    if os.path.lexists(target):
        if not force:
            raise ModsError(f"{os.path.basename(target)} is already installed; pass --force to replace it")
        backup = os.path.join(os.path.dirname(target),
                              f".{os.path.basename(target)}.replaced-{secrets.token_hex(4)}")
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except OSError:
        if backup is not None:
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def stage_source(source: str, work: str) -> tuple[str, str, str]:
    staged = os.path.join(work, "mod")
    source = os.path.abspath(source)
    archive_source = False
    if os.path.isdir(source):
        root = find_manifest_root(source)
        label = os.path.basename(root.rstrip(os.sep))
        reject_symlinks(root)
    elif os.path.isfile(source) and zipfile.is_zipfile(source):
        archive_source = True
        unpacked = os.path.join(work, "package")
        extract_zip(source, unpacked)
        root = find_manifest_root(unpacked)
        label = os.path.splitext(os.path.basename(source))[0] if os.path.abspath(root) == \
            os.path.abspath(unpacked) else os.path.basename(root)
        reject_symlinks(root)
    elif os.path.isfile(source):
        raise ModsError(f"{source} is not a mod folder or ZIP/.leonidamod package")
    else:
        raise ModsError(f"{source} does not exist")
    try:
        if archive_source:
            # The archive was unpacked inside `work`, on the same filesystem as
            # `staged`. Renaming avoids duplicating every extracted byte.
            os.replace(root, staged)
        else:
            # External source folders must be copied: never move user files.
            shutil.copytree(root, staged, symlinks=True)
    except OSError as exc:
        raise ModsError(f"cannot stage {root}: {exc}") from exc
    return staged, root, label


def install_mod(source: str, mods_dir: str, name: str | None = None, force: bool = False,
                enabled: bool | None = None) -> tuple[ModInfo, str]:
    os.makedirs(mods_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".leonida-install-", dir=mods_dir)
    try:
        staged, root, package_label = stage_source(source, work)
        if is_within(root, mods_dir) and not is_within(root, work):
            raise ModsError(f"{root} is already inside the managed mods folder")
        label = safe_folder_name(name or package_label)
        manifest_path = os.path.join(staged, MANIFEST)
        mod = read_manifest(manifest_path, label)
        if enabled is not None:
            set_enabled_in_manifest(manifest_path, enabled)
            mod.enabled = enabled
        target = os.path.join(mods_dir, label)
        publish_mod(staged, target, force)
        mod.folder, mod.path = label, os.path.join(target, MANIFEST)
        return mod, target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def create_mod(folder: str, mods_dir: str, display_name: str | None, game_version: str | None,
               swap_specs: list[str], disabled: bool, force: bool) -> tuple[ModInfo, str]:
    if not swap_specs:
        raise ModsError("new needs at least one --swap FROM=TO")
    label = safe_folder_name(folder)
    swaps = []
    for spec in swap_specs:
        if "=" not in spec:
            raise ModsError(f"--swap {spec!r} must be FROM=TO")
        from_name, to_name = spec.split("=", 1)
        swaps.append({"from": from_name, "to": to_name, "when": "always"})
    data = {"name": display_name or label,
            "enabled": not disabled,
            "archetype_swaps": swaps}
    if game_version:
        data["game_version"] = game_version
    mod = validate_manifest(data, label)
    os.makedirs(mods_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".leonida-new-", dir=mods_dir)
    try:
        staged = os.path.join(work, "mod")
        os.makedirs(staged)
        write_manifest(os.path.join(staged, MANIFEST), data)
        target = os.path.join(mods_dir, label)
        publish_mod(staged, target, force)
        return mod, target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def find_mod_folder(mods_dir: str, selector: str) -> str:
    wanted = simple_selector(selector)
    try:
        names = [n for n in os.listdir(mods_dir) if os.path.isdir(os.path.join(mods_dir, n))]
    except OSError as exc:
        raise ModsError(f"cannot list {mods_dir}: {exc}") from exc
    matches = [n for n in names if n == wanted]
    if not matches:
        folded = wanted.casefold()
        matches = [n for n in names if n.casefold() == folded]
    if len(matches) > 1:
        raise ModsError(f"{wanted} names more than one mod folder")
    if not matches:
        raise ModsError(f"{wanted} is not installed in {mods_dir}")
    path = os.path.join(mods_dir, matches[0])
    if not os.path.isfile(os.path.join(path, MANIFEST)):
        raise ModsError(f"{matches[0]} has no {MANIFEST}; refusing to manage it")
    return path


def set_mod_enabled(mods_dir: str, selector: str, enabled: bool) -> ModInfo:
    path = find_mod_folder(mods_dir, selector)
    manifest = os.path.join(path, MANIFEST)
    set_enabled_in_manifest(manifest, enabled)
    return read_manifest(manifest, os.path.basename(path))


def uninstall_mod(mods_dir: str, selector: str, yes: bool) -> str:
    path = find_mod_folder(mods_dir, selector)
    if os.path.islink(path) or not is_within(path, mods_dir):
        raise ModsError(f"{path} is not a regular folder inside {mods_dir}")
    if not yes:
        if not sys.stdin.isatty():
            raise ModsError("uninstall needs --yes when input is not interactive")
        answer = input(f"Remove {os.path.basename(path)} from {mods_dir}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise ModsError("uninstall cancelled")
    shutil.rmtree(path)
    return path


def pack_mod(mods_dir: str, selector: str, output: str, force: bool) -> str:
    path = find_mod_folder(mods_dir, selector)
    reject_symlinks(path)
    output = os.path.abspath(output)
    if is_within(output, path):
        raise ModsError("the package output cannot be inside the mod being packed")
    if os.path.exists(output) and not force:
        raise ModsError(f"{output} exists; pass --force to replace it")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    folder = os.path.basename(path)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for base, dirs, files in os.walk(path, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(base, name)
                member = PurePosixPath(folder, os.path.relpath(full, path).replace(os.sep, "/"))
                archive.write(full, member.as_posix())
    return output


def runtime_executable(port: str) -> str:
    names = ("gta6rt.exe",) if os.name == "nt" else ("gta6rt.sh", "gta6rt.exe")
    for name in names:
        path = os.path.join(port, name)
        if os.path.isfile(path):
            return path
    raise ModsError(f"{port} has no gta6rt executable; build the runtime stage first")


def runtime_command(args: argparse.Namespace, mods_dir: str) -> list[str]:
    port = port_for(args)
    game = os.path.abspath(args.game) if args.game else None
    if game is None:
        game_path = os.path.join(port, "game_path.txt")
        try:
            with open(game_path, encoding="utf-8") as f:
                game = f.read().strip()
        except OSError as exc:
            raise ModsError(f"no --game given and {game_path} is unreadable: {exc}") from exc
        if not game:
            raise ModsError(f"{game_path} is empty; pass --game")
    image = os.path.abspath(args.image) if args.image else os.path.join(game, EXPECTED_EXE)
    if not os.path.isfile(image):
        raise ModsError(f"{image} is not present; pass --image for a stand-in executable")
    mode = "--dry-run" if args.dry_run else "--init" if args.init else "--run"
    command = [runtime_executable(port), mode, image, "--game-dir", game, "--mods", mods_dir]
    if args.archetype_lookup:
        command += ["--archetype-lookup", args.archetype_lookup]
    if args.trace_d3d12:
        command.append("--trace-d3d12")
    if args.trace:
        command += ["--trace", args.trace]
    command += args.game_args
    return command


def run_args(port: str, game: str | None = None, image: str | None = None, mode: str = "--run",
             archetype_lookup: str | None = None, game_args: list[str] | None = None) -> SimpleNamespace:
    """The attribute set `runtime_command` reads, for callers that do not come through argparse."""
    return SimpleNamespace(port=port, mods_dir=None, game=game, image=image,
                           dry_run=mode == "--dry-run", init=mode == "--init",
                           archetype_lookup=archetype_lookup, trace_d3d12=False, trace=None,
                           game_args=game_args or [])


def port_from_mods_dir(mods_dir: str | None) -> str | None:
    """The built-port parent of a `<port>/mods` folder, when it has the runtime's marker files."""
    if not mods_dir:
        return None
    path = os.path.abspath(mods_dir)
    if os.path.basename(path).lower() != "mods":
        return None
    parent = os.path.dirname(path)
    if any(os.path.isfile(os.path.join(parent, name))
           for name in ("gta6rt.exe", "gta6rt.sh", "game_path.txt")):
        return parent
    return None


def print_report(mods_dir: str, mods: list[ModInfo], errors: list[str]) -> None:
    print(f"mods dir: {mods_dir}")
    if not mods and not errors:
        print("no mods installed")
        return
    previous = {}
    for mod in mods:
        state = "enabled" if mod.enabled else "disabled"
        version = f", game {mod.game_version}" if mod.game_version else ""
        swaps = f"{len(mod.swaps)} swap" + ("" if len(mod.swaps) == 1 else "s")
        print(f"{mod.folder}: {mod.name} [{state}; {swaps}{version}]")
        for warning in mod.warnings:
            print(f"  warning: {warning}")
        for swap in mod.swaps:
            conflict = ""
            if mod.enabled and swap.from_id in previous:
                conflict = f"  (overrides {previous[swap.from_id]})"
            print(f"  archetype {swap.from_name} (0x{swap.from_id:08X}) -> {swap.to_name} "
                  f"(0x{swap.to_id:08X}){conflict}")
            if mod.enabled:
                previous[swap.from_id] = mod.folder
    for error in errors:
        print(f"skipped {error}")


def report_json(mods_dir: str, mods: list[ModInfo], errors: list[str]) -> str:
    data = {
        "mods_dir": mods_dir,
        "mods": [{
            "folder": m.folder,
            "name": m.name,
            "enabled": m.enabled,
            "game_version": m.game_version,
            "warnings": m.warnings,
            "archetype_swaps": [{
                "from": s.from_name, "to": s.to_name,
                "from_hash": f"0x{s.from_id:08X}", "to_hash": f"0x{s.to_id:08X}",
                "when": s.when,
            } for s in m.swaps],
        } for m in mods],
        "errors": errors,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def run_gui(args: argparse.Namespace, initial_mods_dir: str | None) -> int:
    """Open the Tkinter mod-manager window. Imported here so the CLI and tests need no display."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise ModsError("Tkinter is not installed; on Windows install Python from python.org with Tcl/Tk") \
            from exc

    class ModsApp:
        def __init__(self, root):
            self.root = root
            self.mods = {}
            self.errors = []
            port = args.port or port_from_mods_dir(initial_mods_dir) or ""
            self.port_var = tk.StringVar(value=port)
            self.mods_var = tk.StringVar(value=initial_mods_dir or (os.path.join(port, "mods") if port else ""))
            self.game_var = tk.StringVar(value=self.read_game_path(port))
            self.image_var = tk.StringVar()
            self.lookup_var = tk.StringVar()
            self.new_folder_var = tk.StringVar()
            self.new_name_var = tk.StringVar()
            self.new_version_var = tk.StringVar(value="1.0.2.1")
            self.new_from_var = tk.StringVar(value="player_story000_00")
            self.new_to_var = tk.StringVar(value="cs_mandy")
            self.new_disabled_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar()
            self.build_layout()
            if self.mods_var.get():
                self.refresh()
            else:
                self.status("Choose the built Leonida port folder")

        def read_game_path(self, port):
            if not port:
                return ""
            try:
                with open(os.path.join(port, "game_path.txt"), encoding="utf-8") as f:
                    return f.read().strip()
            except OSError:
                return ""

        def build_layout(self):
            self.root.title("Leonida Mod Manager")
            self.root.minsize(860, 620)
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            outer = ttk.Frame(self.root, padding=10)
            outer.grid(row=0, column=0, sticky="nsew")
            outer.columnconfigure(0, weight=1)
            outer.rowconfigure(1, weight=1)
            outer.rowconfigure(4, weight=1)

            paths = ttk.LabelFrame(outer, text="Folders", padding=8)
            paths.grid(row=0, column=0, sticky="ew")
            paths.columnconfigure(1, weight=1)
            ttk.Label(paths, text="Port").grid(row=0, column=0, sticky="w")
            ttk.Entry(paths, textvariable=self.port_var).grid(row=0, column=1, sticky="ew", padx=6)
            ttk.Button(paths, text="Browse...", command=self.choose_port).grid(row=0, column=2)
            ttk.Label(paths, text="Mods").grid(row=1, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(paths, textvariable=self.mods_var).grid(row=1, column=1, sticky="ew", padx=6,
                                                              pady=(6, 0))
            mods_buttons = ttk.Frame(paths)
            mods_buttons.grid(row=1, column=2, pady=(6, 0))
            ttk.Button(mods_buttons, text="Browse...", command=self.choose_mods).pack(side="left")
            ttk.Button(mods_buttons, text="Open", command=lambda: self.guard(self.open_mods)).pack(
                side="left", padx=(6, 0))

            installed = ttk.LabelFrame(outer, text="Installed mods", padding=8)
            installed.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
            installed.columnconfigure(0, weight=1)
            installed.rowconfigure(0, weight=1)
            columns = ("folder", "name", "state", "swaps", "version")
            self.tree = ttk.Treeview(installed, columns=columns, show="headings", height=9,
                                     selectmode="browse")
            for column, title, width in (("folder", "Folder", 190), ("name", "Name", 250),
                                         ("state", "State", 75), ("swaps", "Swaps", 60),
                                         ("version", "Game", 90)):
                self.tree.heading(column, text=title)
                self.tree.column(column, width=width, anchor="w")
            scroll = ttk.Scrollbar(installed, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=scroll.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            scroll.grid(row=0, column=1, sticky="ns")
            actions = ttk.Frame(installed)
            actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
            for text, command in (("Refresh", self.refresh),
                                  ("Install ZIP", self.install_package),
                                  ("Install folder", self.install_folder),
                                  ("Enable/disable", self.toggle_selected),
                                  ("Pack", self.pack_selected),
                                  ("Uninstall", self.uninstall_selected),
                                  ("Check", self.check_mods)):
                ttk.Button(actions, text=text, command=lambda c=command: self.guard(c)).pack(side="left",
                                                                                             padx=(0, 6))

            bottom = ttk.Frame(outer)
            bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
            bottom.columnconfigure(0, weight=1)
            bottom.columnconfigure(1, weight=1)

            creator = ttk.LabelFrame(bottom, text="New archetype swap", padding=8)
            creator.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
            for col in range(4):
                creator.columnconfigure(col, weight=1)
            fields = (("Folder", self.new_folder_var), ("Name", self.new_name_var),
                      ("Game version", self.new_version_var), ("From", self.new_from_var),
                      ("To", self.new_to_var))
            for row, (label, var) in enumerate(fields):
                ttk.Label(creator, text=label).grid(row=row, column=0, sticky="w", pady=2)
                ttk.Entry(creator, textvariable=var).grid(row=row, column=1, columnspan=3, sticky="ew",
                                                          padx=6, pady=2)
            ttk.Checkbutton(creator, text="Install disabled", variable=self.new_disabled_var).grid(
                row=5, column=1, sticky="w", pady=4)
            ttk.Button(creator, text="Create mod", command=lambda: self.guard(self.create_new)).grid(
                row=5, column=3, sticky="e", pady=4)

            launcher = ttk.LabelFrame(bottom, text="Run", padding=8)
            launcher.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
            launcher.columnconfigure(1, weight=1)
            ttk.Label(launcher, text="Game folder").grid(row=0, column=0, sticky="w")
            ttk.Entry(launcher, textvariable=self.game_var).grid(row=0, column=1, sticky="ew", padx=6)
            ttk.Button(launcher, text="Browse...",
                       command=lambda: self.choose_directory(self.game_var, "Choose the game package folder")).grid(
                           row=0, column=2)
            ttk.Label(launcher, text="Image").grid(row=1, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(launcher, textvariable=self.image_var).grid(row=1, column=1, sticky="ew", padx=6,
                                                                  pady=(6, 0))
            ttk.Button(launcher, text="Browse...",
                       command=lambda: self.choose_file(self.image_var, "Choose an image to load")).grid(
                           row=1, column=2, pady=(6, 0))
            ttk.Label(launcher, text="Lookup RVA").grid(row=2, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(launcher, textvariable=self.lookup_var).grid(row=2, column=1, sticky="ew", padx=6,
                                                                   pady=(6, 0))
            run_buttons = ttk.Frame(launcher)
            run_buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
            for text, mode, print_only in (("Run", "--run", False), ("Dry run", "--dry-run", False),
                                           ("Command", "--run", True)):
                ttk.Button(run_buttons, text=text,
                           command=lambda m=mode, p=print_only: self.guard(self.launch, m, p)).pack(
                               side="left", padx=(0, 6))

            log_frame = ttk.LabelFrame(outer, text="Log", padding=8)
            log_frame.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
            log_frame.columnconfigure(0, weight=1)
            log_frame.rowconfigure(0, weight=1)
            self.log_text = tk.Text(log_frame, height=7, wrap="word", state="disabled")
            log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
            self.log_text.configure(yscrollcommand=log_scroll.set)
            self.log_text.grid(row=0, column=0, sticky="nsew")
            log_scroll.grid(row=0, column=1, sticky="ns")
            ttk.Label(outer, textvariable=self.status_var).grid(row=5, column=0, sticky="ew",
                                                                pady=(6, 0))

        def log(self, text):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text.rstrip() + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def status(self, text):
            self.status_var.set(text)

        def guard(self, fn, *argv):
            try:
                fn(*argv)
            except Exception as exc:
                self.log(f"ERROR: {exc}")
                messagebox.showerror("Leonida Mod Manager", str(exc))

        def mods_dir(self):
            path = self.mods_var.get().strip()
            if not path:
                raise ModsError("choose the port or mods folder first")
            return os.path.abspath(path)

        def choose_port(self):
            folder = filedialog.askdirectory(title="Choose the built Leonida port folder")
            if folder:
                self.port_var.set(folder)
                self.mods_var.set(os.path.join(folder, "mods"))
                game = self.read_game_path(folder)
                if game:
                    self.game_var.set(game)
                self.guard(self.refresh)

        def choose_mods(self):
            folder = filedialog.askdirectory(title="Choose the mods folder")
            if folder:
                self.mods_var.set(folder)
                port = port_from_mods_dir(folder)
                if port:
                    self.port_var.set(port)
                self.guard(self.refresh)

        def choose_directory(self, variable, title):
            folder = filedialog.askdirectory(title=title)
            if folder:
                variable.set(folder)

        def choose_file(self, variable, title):
            path = filedialog.askopenfilename(title=title)
            if path:
                variable.set(path)

        def refresh(self):
            path = self.mods_var.get().strip()
            self.tree.delete(*self.tree.get_children())
            self.mods = {}
            if not path:
                self.status("Choose the built Leonida port folder")
                return
            self.mods_list, self.errors = load_mods(path)
            for mod in self.mods_list:
                self.mods[mod.folder] = mod
                self.tree.insert("", "end", iid=mod.folder,
                                 values=(mod.folder, mod.name, "enabled" if mod.enabled else "disabled",
                                        len(mod.swaps), mod.game_version))
            active = len(active_swaps(self.mods_list))
            self.status(f"{len(self.mods_list)} mod(s), {active} active swap(s), "
                        f"{len(self.errors)} manifest error(s)")
            for error in self.errors:
                self.log(f"skipped {error}")

        def selected(self):
            selection = self.tree.selection()
            if not selection:
                raise ModsError("select a mod first")
            folder = selection[0]
            if folder not in self.mods:
                raise ModsError("select an installed mod first")
            return self.mods[folder]

        def open_mods(self):
            path = self.mods_dir()
            os.makedirs(path, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            self.log(f"opened {path}")

        def install_source(self, source):
            try:
                mod, target = install_mod(source, self.mods_dir())
            except ModsError as exc:
                if "already installed" not in str(exc) or not messagebox.askyesno(
                        "Replace mod?", f"{exc}\nReplace it?"):
                    raise
                mod, target = install_mod(source, self.mods_dir(), force=True)
            self.log(f"installed {mod.folder}: {mod.name} -> {target}")
            self.refresh()

        def install_package(self):
            source = filedialog.askopenfilename(
                title="Install a mod package",
                filetypes=(("Leonida mod packages", "*.leonidamod *.zip"), ("All files", "*.*")))
            if source:
                self.install_source(source)

        def install_folder(self):
            source = filedialog.askdirectory(title="Install a mod folder")
            if source:
                self.install_source(source)

        def create_new(self):
            folder = self.new_folder_var.get().strip()
            if not folder:
                raise ModsError("folder is required")
            spec = f"{self.new_from_var.get().strip()}={self.new_to_var.get().strip()}"
            try:
                mod, target = create_mod(folder, self.mods_dir(), self.new_name_var.get().strip() or None,
                                         self.new_version_var.get().strip() or None, [spec],
                                         self.new_disabled_var.get(), False)
            except ModsError as exc:
                if "already installed" not in str(exc) or not messagebox.askyesno(
                        "Replace mod?", f"{exc}\nReplace it?"):
                    raise
                mod, target = create_mod(folder, self.mods_dir(), self.new_name_var.get().strip() or None,
                                         self.new_version_var.get().strip() or None, [spec],
                                         self.new_disabled_var.get(), True)
            self.log(f"created {mod.folder}: {mod.name} -> {target}")
            self.refresh()

        def toggle_selected(self):
            mod = self.selected()
            updated = set_mod_enabled(self.mods_dir(), mod.folder, not mod.enabled)
            self.log(f"{updated.folder}: {'enabled' if updated.enabled else 'disabled'}")
            self.refresh()

        def pack_selected(self):
            mod = self.selected()
            output = filedialog.asksaveasfilename(
                title="Pack mod", defaultextension=".leonidamod", initialfile=mod.folder + ".leonidamod",
                filetypes=(("Leonida mod package", "*.leonidamod"), ("ZIP", "*.zip"), ("All files", "*.*")))
            if output:
                replace = os.path.exists(output)
                if replace and not messagebox.askyesno("Replace package?", f"{output} exists. Replace it?"):
                    return
                self.log(f"packed {pack_mod(self.mods_dir(), mod.folder, output, replace)}")

        def uninstall_selected(self):
            mod = self.selected()
            if not messagebox.askyesno("Uninstall mod?", f"Remove {mod.folder}?"):
                return
            self.log(f"removed {uninstall_mod(self.mods_dir(), mod.folder, True)}")
            self.refresh()

        def check_mods(self):
            mods, errors = load_mods(self.mods_dir())
            if errors:
                messagebox.showerror("Mod check", "\n".join(errors))
            else:
                messagebox.showinfo("Mod check", f"{len(mods)} mod(s) checked; no manifest errors")
            self.refresh()

        def launch(self, mode, print_only):
            port = self.port_var.get().strip()
            if not port:
                raise ModsError("choose the built port folder first")
            command_args = run_args(port, self.game_var.get().strip() or None,
                                    self.image_var.get().strip() or None, mode,
                                    self.lookup_var.get().strip() or None)
            command = runtime_command(command_args, self.mods_dir())
            if print_only:
                self.log(shlex.join(command))
                return
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            process = subprocess.Popen(command, cwd=port, creationflags=flags)
            self.log(f"started {mode} as process {process.pid}: {shlex.join(command)}")

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise ModsError(f"cannot open the GUI: {exc}") from exc
    ModsApp(root)
    root.mainloop()
    return 0


def add_target_args(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--port", help="built port folder containing gta6rt (its mods/ folder is managed)")
    target.add_argument("--dir", dest="mods_dir", help="manage this mods folder directly")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_target_args(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("gui", help="open the Windows mod-manager window")
    commands.add_parser("path", help="print the managed mods folder")

    listing = commands.add_parser("list", help="list installed mods")
    listing.add_argument("--json", action="store_true", help="print machine-readable JSON")

    check = commands.add_parser("check", help="validate installed manifests")
    check.add_argument("--json", action="store_true", help="print machine-readable JSON")

    install = commands.add_parser("install", help="install a mod folder or ZIP/.leonidamod package")
    install.add_argument("source")
    install.add_argument("--name", help="installed folder name")
    install.add_argument("--force", action="store_true", help="replace an installed mod with the same folder")
    enabled = install.add_mutually_exclusive_group()
    enabled.add_argument("--enable", dest="set_enabled", action="store_true", default=None,
                         help="mark the installed manifest enabled")
    enabled.add_argument("--disable", dest="set_enabled", action="store_false",
                         help="mark the installed manifest disabled")

    new = commands.add_parser("new", help="create an archetype-swap mod")
    new.add_argument("folder", help="installed folder name")
    new.add_argument("--name", help="display name (default: folder)")
    new.add_argument("--game-version", help="package version the mod was made for")
    new.add_argument("--swap", action="append", default=[], metavar="FROM=TO",
                     help="archetype swap; repeat for more than one")
    new.add_argument("--disabled", action="store_true", help="install it disabled")
    new.add_argument("--force", action="store_true", help="replace an installed mod with the same folder")

    for command, enabled_state in (("enable", True), ("disable", False)):
        sub = commands.add_parser(command, help=f"mark a mod {command}d")
        sub.add_argument("mod")
        sub.set_defaults(enabled_state=enabled_state)

    uninstall = commands.add_parser("uninstall", help="remove an installed mod folder")
    uninstall.add_argument("mod")
    uninstall.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    pack = commands.add_parser("pack", help="write an installed mod as a ZIP package")
    pack.add_argument("mod")
    pack.add_argument("--out", required=True, help="output .zip or .leonidamod file")
    pack.add_argument("--force", action="store_true", help="replace the output file")

    run = commands.add_parser("run", help="start gta6rt with this mods folder")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="map the image without running game code")
    mode.add_argument("--init", action="store_true", help="run module initialisation but not the entry point")
    run.add_argument("--game", help="game package folder (default: game_path.txt)")
    run.add_argument("--image", help=f"image to load (default: <game>/{EXPECTED_EXE})")
    run.add_argument("--archetype-lookup", metavar="RVA[,ARG]",
                     help="hook the measured archetype lookup for this executable")
    run.add_argument("--trace-d3d12", action="store_true", help="log D3D12 calls")
    run.add_argument("--trace", help="write the startup JSONL trace here")
    run.add_argument("--print", dest="print_command", action="store_true",
                     help="print the command without starting it")
    run.add_argument("game_args", nargs=argparse.REMAINDER,
                     help="arguments after -- are passed to the game")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "gui":
            try:
                mods_dir = mods_dir_for(args)
            except ModsError:
                if args.port or args.mods_dir:
                    raise
                mods_dir = None
            return run_gui(args, mods_dir)
        mods_dir = mods_dir_for(args)
        if args.command == "path":
            print(mods_dir)
        elif args.command in ("list", "check"):
            mods, errors = load_mods(mods_dir)
            if args.json:
                print(report_json(mods_dir, mods, errors))
            else:
                print_report(mods_dir, mods, errors)
            if args.command == "check" and errors:
                return 1
        elif args.command == "install":
            mod, target = install_mod(args.source, mods_dir, args.name, args.force, args.set_enabled)
            print(f"installed {mod.folder}: {mod.name} -> {target}")
            for warning in mod.warnings:
                print(f"warning: {warning}")
        elif args.command == "new":
            mod, target = create_mod(args.folder, mods_dir, args.name, args.game_version, args.swap,
                                     args.disabled, args.force)
            print(f"created {mod.folder}: {mod.name} -> {target}")
            for warning in mod.warnings:
                print(f"warning: {warning}")
        elif args.command in ("enable", "disable"):
            mod = set_mod_enabled(mods_dir, args.mod, args.enabled_state)
            print(f"{mod.folder}: {'enabled' if mod.enabled else 'disabled'}")
        elif args.command == "uninstall":
            print(f"removed {uninstall_mod(mods_dir, args.mod, args.yes)}")
        elif args.command == "pack":
            print(f"packed {pack_mod(mods_dir, args.mod, args.out, args.force)}")
        elif args.command == "run":
            game_args = list(args.game_args)
            if game_args[:1] == ["--"]:
                game_args = game_args[1:]
            args.game_args = game_args
            command = runtime_command(args, mods_dir)
            if args.print_command:
                print(shlex.join(command))
            else:
                return subprocess.call(command)
        else:
            raise ModsError(f"unknown command {args.command}")
    except (ModsError, OSError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    sys.exit(main())
