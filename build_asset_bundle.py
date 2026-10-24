"""build_asset_bundle.py — đóng gói tầng asset 16 GB để chuyển qua air gap.

The three layers of the portable package change at wildly different rates: the
application source moves several times a week, the runtime a handful of times
ever, and the 16 GB of weights and llama.cpp binaries essentially never. CI can
rebuild the first two, but the assets live outside Git, so they are packed once
here on the machine that already holds them and then carried in by USB.

Usage:
    python build_asset_bundle.py pack --source . --output OCR_VBHC_Assets
    python build_asset_bundle.py verify OCR_VBHC_Assets
    python build_asset_bundle.py check OCR_VBHC_Portable OCR_VBHC_Assets

Two decisions are worth stating because they are not obvious:

*Uncompressed tar by default.* GGUF quantisations and safetensors weights are
already compressed; gzip returns a percent or two on them while costing hours of
single-threaded CPU on 16 GB, and the transfer is bounded by USB write speed
rather than by size. Integrity comes from SHA256SUMS, not from a gzip CRC. `tar`
is preferred over `zip` because tarfile is stdlib — the embedded runtime on the
target can unpack a bundle with no extra tooling — and because splitting a
single stream is exactly what release_bundle.split_file does. `--compress gz`
stays available for asset sets that are mostly binaries.

*ASSET_MANIFEST.json mirrors BUILD_MANIFEST.json.* Same schema field, same
`components` map keyed by release_bundle.ASSET_DIRS, same present/bytes/note
shape. That is what lets check_combined_package() union a runtime package with
an asset bundle and say whether the pair is a complete "full" installation —
the question an operator cannot otherwise answer on an air-gapped target, where
neither build machine is reachable.

`pack` runs on the release machine, but `check` and `restore` run on the target,
so this module ships. It therefore depends only on release_bundle and the
standard library: importing the builder would drag in tooling the package does
not carry, and the import would fail on the very machine the operator needs it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import release_bundle


ASSET_MANIFEST_NAME = "ASSET_MANIFEST.json"

# Resolved locally rather than imported from the builder, so `pack` still
# defaults sensibly on the release machine without this module depending on
# build-side code it cannot import on the target.
PACKAGE_ROOT = Path(__file__).resolve().parent

# BUILD_MANIFEST.json and ASSET_MANIFEST.json both carry `schema: 1` and a
# `components` map, and both end up in the same directory once a bundle is
# unpacked in place. `kind` is what tells the two records apart.
ASSET_KIND = "asset-bundle"
ASSET_SCHEMA = 1

ARCHIVE_STEM = "assets"

# Same ceiling as the runtime layer: a GitHub release asset may not exceed
# 2 GiB, and USB filesystems are happier well below it.
ASSET_PART_LIMIT = release_bundle.DEFAULT_PART_LIMIT

# compression name -> (archive suffix, tarfile write mode)
_ARCHIVE_MODES = {
    "none": ("", "w"),
    "gz": (".gz", "w:gz"),
}

_GIB = 1024 ** 3

# Where a component was found when the two halves are merged.
SOURCE_PACKAGE = "package"
SOURCE_BUNDLE = "bundle"


class AssetSourceError(RuntimeError):
    """Raised when a requested asset directory is absent or empty."""


class BundleIntegrityError(RuntimeError):
    """Raised when a bundle fails verification, before anything is installed."""


AssetEntry = namedtuple("AssetEntry", "path size sha256")

CombinedCheck = namedtuple("CombinedCheck", "complete profile sources missing reasons")


# ══════════════════════════════════════════════════════════════
# Pure rules — no filesystem, so the 16 GB paths stay testable
# ══════════════════════════════════════════════════════════════

def directory_digest(entries) -> str:
    """Content-address a directory from (path, size, sha256) triples.

    Sorting makes the digest independent of the order the tree was walked in,
    and separators are normalised because a bundle cut on Windows has to verify
    on the Linux CI runner.
    """
    rows = sorted(
        (str(path).replace("\\", "/"), int(size), str(digest))
        for path, size, digest in entries
    )
    accumulator = hashlib.sha256()
    for path, size, digest in rows:
        accumulator.update(
            "{}\0{}\0{}\n".format(path, size, digest).encode("utf-8")
        )
    return accumulator.hexdigest()


def parse_checksums(text: str) -> dict:
    """Read `<digest>  <name>` lines back into a name -> digest mapping.

    Reusing the digests release_bundle.write_checksums already computed saves a
    second full pass over 16 GB, and guarantees the manifest and SHA256SUMS can
    never disagree.
    """
    digests = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition("  ")
        name = name.strip()
        if name:
            digests[name] = digest
    return digests


def unsafe_members(members):
    """Return the names of archive members that could write outside the destination.

    The archive arrives on removable media, so its members are input, not
    something to trust. Python 3.9 has no extraction filter (added in 3.12).
    `members` may be plain names or tarfile.TarInfo objects; only the latter
    can be checked for type, which is what restore_assets passes.

    A link is refused even when its own name is harmless: extracting it makes
    a later member of the same name, or under it, write wherever the link
    points. create_archive dereferences links, so a genuine bundle never
    holds one — nor a device file or FIFO, which are refused for the same
    reason a link is: they are not weights or binaries.
    """
    rejected = []
    for member in members:
        if isinstance(member, tarfile.TarInfo):
            name = member.name
            if not (member.isreg() or member.isdir()):
                rejected.append(name)
                continue
        else:
            name = member
        text = str(name).replace("\\", "/")
        # An absolute path, or a Windows drive letter, escapes on its own.
        if text.startswith("/") or ":" in text.split("/")[0]:
            rejected.append(name)
            continue
        depth = 0
        for part in text.split("/"):
            if part in ("", "."):
                continue
            depth += -1 if part == ".." else 1
            if depth < 0:
                rejected.append(name)
                break
    return rejected


def is_plain_file_name(name) -> bool:
    """Whether `name` names a file directly inside whatever directory it is joined to.

    The archive and part names in ASSET_MANIFEST.json are joined onto the
    bundle and staging directories, and the manifest travels on the same USB
    stick as the parts, so a name is input just as a member name is. pack only
    ever writes names like `assets.tar.part001`; anything with a separator, a
    drive or a `..` was not written by pack.
    """
    if not isinstance(name, str) or not name:
        return False
    if any(mark in name for mark in ("/", "\\", ":", "\0")) or ".." in name:
        return False
    return Path(name).name == name


def manifest_name_problems(manifest):
    """Return a problem for each manifest file name that is not a plain file name."""
    archive_name = manifest.get("archive", {}).get("name", ARCHIVE_STEM + ".tar")
    labelled = [("archive", archive_name)] + [
        ("part {}".format(index), part.get("name"))
        for index, part in enumerate(manifest.get("parts", []), start=1)
    ]
    return [
        "{}: {} name {!r} is not a plain file name; refusing to read or write "
        "outside the bundle".format(ASSET_MANIFEST_NAME, label, name)
        for label, name in labelled
        if not is_plain_file_name(name)
    ]


def build_asset_manifest(summaries, parts, archive, built_at,
                         source_commit=None, notes=None) -> dict:
    """Describe the bundle in the shape BUILD_MANIFEST.json already uses.

    Every asset directory is listed, including the ones a partial re-cut left
    out, so the record always states the whole asset set rather than only what
    happened to be packed.
    """
    notes = release_bundle.ASSET_NOTES if notes is None else notes

    components = {}
    for name in release_bundle.ASSET_DIRS:
        summary = summaries.get(name)
        present = summary is not None
        components[name] = {
            "present": present,
            "bytes": summary["bytes"] if present else 0,
            "sha256": summary["sha256"] if present else "",
            "files": summary["files"] if present else 0,
            "note": "" if present else notes.get(
                name, "Supply from the asset bundle."
            ),
        }

    return {
        "schema": ASSET_SCHEMA,
        "kind": ASSET_KIND,
        "built_at": built_at,
        "source_commit": source_commit or "unknown",
        # No runtime_lock_sha256 on purpose: model weights do not change when
        # the dependency pins do, and tying a 16 GB bundle to a runtime layer
        # would force a re-cut on every wheel bump.
        "archive": dict(archive),
        "checksums": release_bundle.CHECKSUM_NAME,
        "parts": [dict(part) for part in parts],
        "components": components,
    }


def _claimed_bytes(manifest, name):
    """Bytes a manifest claims for `name`; None when it claims nothing at all.

    The distinction matters: absent and "present but empty" are different
    failures and deserve different advice.
    """
    if not isinstance(manifest, dict):
        return None
    components = manifest.get("components")
    if not isinstance(components, dict):
        return None
    component = components.get(name)
    if not isinstance(component, dict) or not component.get("present"):
        return None
    try:
        return int(component.get("bytes") or 0)
    except (TypeError, ValueError):
        return 0


def _shape_problems(build_manifest, asset_manifest):
    """Reasons the two records cannot be compared at all."""
    problems = []

    kind = asset_manifest.get("kind") if isinstance(asset_manifest, dict) else None
    if kind != ASSET_KIND:
        problems.append(
            "Asset manifest declares kind {!r}, expected {!r}; the two manifests "
            "may have been supplied the wrong way round.".format(kind, ASSET_KIND)
        )

    schema = asset_manifest.get("schema") if isinstance(asset_manifest, dict) else None
    if isinstance(schema, int) and schema > ASSET_SCHEMA:
        problems.append(
            "Asset manifest schema {} is newer than the {} this tooling reads; "
            "update the tooling instead of guessing at the extra fields.".format(
                schema, ASSET_SCHEMA
            )
        )

    if not isinstance((build_manifest or {}).get("components"), dict):
        problems.append(
            "Runtime package manifest carries no components map, so what the "
            "package contains cannot be established; rebuild it with a current "
            "build_portable.py on the release machine."
        )
    return problems


def _missing_reason(name, package_bytes, bundle_bytes, notes):
    empty_in = []
    if package_bytes == 0:
        empty_in.append("the runtime package")
    if bundle_bytes == 0:
        empty_in.append("the asset bundle")

    if empty_in:
        cause = (
            "recorded present in {} but zero bytes, so it was built from an "
            "empty placeholder directory".format(" and ".join(empty_in))
        )
    else:
        cause = "absent from both the runtime package and the asset bundle"
    return "{}: {} - {}".format(
        name, cause, notes.get(name, "Supply from the asset bundle.")
    )


def check_combined_package(build_manifest, asset_manifest, profile="full",
                           notes=None) -> CombinedCheck:
    """Would this runtime package plus this asset bundle satisfy `profile`?

    Pure: two manifests in, a verdict out. That is what lets the question "is
    this installation complete?" be answered on the staging machine that puts
    the USB together, where neither build machine is reachable.
    """
    if profile not in release_bundle.BUILD_PROFILES:
        raise ValueError(
            "Unknown build profile {!r}; expected one of {}".format(
                profile, ", ".join(sorted(release_bundle.BUILD_PROFILES))
            )
        )
    notes = release_bundle.ASSET_NOTES if notes is None else notes

    reasons = _shape_problems(build_manifest, asset_manifest)

    claims = {
        name: (_claimed_bytes(build_manifest, name),
               _claimed_bytes(asset_manifest, name))
        for name in release_bundle.ASSET_DIRS
    }
    sources = {}
    for name, (package_bytes, bundle_bytes) in claims.items():
        # The package wins when both carry an asset: it is already unpacked, so
        # re-extracting the bundle over it would only cost time.
        if package_bytes:
            sources[name] = SOURCE_PACKAGE
        elif bundle_bytes:
            sources[name] = SOURCE_BUNDLE
        else:
            sources[name] = None

    missing = []
    for name in sorted(release_bundle.BUILD_PROFILES[profile]["required_assets"]):
        if sources.get(name) is not None:
            continue
        missing.append(name)
        package_bytes, bundle_bytes = claims[name]
        reasons.append(_missing_reason(name, package_bytes, bundle_bytes, notes))

    return CombinedCheck(
        complete=not reasons,
        profile=profile,
        sources=sources,
        missing=missing,
        reasons=reasons,
    )


# ══════════════════════════════════════════════════════════════
# Reading the source tree
# ══════════════════════════════════════════════════════════════

def scan_asset_dir(path):
    """List one asset directory as AssetEntry rows, sorted by relative path."""
    path = Path(path)
    entries = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            entries.append(AssetEntry(
                path=item.relative_to(path).as_posix(),
                size=item.stat().st_size,
                sha256=release_bundle.sha256_file(item),
            ))
    return entries


def summarise_assets(source_root, names) -> dict:
    """Measure the requested asset directories, refusing unusable ones.

    Runs before a single byte is written, because discovering an empty gguf/
    after streaming 11 GB into a tar wastes an hour on the release machine.
    """
    source_root = Path(source_root)
    summaries = {}
    for name in names:
        source = source_root / name
        if not source.is_dir():
            raise AssetSourceError(
                "Asset directory {!r} is absent from {}. Point --source at the "
                "tree that holds the weights and llama.cpp binaries.".format(
                    name, source_root
                )
            )
        entries = scan_asset_dir(source)
        total = sum(entry.size for entry in entries)
        if total == 0:
            raise AssetSourceError(
                "Asset directory {!r} in {} holds no data. The builder "
                "leaves an empty placeholder for every excluded asset; packing "
                "one would produce a bundle that installs nothing.".format(
                    name, source_root
                )
            )
        summaries[name] = {
            "bytes": total,
            "files": len(entries),
            "sha256": directory_digest(entries),
        }
    return summaries


# ══════════════════════════════════════════════════════════════
# Writing the bundle
# ══════════════════════════════════════════════════════════════

def create_archive(source_root, names, archive_path, compression="none") -> Path:
    """Write one tar holding each requested asset directory at its top level.

    Members are added in sorted order so two cuts of an unchanged tree differ
    only in the timestamps tar records, which keeps a manifest diff readable.
    """
    if compression not in _ARCHIVE_MODES:
        raise ValueError(
            "Unknown compression {!r}; expected one of {}".format(
                compression, ", ".join(sorted(_ARCHIVE_MODES))
            )
        )
    source_root = Path(source_root)
    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    _, mode = _ARCHIVE_MODES[compression]
    # PAX, because ustar caps a member at 8 GiB and single GGUF weights are
    # already larger than that. Dereferenced, because restore_assets refuses
    # link members: a symlink or a second hard link to a weight file is
    # stored as the file itself, which is also what scan_asset_dir measured.
    with tarfile.open(str(archive_path), mode, format=tarfile.PAX_FORMAT,
                      dereference=True) as tar:
        for name in names:
            source = source_root / name
            tar.add(str(source), arcname=name, recursive=False)
            for item in sorted(source.rglob("*")):
                arcname = "{}/{}".format(
                    name, item.relative_to(source).as_posix()
                )
                tar.add(str(item), arcname=arcname, recursive=False)
    return archive_path


def write_asset_manifest(out_dir, manifest) -> Path:
    path = Path(out_dir) / ASSET_MANIFEST_NAME
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def read_asset_manifest(bundle_dir) -> dict:
    path = Path(bundle_dir) / ASSET_MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(
            "{} not found in {}; this is not an asset bundle, or the transfer "
            "is incomplete.".format(ASSET_MANIFEST_NAME, bundle_dir)
        )
    return json.loads(path.read_text(encoding="utf-8"))


def pack_asset_bundle(source_root, out_dir, names=None,
                      part_limit=ASSET_PART_LIMIT, compression="none",
                      built_at=None, source_commit=None) -> dict:
    """Archive, split, checksum and describe the asset layer. Returns the manifest."""
    names = list(release_bundle.ASSET_DIRS) if names is None else list(names)
    if not names:
        raise ValueError(
            "Nothing to pack; name at least one of {}".format(
                ", ".join(release_bundle.ASSET_DIRS)
            )
        )
    unknown = [name for name in names if name not in release_bundle.ASSET_DIRS]
    if unknown:
        raise ValueError(
            "Unknown asset directory {}; release_bundle.ASSET_DIRS declares "
            "{}".format(", ".join(unknown), ", ".join(release_bundle.ASSET_DIRS))
        )
    if compression not in _ARCHIVE_MODES:
        raise ValueError(
            "Unknown compression {!r}; expected one of {}".format(
                compression, ", ".join(sorted(_ARCHIVE_MODES))
            )
        )

    source_root = Path(source_root)
    out_dir = Path(out_dir)
    summaries = summarise_assets(source_root, names)

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix, _ = _ARCHIVE_MODES[compression]
    archive_path = out_dir / "{}.tar{}".format(ARCHIVE_STEM, suffix)
    create_archive(source_root, names, archive_path, compression)
    archive_record = {
        "name": archive_path.name,
        "compression": compression,
        "bytes": archive_path.stat().st_size,
        "sha256": release_bundle.sha256_file(archive_path),
    }

    parts = release_bundle.split_file(archive_path, out_dir, limit=part_limit)
    checksum_file = release_bundle.write_checksums(
        parts, out_dir / release_bundle.CHECKSUM_NAME
    )
    published = parse_checksums(checksum_file.read_text(encoding="utf-8"))
    part_records = [
        {
            "index": index,
            "name": part.name,
            "bytes": part.stat().st_size,
            "sha256": published[part.name],
        }
        for index, part in enumerate(parts, start=1)
    ]

    # Keeping the archive alongside its parts would demand a second 16 GB of
    # free space; archive.sha256 above is what proves a later join was correct.
    archive_path.unlink()

    manifest = build_asset_manifest(
        summaries,
        part_records,
        archive_record,
        built_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        source_commit=source_commit,
    )
    write_asset_manifest(out_dir, manifest)
    return manifest


# ══════════════════════════════════════════════════════════════
# Target side: verify, then restore
# ══════════════════════════════════════════════════════════════

def verify_bundle(bundle_dir):
    """Return human-readable problems; an empty list means the transfer is intact."""
    bundle_dir = Path(bundle_dir)
    try:
        manifest = read_asset_manifest(bundle_dir)
    except (FileNotFoundError, ValueError) as error:
        return [str(error)]

    # Checked first: every name below is joined onto bundle_dir, and the
    # checksums cannot vouch for a manifest that shipped beside them.
    problems = manifest_name_problems(manifest)
    if problems:
        return problems

    checksum_file = bundle_dir / release_bundle.CHECKSUM_NAME
    if not checksum_file.is_file():
        return [
            "{}: missing, so the bundle cannot be verified".format(
                release_bundle.CHECKSUM_NAME
            )
        ]

    problems = release_bundle.verify_checksums(checksum_file, bundle_dir)

    # A stale part from an earlier cut sorts into the join and corrupts it
    # silently, so anything undeclared is a problem even if it checksums fine.
    declared = {part["name"] for part in manifest.get("parts", [])}
    archive_name = manifest.get("archive", {}).get("name", ARCHIVE_STEM + ".tar")
    for path in sorted(bundle_dir.glob(archive_name + ".part*")):
        if path.name not in declared:
            problems.append(
                "{}: not declared in {}; likely a stale part from an earlier "
                "cut".format(path.name, ASSET_MANIFEST_NAME)
            )
    return problems


def restore_assets(bundle_dir, destination) -> Path:
    """Verify, join and extract a bundle into `destination`.

    Nothing is extracted until the parts, the joined archive and every member
    have checked out — a half-written asset tree is harder to diagnose than a
    refused transfer.
    """
    bundle_dir = Path(bundle_dir)
    destination = Path(destination)

    problems = verify_bundle(bundle_dir)
    if not problems:
        # verify_bundle checked the names on its own read; check them again
        # on the copy whose names are about to be joined onto real paths.
        manifest = read_asset_manifest(bundle_dir)
        problems = manifest_name_problems(manifest)
    if problems:
        raise BundleIntegrityError(
            "Asset bundle in {} failed verification:\n  {}".format(
                bundle_dir, "\n  ".join(problems)
            )
        )

    parts = [bundle_dir / part["name"] for part in manifest["parts"]]
    destination.mkdir(parents=True, exist_ok=True)

    # Join on the destination volume: the joined archive is as large as the
    # assets, and that is the disk the operator already sized for them.
    staging = Path(tempfile.mkdtemp(prefix="asset-join-", dir=str(destination.parent)))
    try:
        archive = release_bundle.join_parts(
            parts, staging / manifest["archive"]["name"]
        )
        actual = release_bundle.sha256_file(archive)
        expected = manifest["archive"]["sha256"]
        if actual != expected:
            raise BundleIntegrityError(
                "Joined archive {} does not match the manifest (expected {}, "
                "got {}); the parts are intact individually, so they were "
                "joined in the wrong order or one is from another cut.".format(
                    archive.name, expected[:16], actual[:16]
                )
            )
        with tarfile.open(str(archive), "r:*") as tar:
            members = tar.getmembers()
            rejected = unsafe_members(members)
            if rejected:
                raise BundleIntegrityError(
                    "Archive {} contains members that are links or special "
                    "files, or would write outside {}: {}".format(
                        archive.name, destination, ", ".join(rejected)
                    )
                )
            tar.extractall(str(destination), members=members)
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
    return destination


# ══════════════════════════════════════════════════════════════
# Command line
# ══════════════════════════════════════════════════════════════

def _git_commit(source_root) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(source_root),
        )
    except (FileNotFoundError, OSError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _gib(value) -> str:
    return "{:.2f} GiB".format(value / _GIB)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pack the asset layer once on the machine that holds it, then "
            "verify and merge it on the other side of the air gap"
        )
    )
    commands = parser.add_subparsers(dest="command")

    pack = commands.add_parser(
        "pack", help="Archive, split and checksum the asset directories"
    )
    pack.add_argument("--source", default=str(PACKAGE_ROOT),
                      help="Tree holding the asset directories (default: this repo)")
    pack.add_argument("--output",
                      default=str(PACKAGE_ROOT / "OCR_VBHC_Assets"),
                      help="Directory to write the parts and manifest into")
    pack.add_argument("--assets", nargs="+", default=list(release_bundle.ASSET_DIRS),
                      choices=list(release_bundle.ASSET_DIRS),
                      help="Asset directories to include (default: all of them)")
    pack.add_argument("--compress", choices=sorted(_ARCHIVE_MODES), default="none",
                      help="none (default) - model weights are already compressed")
    pack.add_argument("--part-limit", type=int, default=ASSET_PART_LIMIT,
                      help="Maximum bytes per part (default: just under 2 GiB)")

    verify = commands.add_parser(
        "verify", help="Check an asset bundle against its SHA256SUMS and manifest"
    )
    verify.add_argument("bundle", help="Directory holding the parts and manifest")

    check = commands.add_parser(
        "check", help="Ask whether a package plus a bundle is a complete install"
    )
    check.add_argument("package", help="Unpacked portable package (BUILD_MANIFEST.json)")
    check.add_argument("bundle", help="Asset bundle (ASSET_MANIFEST.json)")
    check.add_argument("--profile", default="full",
                       choices=sorted(release_bundle.BUILD_PROFILES),
                       help="Profile the merged installation must satisfy")

    parser.set_defaults(command="pack")
    return parser


def _run_pack(args) -> int:
    manifest = pack_asset_bundle(
        source_root=args.source,
        out_dir=args.output,
        names=args.assets,
        part_limit=args.part_limit,
        compression=args.compress,
        source_commit=_git_commit(args.source),
    )
    print("Asset bundle written to {}".format(args.output))
    for name, component in sorted(manifest["components"].items()):
        if component["present"]:
            print("  {:<16} {:>10}  {} files".format(
                name, _gib(component["bytes"]), component["files"]
            ))
        else:
            print("  {:<16} {:>10}  excluded from this cut".format(name, "-"))
    print("  archive {} ({}), {} parts".format(
        manifest["archive"]["name"],
        _gib(manifest["archive"]["bytes"]),
        len(manifest["parts"]),
    ))
    return 0


def _run_verify(args) -> int:
    problems = verify_bundle(args.bundle)
    if problems:
        print("Asset bundle {} FAILED verification:".format(args.bundle))
        for problem in problems:
            print("  {}".format(problem))
        return 1
    print("Asset bundle {} verified".format(args.bundle))
    return 0


def _run_check(args) -> int:
    report = check_combined_package(
        release_bundle.read_manifest(args.package),
        read_asset_manifest(args.bundle),
        profile=args.profile,
    )
    for name in release_bundle.ASSET_DIRS:
        print("  {:<16} {}".format(name, report.sources.get(name) or "MISSING"))
    if report.complete:
        print("Package + bundle satisfy profile {!r}".format(report.profile))
        return 0
    print("Package + bundle do NOT satisfy profile {!r}:".format(report.profile))
    for reason in report.reasons:
        print("  {}".format(reason))
    return 1


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    handlers = {"pack": _run_pack, "verify": _run_verify, "check": _run_check}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
