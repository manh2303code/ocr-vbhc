"""Apply an application-layer patch on an air-gapped machine.

The target has no network, no technical staff, and runs a live document
workflow. Three consequences shape this module:

- Everything is checked before anything is written. A refused update costs a
  second trip; a half-written one stops the office.
- The previous application layer is kept, and restored if the replacement
  fails partway through.
- User data is never in scope. Only files SHA256SUMS lists are replaced, so
  database/, config/, logs/ and output/ are untouched.

The patch itself is small — the application layer is about 5 MB against 23 GB
for the whole package — which is the point of versioning the layers separately.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections import namedtuple
from pathlib import Path, PurePosixPath

import release_bundle


# Directories holding state the operator owns. A patch never carries these, but
# guard them explicitly so a malformed bundle cannot reach them either.
PROTECTED_DIRS = ("database", "config", "logs", "output", "temp", "backup")
_PROTECTED_KEYS = frozenset(name.casefold() for name in PROTECTED_DIRS)

UpdateResult = namedtuple("UpdateResult", "applied rolled_back problems backup")


def _listed_names(checksum_file):
    """Names SHA256SUMS vouches for, in file order, each once.

    This list is the payload. verify_checksums only checks what is listed, so
    installing whatever else happens to be on the media would install it
    unverified. Malformed lines are skipped here; verify_checksums reports them.
    """
    names = []
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        _digest, _, name = line.strip().partition("  ")
        name = name.strip()
        if name and name not in names:
            names.append(name)
    return names


def _parts(name):
    """Split a listed name the way the Windows target reads it: "\\" separates."""
    return PurePosixPath(str(name).replace("\\", "/")).parts


def _escapes_install_dir(name):
    """Whether a listed name could resolve outside install_dir.

    Lexical and deliberately broad. A ":" covers drive letters ("C:x" is
    relative to another drive's current folder) and NTFS streams. A part made
    only of dots and spaces is refused because Win32 strips trailing dots and
    spaces, so ".. " opens as "..".
    """
    text = str(name).replace("\\", "/")
    parts = _parts(text)
    return (
        not parts
        or text.startswith("/")
        or any(":" in part or not part.rstrip(" .") for part in parts)
    )


def _is_protected(name):
    """Whether writing this listed name could touch operator-owned data.

    NTFS is case-insensitive and Win32 drops trailing dots and spaces, so
    "Database/documents.db" and "database./documents.db" both open the real
    database/documents.db. A name that could leave install_dir counts as
    protected too: whatever it would reach is not the application layer.
    """
    if _escapes_install_dir(name):
        return True
    return _parts(name)[0].rstrip(" .").casefold() in _PROTECTED_KEYS


def _path_key(parts):
    """Compare paths the way this filesystem does (case-insensitive on Windows)."""
    return os.path.normcase("/".join(parts))


def _layout_problems(patch_dir, names):
    """Everything wrong with what the listing names, found without writing.

    Every blocker is reported at once: reaching the machine again costs a trip.
    """
    problems = []

    escaping = [name for name in names if _escapes_install_dir(name)]
    if escaping:
        problems.append(
            "{} names files outside the installation folder: {}".format(
                release_bundle.CHECKSUM_NAME, ", ".join(sorted(escaping))
            )
        )

    blocked = [
        name for name in names
        if name not in escaping and _is_protected(name)
    ]
    if blocked:
        problems.append(
            "Patch would write into operator-owned data: {}".format(
                ", ".join(sorted(blocked))
            )
        )

    listed = {_path_key(_parts(name)) for name in names}
    listed.add(_path_key((release_bundle.CHECKSUM_NAME,)))
    unlisted = [
        relative.as_posix()
        for relative in (
            path.relative_to(patch_dir)
            for path in sorted(patch_dir.rglob("*")) if path.is_file()
        )
        if _path_key(relative.parts) not in listed
    ]
    if unlisted:
        problems.append(
            "Files on the media are not listed in {} and cannot be verified: {}. "
            "Remove them or rebuild the patch.".format(
                release_bundle.CHECKSUM_NAME, ", ".join(unlisted)
            )
        )

    return problems


def apply_patch(patch_dir, install_dir, _copy=None):
    """Verify, back up, then replace the application layer.

    _copy is injected only so tests can simulate a media failure midway; it
    defaults to shutil.copy2.
    """
    patch_dir = Path(patch_dir)
    install_dir = Path(install_dir)
    copy = _copy or shutil.copy2

    checksums = patch_dir / release_bundle.CHECKSUM_NAME
    if not checksums.is_file():
        return UpdateResult(
            False, False,
            ["{} is missing from the patch; an unverified bundle is not "
             "applied.".format(release_bundle.CHECKSUM_NAME)],
            None,
        )

    # Checked before verify_checksums so a hostile name is never even opened.
    names = _listed_names(checksums)
    problems = _layout_problems(patch_dir, names)
    if problems:
        return UpdateResult(False, False, problems, None)

    problems = release_bundle.verify_checksums(checksums, base_dir=patch_dir)
    if problems:
        return UpdateResult(False, False, problems, None)

    try:
        patch_manifest = release_bundle.read_manifest(patch_dir)
        installed_manifest = release_bundle.read_manifest(install_dir)
    except FileNotFoundError as exc:
        return UpdateResult(False, False, [str(exc)], None)

    problems = release_bundle.compatibility_problems(
        patch_manifest, installed_manifest
    )
    if problems:
        return UpdateResult(False, False, problems, None)

    # Joined exactly as verify_checksums joined them, so what is installed is
    # what was verified.
    relatives = [Path(name) for name in names]

    backup = install_dir / "backup" / "app-{}".format(time.strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True, exist_ok=True)
    saved = []
    for relative in relatives:
        current = install_dir / relative
        if current.is_file():
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, destination)
            saved.append(relative)

    try:
        for relative in relatives:
            destination = install_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy(patch_dir / relative, destination)
    except Exception as exc:
        _restore(backup, install_dir, saved)
        return UpdateResult(
            False, True,
            ["Update failed and the previous version was restored: {}".format(exc)],
            backup,
        )

    _record_new_app_layer(install_dir, installed_manifest, patch_manifest)
    return UpdateResult(True, False, [], backup)


def _restore(backup, install_dir, relatives):
    for relative in relatives:
        source = backup / relative
        if source.is_file():
            destination = install_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def _record_new_app_layer(install_dir, installed_manifest, patch_manifest):
    """Advance only the application layer; runtime and assets were not shipped."""
    updated = dict(installed_manifest)
    layers = dict(updated.get("layers", {}))
    app = dict(layers.get("app", {}))
    patch_app = patch_manifest.get("layers", {}).get("app", {})

    app["version"] = patch_app.get("version", app.get("version"))
    app["commit"] = patch_app.get("commit", app.get("commit"))
    app["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    layers["app"] = app
    updated["layers"] = layers

    (install_dir / release_bundle.MANIFEST_NAME).write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
