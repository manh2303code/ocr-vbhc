"""Split, checksum and describe release artifacts that travel on removable media.

build_portable.py stays on the build machine, so everything the target side
needs — joining parts, verifying checksums, reading the manifest — lives here
and ships inside the package.

Two constraints shape this module. Artifacts cross an air gap on USB media, so
corruption has to be caught before anything is installed rather than at first
launch. And a GitHub release rejects any single file over 2 GB, while the
runtime layer alone is about 6 GB.
"""
from __future__ import annotations

import hashlib
import json
from collections import namedtuple
from pathlib import Path


MANIFEST_NAME = "BUILD_MANIFEST.json"
CHECKSUM_NAME = "SHA256SUMS"

# Heavy assets live outside Git, so what a build can include depends on where it
# runs. A CI checkout has none of them; a release machine has all of them.
# Declaring that up front stops a build from quietly shipping an empty package.
#
# This vocabulary lives here rather than in build_portable because the target
# machine needs it: checking and unpacking an asset bundle happens on the
# air-gapped side, where the builder is not installed.
ASSET_DIRS = ("gguf", "bin", "model_embedding", "model_reranker")

ASSET_NOTES = {
    "gguf": (
        "Chandra OCR and Qwen3 chat weights. Supply from the asset bundle "
        "before first launch; OCR and the assistant stay disabled without them."
    ),
    "bin": (
        "llama.cpp server binaries (cpu/ and cuda/). Supply from the asset "
        "bundle; no local inference starts without them."
    ),
    "model_embedding": (
        "Vietnamese embedding model. Supply from the asset bundle; semantic "
        "search and the assistant fall back to keyword matching without it."
    ),
    "model_reranker": (
        "ViRanker cross-encoder. Supply from the asset bundle; retrieval keeps "
        "working but result ordering degrades without it."
    ),
}

# A profile declares what the package *contains*, not merely what it needs. The
# release machine holds all 16.5 GB locally and must still be able to cut a
# runtime-only package, because the assets travel as their own bundle.
BUILD_PROFILES = {
    # What CI can build: embedded Python, wheels and application source.
    # The operator adds the assets from a separately distributed bundle.
    "runtime": {
        "assets": (),
        "required_assets": (),
        "description": "Runtime and application only; assets supplied separately",
    },
    # What a release machine builds: everything needed to run offline.
    "full": {
        "assets": ASSET_DIRS,
        "required_assets": ASSET_DIRS,
        "description": "Complete offline package including models and binaries",
    },
}

# GitHub rejects release assets above 2 GiB. Stay under it rather than on it.
DEFAULT_PART_LIMIT = 1900 * 1024 * 1024

_READ_CHUNK = 1024 * 1024

# offset/length describe the slice; name is the file it lands in.
SplitPart = namedtuple("SplitPart", "index offset length name")


