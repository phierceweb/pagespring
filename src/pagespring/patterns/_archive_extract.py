"""Unpacking a downloaded archive, refused past the extraction budget."""

from __future__ import annotations

import gzip
import io
import lzma
import tarfile
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path

from pf_core.exceptions import InvalidInputError
from pf_core.utils.env import resolve_int

# Extraction budget, checked against declared member sizes before anything is written.
_MAX_EXTRACT_BYTES_DEFAULT = 2 * 1024 * 1024 * 1024
_MAX_EXTRACT_BYTES_ENV_VAR = "PAGESPRING_MAX_EXTRACT_BYTES"
_MAX_MEMBERS = 100_000
_MAX_RATIO = 100
# Below this many extracted bytes a high ratio is ordinary repetitive text, not a bomb.
_RATIO_FLOOR_BYTES = 16 * 1024 * 1024
_ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    tarfile.TarError,
    EOFError,
    zlib.error,
    lzma.LZMAError,
    gzip.BadGzipFile,
    NotImplementedError,
)


def _max_extract_bytes() -> int:
    """Cap on an archive's extracted size; PAGESPRING_MAX_EXTRACT_BYTES overrides."""
    n: int = resolve_int(None, _MAX_EXTRACT_BYTES_ENV_VAR, default=_MAX_EXTRACT_BYTES_DEFAULT)
    return n if n > 0 else _MAX_EXTRACT_BYTES_DEFAULT


def _check_budget(sizes: Iterator[int], archive_bytes: int, src: str) -> None:
    """Refuse a bomb from its declared member sizes before any member reaches disk."""
    max_bytes = _max_extract_bytes()
    total = 0
    for members, size in enumerate(sizes, start=1):
        total += size
        if members > _MAX_MEMBERS:
            raise InvalidInputError(f"{src}: archive has more than {_MAX_MEMBERS} members")
        if total > max_bytes:
            raise InvalidInputError(
                f"{src}: archive would extract past {max_bytes} bytes "
                f"(raise {_MAX_EXTRACT_BYTES_ENV_VAR} for a larger manual)"
            )
        ratio = total / max(archive_bytes, 1)
        if total > _RATIO_FLOOR_BYTES and ratio > _MAX_RATIO:
            raise InvalidInputError(
                f"{src}: implausible compression ratio ({ratio:.0f}:1) for a docs archive"
            )


def _open_tar(data: bytes, src: str) -> tarfile.TarFile:
    try:
        return tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.ReadError as exc:
        got = " (got an HTML page)" if data.lstrip()[:1] == b"<" else ""
        raise InvalidInputError(f"{src}: not a zip, tar or epub archive{got}") from exc


def extract(data: bytes, dest: Path, src: str) -> None:
    """Unpack a zip, tar or epub into ``dest``; a damaged or unsafe one is invalid input."""
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                _check_budget((i.file_size for i in z.infolist()), len(data), src)
                z.extractall(dest)
            return
        with _open_tar(data, src) as tar:
            _check_budget((m.size for m in tar), len(data), src)
            tar.extractall(dest, filter="data")
    except _ARCHIVE_ERRORS as exc:
        raise InvalidInputError(f"{src}: damaged or unsafe archive: {exc}") from exc
