"""Apply an application-layer patch on an air-gapped machine.

The target has no network, no technical staff, and runs a live document
workflow. Three consequences shape this module:

- Everything is checked before anything is written. A refused update costs a
  second trip; a half-written one stops the office.
- The previous application layer is kept, and restored if the replacement
  fails partway through.
- User data is never in scope. Only files the patch actually carries are
  replaced, so database/, config/, logs/ and output/ are untouched.

The patch itself is small — the application layer is about 5 MB against 23 GB
for the whole package — which is the point of versioning the layers separately.
"""
from __future__ import annotations

import json
import shutil
import time
from collections import namedtuple
from pathlib import Path

import release_bundle


# Directories holding state the operator owns. A patch never carries these, but
# guard them explicitly so a malformed bundle cannot reach them either.
PROTECTED_DIRS = ("database", "config", "logs", "output", "temp", "backup")

UpdateResult = namedtuple("UpdateResult", "applied rolled_back problems backup")


def _payload_files(patch_dir):
    """Files the patch carries, excluding its own checksum listing."""
    return [
        path for path in sorted(patch_dir.rglob("*"))
        if path.is_file() and path.name != release_bundle.CHECKSUM_NAME
    ]


def _is_protected(relative):
    return relative.parts and relative.parts[0] in PROTECTED_DIRS


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

    payload = _payload_files(patch_dir)
    relatives = [path.relative_to(patch_dir) for path in payload]
    blocked = [str(rel) for rel in relatives if _is_protected(rel)]
    if blocked:
        return UpdateResult(
            False, False,
            ["Patch would write into operator-owned data: {}".format(
                ", ".join(sorted(blocked))
            )],
            None,
        )

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
        for source, relative in zip(payload, relatives):
            destination = install_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy(source, destination)
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
