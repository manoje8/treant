import asyncio
import gzip
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import aiofiles.os

logger = logging.getLogger(__name__)


_DEFAULT_MAX_AGE_DAYS: int = 30
_DEFAULT_MAX_SIZE_MB: float = 500.0


class DocumentCache:
    """
    Async filesystem-backed cache for parsed document content.

    Each instance is self-contained — there is no module-level singleton.
    Pass (or inject) an instance explicitly wherever a cache is needed.

    Concurrency safety
    All manifest reads and writes are serialised through an
    ``asyncio.Lock``.  Writes use the atomic temp-file +
    ``os.replace`` pattern so readers never see a partially-written
    manifest.

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
        self._lock = asyncio.Lock()
        self._manifest: dict = {}
        self._initialized: bool = False

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

    async def _ensure_initialized(self) -> None:
        """Load the manifest from disk on first use."""
        if not self._initialized:
            self._manifest = await self._load_manifest()
            self._initialized = True

    # Manifest persistence
    async def _load_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return {}

        try:
            async with aiofiles.open(self._manifest_path, encoding="utf-8") as f:
                raw = await f.read()
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Cache - Manifest corrupted, starting fresh")
            return {}

    async def _save_manifest(self) -> None:
        """Write the manifest atomically under the async lock.

        Steps:
        1. Serialize the manifest to a sibling temp file in the *same*
           directory (guarantees the same filesystem → rename is atomic).
        2. ``os.replace`` the temp file over the real manifest — this is
           an atomic operation on both POSIX and Windows.
        """
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)

        payload = json.dumps(self._manifest, indent=2, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._manifest_path.parent,
            prefix=".manifest_tmp_",
            suffix=".json",
        )
        try:
            async with aiofiles.open(fd, "w", encoding="utf-8", closefd=True) as fh:
                await fh.write(payload)
            os.replace(tmp_path, self._manifest_path)
        except Exception:
            # Clean up the orphaned temp file on failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    async def _hash_file_content(file_path: Path) -> str:
        """
        Return a BLAKE2b-256 hex digest of the full file bytes.
        Chunked reads keep memory usage constant regardless of file size.
        """
        hasher = hashlib.blake2b(digest_size=32)
        async with aiofiles.open(file_path, "rb") as fh:
            while True:
                chunk = await fh.read(65_536)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    async def _evict(self, cache_key: str) -> None:
        entry = self._manifest.pop(cache_key, None)

        if entry:
            stale_file = self.cache_dir / entry["filename"]
            stale_file.unlink(missing_ok=True)
            await self._save_manifest()

    async def _enforce_limits(self) -> int:
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
            await self._evict(key)
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
                await self._evict(key)
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

    async def get(self, cache_key: str) -> list[dict] | None:
        await self._ensure_initialized()

        async with self._lock:
            entry = self._manifest.get(cache_key)

            if entry is None:
                return None

            cache_file = self.cache_dir / entry["filename"]

            if not cache_file.exists():
                await self._evict(cache_key)
                return None

            stored_hash = entry.get("content_hash")
            stored_size = entry.get("file_size")
            source_path = Path(entry["source_file"])

            if stored_hash is None or stored_size is None:
                logger.warning(
                    f"Cache - Entry {cache_key[:8]}… missing fingerprint fields, evicting "
                    f"(written before staleness-guard was added)"
                )
                await self._evict(cache_key)
                return None

            try:
                stat_result = await aiofiles.os.stat(source_path)
                current_size = stat_result.st_size
            except OSError:
                logger.warning(f"Cache - Source file gone for {cache_key[:8]}…, evicting")
                await self._evict(cache_key)
                return None

            if current_size != stored_size:
                logger.warning(
                    f"Cache STALE (size changed) - key={cache_key[:8]}… file={entry['source_file']}"
                )
                await self._evict(cache_key)
                return None

            current_hash = await self._hash_file_content(source_path)
            if current_hash != stored_hash:
                logger.warning(
                    f"Cache STALE (content changed, mtime preserved) - key={cache_key[:8]}… "
                    f"file={entry['source_file']}"
                )
                await self._evict(cache_key)
                return None

            try:
                async with aiofiles.open(cache_file, "rb") as f:
                    compressed = await f.read()
                raw = gzip.decompress(compressed)
                content_list = json.loads(raw)

                logger.info(f"Cache HIT - key={cache_key[:8]}… file={entry['source_file']}")
                return content_list

            except (OSError, json.JSONDecodeError, gzip.BadGzipFile) as e:
                logger.warning(f"Cache - Corrupted entry {cache_key[:8]}…, evicting. Error: {e}")
                await self._evict(cache_key)
                return None

    async def store(
        self,
        cache_key: str,
        content_list: list[dict],
        file_path: str | Path,
        parse_method: str,
    ) -> None:
        await self._ensure_initialized()

        async with self._lock:
            file_path = Path(file_path)
            filename = f"{cache_key}.json.gz"
            cache_file = self.cache_dir / filename

            try:
                compressed = gzip.compress(
                    json.dumps(content_list, ensure_ascii=False).encode("utf-8")
                )
                async with aiofiles.open(cache_file, "wb") as f:
                    await f.write(compressed)

            except OSError as e:
                logger.error(f"Cache - Failed to write {cache_file}: {e}")
                return

            content_hash = await self._hash_file_content(file_path)
            stat_result = await aiofiles.os.stat(file_path)
            file_size = stat_result.st_size

            self._manifest[cache_key] = {
                "filename": filename,
                "source_file": str(file_path),
                "parse_method": parse_method,
                "block_count": len(content_list),
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "content_hash": content_hash,
                "file_size": file_size,
            }

            await self._save_manifest()
            logger.info(f"Cache STORE - key={cache_key[:8]}… blocks={len(content_list)}")

            # Run eviction after every store so the cache stays bounded.
            await self._enforce_limits()

    async def invalidate(self, file_path: str | Path) -> int:
        """Remove all cache entries for a given source file.

        Useful when a file is re-uploaded or explicitly re-processed.
        Returns the number of entries removed.
        """
        await self._ensure_initialized()

        async with self._lock:
            stale_keys = [
                k for k, v in self._manifest.items() if v.get("source_file") == str(file_path)
            ]

            for key in stale_keys:
                await self._evict(key)

            return len(stale_keys)

    async def clear(self) -> int:
        """Remove **all** cache entries and their on-disk files.

        Returns the number of entries removed.
        """
        await self._ensure_initialized()

        async with self._lock:
            keys = list(self._manifest.keys())
            for key in keys:
                entry = self._manifest.pop(key, None)
                if entry:
                    stale_file = self.cache_dir / entry["filename"]
                    stale_file.unlink(missing_ok=True)

            await self._save_manifest()
            return len(keys)

    async def stats(self) -> dict:
        await self._ensure_initialized()

        async with self._lock:
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