def split_plan(total_bytes, limit=DEFAULT_PART_LIMIT, stem="bundle.tar.gz"):
    """Plan the slices without touching the filesystem.

    An empty input still yields one part, so splitting and joining round-trip
    for degenerate archives instead of producing nothing to transfer.
    """
    if limit <= 0:
        raise ValueError("limit must be positive, got {!r}".format(limit))
    total_bytes = int(total_bytes)
    if total_bytes < 0:
        raise ValueError("total_bytes must not be negative")

    count = max(1, (total_bytes + limit - 1) // limit)
    # Zero-pad wide enough that part 10 never sorts before part 2; operators
    # copy these in filename order.
    width = max(4, len(str(count)))

    parts = []
    offset = 0
    for index in range(count):
        length = min(limit, total_bytes - offset)
        parts.append(SplitPart(
            index=index + 1,
            offset=offset,
            length=length,
            name="{}.part{}".format(stem, str(index + 1).zfill(width)),
        ))
        offset += length
    return parts


def split_file(source, dest_dir, limit=DEFAULT_PART_LIMIT):
    """Write `source` out as numbered parts and return their paths."""
    source = Path(source)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    plan = split_plan(source.stat().st_size, limit=limit, stem=source.name)
    written = []
    with source.open("rb") as handle:
        for part in plan:
            target = dest_dir / part.name
            remaining = part.length
            with target.open("wb") as out:
                while remaining > 0:
                    chunk = handle.read(min(_READ_CHUNK, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            written.append(target)
    return written


def join_parts(parts, destination):
    """Concatenate parts back into a single file, in the order given."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as out:
        for part in parts:
            with Path(part).open("rb") as handle:
                while True:
                    chunk = handle.read(_READ_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
    return destination


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(_READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_name(path, base):
    """Record a path relative to the checksum file, not just its basename.

    Split parts sit beside SHA256SUMS so the two forms agree, but a patch
    bundle carries nested files; recording only the basename made those
    unresolvable and every nested file verified as missing. Separators are
    normalised to "/" so a listing written on Windows verifies on Linux.
    """
    path = Path(path)
    try:
        relative = path.resolve().relative_to(base.resolve())
    except ValueError:
        return path.name
    return relative.as_posix()


def write_checksums(paths, destination):
    """Write the standard `<digest>  <name>` form so sha256sum -c can read it."""
    destination = Path(destination)
    base = destination.parent
    lines = [
        "{}  {}".format(sha256_file(path), _checksum_name(path, base))
        for path in paths
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def verify_checksums(checksum_file, base_dir=None):
    """Return a list of human-readable problems; empty means everything matched."""
    checksum_file = Path(checksum_file)
    base = Path(base_dir) if base_dir is not None else checksum_file.parent

    problems = []
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, _, name = line.partition("  ")
        name = name.strip()
        if not name:
            problems.append("Malformed checksum line: {}".format(line))
            continue
        target = base / name
        if not target.is_file():
            problems.append("{}: missing".format(name))
            continue
        actual = sha256_file(target)
        if actual != expected:
            problems.append(
                "{}: checksum mismatch (expected {}, got {})".format(
                    name, expected[:16], actual[:16]
                )
            )
    return problems


def is_release_version(text):
    """Whether a version string can be meaningfully ordered.

    A commit hash cannot. "abc1234" and "d94102c" reduce to (1234,) and
    (94102,), so which counts as newer depends on which hex digits happen to be
    numerals — the downgrade guard would be deciding at random.
    """
    text = str(text or "").strip().lstrip("vV")
    if not text:
        return False
    head = text.split("-")[0].split("+")[0]
    segments = head.split(".")
    return len(segments) >= 2 and all(part.isdigit() for part in segments if part)


def _version_tuple(text):
    """Compare release numbers without importing packaging on the target.

    Non-numeric segments sort as 0 rather than raising, because an operator's
    USB may carry a hand-labelled build and refusing to parse it would be worse
    than treating it as unordered.
    """
    parts = []
    for chunk in str(text or "0").lstrip("vV").replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def compatibility_problems(patch_manifest, installed_manifest):
    """Decide whether a patch may be applied, and say why not.

    Returns a list of human-readable problems; empty means the patch is safe to
    install. Every blocker is reported at once: reaching an air-gapped machine
    costs a physical trip, so the operator must learn everything they are
    missing in one visit rather than one failure at a time.
    """
    problems = []

    patch_app = (patch_manifest or {}).get("layers", {}).get("app")
    installed_layers = (installed_manifest or {}).get("layers", {})
    if not patch_app or not installed_layers:
        return [
            "Unreadable build manifest: the patch or the installed package is "
            "missing its layer record, so compatibility cannot be established."
        ]

    required_runtime = patch_app.get("requires_runtime")
    installed_runtime = installed_layers.get("runtime", {}).get("lock_sha256")
    if required_runtime and required_runtime != installed_runtime:
        problems.append(
            "Runtime layer mismatch: this patch was built against runtime {}, "
            "the installed runtime is {}. Install the matching runtime layer "
            "first.".format(
                str(required_runtime)[:12], str(installed_runtime)[:12]
            )
        )

    installed_assets = set(installed_layers.get("assets", {}).get("present", []))
    missing_assets = [
        name for name in patch_app.get("requires_assets", [])
        if name not in installed_assets
    ]
    if missing_assets:
        problems.append(
            "Missing asset layer components: {}. Apply the asset bundle before "
            "this patch.".format(", ".join(sorted(missing_assets)))
        )

    installed_version = installed_layers.get("app", {}).get("version")
    patch_version = patch_app.get("version")
    if not (is_release_version(patch_version) and is_release_version(installed_version)):
        # Refusing outright would block valid patches; accepting silently would
        # let a real downgrade through. Say so instead of guessing.
        problems.append(
            "Version cannot be ordered: patch is {!r}, installed is {!r}. At "
            "least one is not a release version, so a downgrade cannot be ruled "
            "out. Rebuild the patch from a tagged release.".format(
                patch_version, installed_version
            )
        )
    elif _version_tuple(patch_version) < _version_tuple(installed_version):
        problems.append(
            "Refusing a downgrade: patch is {}, installed is {}. An older "
            "package must not silently replace a newer one.".format(
                patch_version, installed_version
            )
        )

    return problems


def read_manifest(package_dir):
    """Load BUILD_MANIFEST.json from an installed or unpacked package."""
    path = Path(package_dir) / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            "{} not found in {}; this package predates build manifests "
            "or the transfer is incomplete.".format(MANIFEST_NAME, package_dir)
        )
    return json.loads(path.read_text(encoding="utf-8"))


def directory_size(path):
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
