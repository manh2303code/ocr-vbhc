"""Turn BUILD_MANIFEST.json into the check/skip decisions verify.bat acts on.

verify.bat used to demand every heavy asset unconditionally, so a package built
with --profile runtime — which leaves out 16.5 GB by design — was reported as
"Package co the khong chay dung". The target machine could not tell a reduced
build from an interrupted USB copy, which is the one distinction that matters
when someone is standing in front of an air-gapped desktop.

The decisions live here rather than in the batch file because cmd.exe cannot be
unit tested; verify.bat only consumes the lines this module prints.

Usage (from the package root, where verify.bat has already cd'd):
    runtime\\python.exe verify_plan.py
    PROFILE|runtime
    CHECK|poppler
    SKIP|gguf|Chandra OCR and Qwen3 chat weights. Supply from the asset bundle...
"""
from __future__ import annotations

import sys
from pathlib import Path

# The embedded runtime ships python39._pth, which ignores PYTHONPATH and does
# not put the script's own directory on sys.path. Without this line the import
# below depends on the ".." entry in that file still resolving to the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_bundle


# Every check group verify.bat can be told to skip, in the order it reports
# them. The first four are manifest components; poppler is not (see below).
COMPONENTS = ("model_embedding", "model_reranker", "gguf", "bin", "poppler")

# A package that claims to be complete carries everything, so an undeclared
# component is only excused for a reduced profile.
FULL_PROFILE = "full"

# tools/poppler is kept out of Git like the model directories, but no manifest
# component covers it yet. Its expectation therefore follows the profile, whose
# own description is "runtime and application only; assets supplied separately".
UNDECLARED_NOTES = {
    "poppler": (
        "PDF renderer binaries travel with the asset bundle; PDF import stays "
        "unavailable until tools/poppler is added."
    ),
}
DEFAULT_NOTE = "Not part of this build profile; supply it from the asset bundle."

# Outcome of a single check.
OK = "ok"
SKIPPED = "skipped"
ERROR = "error"

# Exit codes verify.bat returns. A reduced package is valid, so it must not
# share a code with a damaged one — that ambiguity is the bug being fixed. The
# damaged code is fixed rather than "number of errors" so a package with ten
# faults can never be mistaken for a clean runtime build.
EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_REDUCED = 10

# cmd.exe treats these as syntax when a note is echoed from inside an if/else
# block, and '|' is the field separator verify.bat splits the plan on.
_BATCH_UNSAFE = frozenset('|&<>^%()"!\r\n\t')


def load_manifest(package_dir=None):
    """Read the package manifest, or return None when it cannot be trusted.

    A missing manifest means the package predates them; an unreadable one means
    its claims are worthless. Both fall back to verifying everything, because
    the failure mode to avoid is excusing an asset that should have been there.
    """
    if package_dir is None:
        package_dir = Path(__file__).resolve().parent
    try:
        manifest = release_bundle.read_manifest(package_dir)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def profile_of(manifest):
    if not manifest:
        return FULL_PROFILE
    return str(manifest.get("profile") or FULL_PROFILE)


def _declared(manifest, component):
    """The manifest entry for a component, or None if it says nothing about it."""
    if not manifest:
        return None
    components = manifest.get("components")
    if not isinstance(components, dict):
        return None
    entry = components.get(component)
    return entry if isinstance(entry, dict) else None


def _is_required(manifest, component):
    entry = _declared(manifest, component)
    if entry is not None:
        # "present" with nothing on disk is damage, not a build choice. An entry
        # that omits the flag is treated as present so a malformed manifest
        # cannot quietly disable a check.
        return bool(entry.get("present", True))
    return profile_of(manifest) == FULL_PROFILE


def note_for(manifest, component):
    entry = _declared(manifest, component)
    if entry is not None:
        note = str(entry.get("note") or "").strip()
        if note:
            return note
    return UNDECLARED_NOTES.get(component, DEFAULT_NOTE)


def required_checks(manifest):
    """Components whose files must be on disk, or the package is damaged."""
    return [name for name in COMPONENTS if _is_required(manifest, name)]


def skipped_components(manifest):
    """(component, note) pairs the package leaves out on purpose."""
    return [
        (name, note_for(manifest, name))
        for name in COMPONENTS
        if not _is_required(manifest, name)
    ]


def classify_result(manifest, component, found):
    """Grade one check: found, absent by design, or absent and wrong."""
    if found:
        return OK
    return ERROR if _is_required(manifest, component) else SKIPPED


def classify_exit_code(errors, skipped):
    """Damage outranks a declared gap; a declared gap outranks nothing."""
    if errors:
        return EXIT_BROKEN
    return EXIT_REDUCED if skipped else EXIT_OK


def batch_safe(text):
    """Strip what cmd.exe would parse as syntax, and collapse the whitespace."""
    cleaned = "".join(ch for ch in str(text) if ch not in _BATCH_UNSAFE)
    return " ".join(cleaned.split())


def plan_lines(package_dir=None, manifest=None):
    """The pipe-separated plan verify.bat reads with `for /f`."""
    if manifest is None:
        manifest = load_manifest(package_dir)
    lines = ["PROFILE|" + batch_safe(profile_of(manifest))]
    lines.extend("CHECK|" + name for name in required_checks(manifest))
    lines.extend(
        "SKIP|{}|{}".format(name, batch_safe(note))
        for name, note in skipped_components(manifest)
    )
    return lines


def _emit(line):
    # The console runs at chcp 65001, but a note with characters the active
    # code page cannot encode must not abort the plan: a plan that stops
    # halfway would leave verify.bat with a half-applied set of gates.
    try:
        sys.stdout.write(line + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(line.encode("ascii", "replace").decode("ascii") + "\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    package_dir = Path(argv[0]) if argv else None
    try:
        lines = plan_lines(package_dir)
    except Exception:
        # Printing nothing makes verify.bat verify everything, which is the
        # safe direction: a broken planner must never excuse a missing model.
        return EXIT_BROKEN
    for line in lines:
        _emit(line)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
