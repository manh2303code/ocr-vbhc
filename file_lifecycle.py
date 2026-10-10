"""Safe ownership transitions for source files imported from the app temp folder."""
from pathlib import Path
import shutil
import time
from typing import Optional, Tuple, Union


PathLike = Union[str, Path]


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def materialize_document_source(
    source_path: PathLike,
    temp_dir: PathLike,
    output_dir: PathLike,
    unique_token: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    """Copy a temp source to managed storage and return its cleanup target."""
    source = Path(source_path)
    if not _is_within(source, Path(temp_dir)):
        return source, None

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    token = unique_token or str(time.time_ns())
    stored = output / f"{source.stem}_perm_{token}{source.suffix}"
    shutil.copy2(source, stored)
    return stored, source


def remove_temporary_source(cleanup_path: Optional[PathLike]) -> None:
    if cleanup_path is not None:
        Path(cleanup_path).unlink(missing_ok=True)

