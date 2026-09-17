import contextlib
import gzip
import hashlib
import json
import logging
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_MAX_AGE_DAYS: int = 30
_DEFAULT_MAX_SIZE_MB: float = 500.0


@contextlib.contextmanager
def _file_lock(lock_path: str | Path, shared: bool = False):
    """
    Cross-platform file lock using stdlib modules.

    On POSIX: uses ``fcntl.flock`` (shared or exclusive).
    On Windows: uses ``msvcrt.locking`` (exclusive only — shared
    locks are not supported by ``msvcrt``, so all locks are exclusive).
    """

    if platform.system() == "Windows":
        import msvcrt

        with open(lock_path, "a+") as lf:
            lf.seek(0)
            msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield lf
            finally:
                lf.seek(0)
                msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)

    else:
        import fcntl

        with open(lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)

            try:
                yield lf
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


class DocumentCache:
    """
    Filesystem-backed cache for parsed document content.

    Each instance is self-contained — there is no module-level singleton.
    Pass (or inject) an instance explicitly wherever a cache is needed.

    Thread / multi-process safety
    ``manifest.json`` is protected by a companion ``manifest.json.lock``
    file using a platform-aware file lock (``fcntl.flock`` on POSIX,
    ``msvcrt.locking`` on Windows).  Writes use the atomic
    temp-file + ``os.replace`` pattern so readers never see a
    partially-written manifest.

    Eviction policy
    After every :meth:`store` call the cache is checked against
    *max_age_days* and *max_size_mb*.  Entries older than *max_age_days*
    are removed first; if the cache is still over *max_size_mb* the
    oldest remaining entries are removed until the limit is satisfied.
    """

    def __init__(
        self,
        project_root: Path,
        cache_dir_name: str = ".doc_cache",
        manifest_path: Path | None = None,
        max_age_days: int | None = None,
        max_size_mb: float | None = None,
    ):
        self.project_root = project_root
        self.cache_dir = self.project_root / cache_dir_name
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = manifest_path or (self.cache_dir / "manifest.json")
        self._lock_path = self._manifest_path.with_suffix(".json.lock")
        self._manifest: dict = self._load_manifest()

        self.max_age_days: int = (
            max_age_days
            if max_age_days is not None
            else int(os.getenv("TREANT_CACHE_MAX_AGE_DAYS", _DEFAULT_MAX_AGE_DAYS))
        )
        self.max_size_mb: float = (
            max_size_mb
            if max_size_mb is not None
            else float(os.getenv("TREANT_CACHE_MAX_SIZE_MB", _DEFAULT_MAX_SIZE_MB))
        )

    # Manifest persistence
    def _load_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {}

        with _file_lock(self._lock_path, shared=True):
            try:
                return json.loads(self._manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.error("Cache - Manifest corrupted, starting fresh")
                return {}

    def _save_manifest(self) -> None:
        """Write the manifest atomically under an exclusive lock.

        Steps:
        1. Open (or create) the lock file and acquire an exclusive lock.
        2. Serialize the manifest to a sibling temp file in the *same*
           directory (guarantees the same filesystem → rename is atomic).
        3. ``os.replace`` the temp file over the real manifest — this is
           an atomic operation on POSIX systems.
        4. Release the lock.
        """
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)

        with _file_lock(self._lock_path, shared=False):
            payload = json.dumps(self._manifest, indent=2, ensure_ascii=False)
            fd, tmp_path = tempfile.mkstemp(
                dir=self._manifest_path.parent,
                prefix=".manifest_tmp_",
                suffix=".json",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp_path, self._manifest_path)
            except Exception:
                # Clean up the orphaned temp file on failure.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    @staticmethod
    def _hash_file_content(file_path: Path) -> str:
        """
        Return a BLAKE2b-256 hex digest of the full file bytes.
        Chunked reads keep memory usage constant regardless of file size.
        """
        hasher = hashlib.blake2b(digest_size=32)
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _evict(self, cache_key: str) -> None:
        entry = self._manifest.pop(cache_key, None)

        if entry:
            stale_file = self.cache_dir / entry["filename"]
            stale_file.unlink(missing_ok=True)
            self._save_manifest()

    def _enforce_limits(self) -> int:
        """Evict entries that exceed *max_age_days* or *max_size_mb*.

        Returns the total number of entries evicted.
        """
        evicted = 0
        now = datetime.now(timezone.utc)

        # 1: age-based eviction
        expired_keys: list[str] = []
        for key, entry in self._manifest.items():
            cached_at = entry.get("cached_at")
            if cached_at is None:
                continue
            try:
                ts = datetime.fromisoformat(cached_at)
            except (ValueError, TypeError):
                continue
            age_days = (now - ts).total_seconds() / 86_400
            if age_days > self.max_age_days:
                expired_keys.append(key)

        for key in expired_keys:
            logger.info(f"Cache EVICT (age) - key={key[:8]}…")
            self._evict(key)
            evicted += 1

        # 2: size-based eviction (LRU by cached_at)
        max_bytes = self.max_size_mb * 1024 * 1024
        total_bytes = self._total_cache_bytes()

        if total_bytes > max_bytes:
            # Sort entries oldest-first
            sorted_keys = sorted(
                self._manifest,
                key=lambda k: self._manifest[k].get("cached_at", ""),
            )
            for key in sorted_keys:
                if total_bytes <= max_bytes:
                    break
                entry = self._manifest.get(key)
                if entry is None:
                    continue
                cache_file = self.cache_dir / entry["filename"]
                try:
                    file_bytes = cache_file.stat().st_size
                except OSError:
                    file_bytes = 0
                logger.info(f"Cache EVICT (size) - key={key[:8]}…")
                self._evict(key)
                total_bytes -= file_bytes
                evicted += 1

        return evicted

    def _total_cache_bytes(self) -> int:
        """Sum the on-disk size of all cached payload files."""
        total = 0
        for entry in self._manifest.values():
            cache_file = self.cache_dir / entry["filename"]
            try:
                total += cache_file.stat().st_size
            except OSError:
                pass
        return total

    def get(self, cache_key: str) -> list[dict] | None:
        entry = self._manifest.get(cache_key)

        if entry is None:
            return None

        cache_file = self.cache_dir / entry["filename"]

        if not cache_file.exists():
            self._evict(cache_key)
            return None

        stored_hash = entry.get("content_hash")
        stored_size = entry.get("file_size")
        source_path = Path(entry["source_file"])

        if stored_hash is None or stored_size is None:
            logger.warning(
                f"Cache - Entry {cache_key[:8]}… missing fingerprint fields, evicting "
                f"(written before staleness-guard was added)"
            )
            self._evict(cache_key)
            return None

        try:
            current_size = source_path.stat().st_size
        except OSError:
            logger.warning(f"Cache - Source file gone for {cache_key[:8]}…, evicting")
            self._evict(cache_key)
            return None

        if current_size != stored_size:
            logger.warning(
                f"Cache STALE (size changed) - key={cache_key[:8]}… file={entry['source_file']}"
            )
            self._evict(cache_key)
            return None

        current_hash = self._hash_file_content(source_path)
        if current_hash != stored_hash:
            logger.warning(
                f"Cache STALE (content changed, mtime preserved) - key={cache_key[:8]}… "
                f"file={entry['source_file']}"
            )
            self._evict(cache_key)
            return None

        try:
            with gzip.open(cache_file, "rt", encoding="utf-8") as f:
                content_list = json.load(f)

            logger.info(f"Cache HIT - key={cache_key[:8]}… file={entry['source_file']}")
            return content_list

        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Cache - Corrupted entry {cache_key[:8]}…, evicting. Error: {e}")
            self._evict(cache_key)
            return None

    def store(
        self,
        cache_key: str,
        content_list: list[dict],
        file_path: str | Path,
        parse_method: str,
    ) -> None:
        file_path = Path(file_path)
        filename = f"{cache_key}.json.gz"
        cache_file = self.cache_dir / filename

        try:
            with gzip.open(cache_file, "wt", encoding="utf-8") as f:
                json.dump(content_list, f, ensure_ascii=False)

        except OSError as e:
            logger.error(f"Cache - Failed to write {cache_file}: {e}")
            return

        content_hash = self._hash_file_content(file_path)
        file_size = file_path.stat().st_size

        self._manifest[cache_key] = {
            "filename": filename,
            "source_file": str(file_path),
            "parse_method": parse_method,
            "block_count": len(content_list),
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "file_size": file_size,
        }

        self._save_manifest()
        logger.info(f"Cache STORE - key={cache_key[:8]}… blocks={len(content_list)}")

        # Run eviction after every store so the cache stays bounded.
        self._enforce_limits()

    def invalidate(self, file_path: str | Path) -> int:
        """Remove all cache entries for a given source file.

        Useful when a file is re-uploaded or explicitly re-processed.
        Returns the number of entries removed.
        """
        stale_keys = [
            k for k, v in self._manifest.items() if v.get("source_file") == str(file_path)
        ]

        for key in stale_keys:
            self._evict(key)

        return len(stale_keys)

    def clear(self) -> int:
        """Remove **all** cache entries and their on-disk files.

        Returns the number of entries removed.
        """
        keys = list(self._manifest.keys())
        for key in keys:
            entry = self._manifest.pop(key, None)
            if entry:
                stale_file = self.cache_dir / entry["filename"]
                stale_file.unlink(missing_ok=True)

        self._save_manifest()
        return len(keys)

    def stats(self) -> dict:
        total_bytes = sum(
            (self.cache_dir / v["filename"]).stat().st_size
            for v in self._manifest.values()
            if (self.cache_dir / v["filename"]).exists()
        )

        oldest_at: str | None = None
        newest_at: str | None = None
        if self._manifest:
            timestamps = [v.get("cached_at", "") for v in self._manifest.values()]
            timestamps = [t for t in timestamps if t]
            if timestamps:
                oldest_at = min(timestamps)
                newest_at = max(timestamps)

        return {
            "entries": len(self._manifest),
            "size_mb": round(total_bytes / 1024 / 1024, 2),
            "cache_dir": str(self.cache_dir),
            "max_age_days": self.max_age_days,
            "max_size_mb": self.max_size_mb,
            "oldest_entry": oldest_at,
            "newest_entry": newest_at,
        }
