"""Read-only, bounded access to an application directory.

Nothing here executes anything: files are only opened as text. Paths are resolved and must stay inside
the application root (symlinks that point outside are skipped), noisy directories are pruned and file
size / count are capped, so a scan of a large repository stays cheap and safe.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

IGNORED_DIRS = frozenset({
    ".git", "node_modules", "venv", ".venv", "dist", "build", "cache", ".cache", "__pycache__", "target",
    "vendor", "genproto", "generated", ".next", "bin", "obj", ".idea", ".vscode", "coverage",
})
SUPPORTED_SUFFIXES = frozenset({".md", ".yml", ".yaml", ".json", ".proto", ".env"})
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 4000


class ApplicationPathError(ValueError):
    """The supplied application path is unusable (message is safe to show to the user)."""


def validate_application_path(path: str, allowed_root: Path | None = None) -> Path:
    if not path or not path.strip() or "\x00" in path:
        raise ApplicationPathError("Application path is required")
    try:
        resolved = Path(path.strip()).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ApplicationPathError(f"Path does not exist: {path}") from None
    if not resolved.is_dir():
        raise ApplicationPathError(f"Path is not a directory: {path}")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ApplicationPathError(f"Path is not readable: {path}")
    if allowed_root is not None:
        root = allowed_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ApplicationPathError(f"Path is outside the allowed application root ({root})")
    return resolved


@dataclass
class ApplicationFiles:
    root: Path
    files: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    truncated: bool = False

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def matching(self, *, suffixes: tuple[str, ...] = (), names: tuple[str, ...] = ()) -> list[Path]:
        return [p for p in self.files if p.suffix.lower() in suffixes or p.name.lower() in names]


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def collect_files(root: Path, max_files: int = MAX_FILES, max_bytes: int = MAX_FILE_BYTES) -> ApplicationFiles:
    found = ApplicationFiles(root=root)
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept = []
        for d in sorted(dirs):
            full = current_path / d
            if d in IGNORED_DIRS or (d.startswith(".") and d not in {".github"}):
                continue
            if full.is_symlink() and not _is_inside(root, full):
                found.skipped.append(f"{full.relative_to(root).as_posix()} (symlink outside root)")
                continue
            kept.append(d)
        dirs[:] = kept
        for name in sorted(names):
            full = current_path / name
            suffix = full.suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES and not name.startswith(".env"):
                continue
            if full.is_symlink() and not _is_inside(root, full):
                found.skipped.append(f"{full.relative_to(root).as_posix()} (symlink outside root)")
                continue
            try:
                if full.stat().st_size > max_bytes:
                    found.skipped.append(f"{full.relative_to(root).as_posix()} (larger than {max_bytes // 1024} KB)")
                    continue
            except OSError:
                continue
            if len(found.files) >= max_files:
                found.truncated = True
                return found
            found.files.append(full)
    return found
