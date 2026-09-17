from datetime import datetime, timedelta, timezone

import pytest

from treant.document_cache import DocumentCache


@pytest.fixture
def cache_env(tmp_path):
    """Set up a DocumentCache rooted in a temp directory with a dummy source file."""
    source_file = tmp_path / "source.pdf"
    source_file.write_bytes(b"hello world")
    cache = DocumentCache(
        project_root=tmp_path,
        max_age_days=30,
        max_size_mb=500,
    )
    return cache, source_file


async def _store_entry(cache, key, source_file, blocks=None, cached_at=None):
    """Helper: store an entry and optionally backdate its cached_at timestamp."""
    if blocks is None:
        blocks = [{"type": "text", "text": f"content-{key[:8]}"}]
    await cache.store(key, blocks, source_file, parse_method="docling")
    if cached_at is not None:
        cache._manifest[key]["cached_at"] = cached_at
        await cache._save_manifest()


class TestStoreAndGet:
    async def test_round_trip(self, cache_env):
        cache, src = cache_env
        blocks = [{"type": "text", "text": "hello"}]
        await cache.store("key1", blocks, src, parse_method="docling")
        assert await cache.get("key1") == blocks

    async def test_get_missing_key_returns_none(self, cache_env):
        cache, _ = cache_env
        assert await cache.get("nonexistent") is None


class TestInvalidate:
    async def test_invalidate_removes_matching_entries(self, cache_env):
        cache, src = cache_env
        await cache.store("k1", [{"t": 1}], src, parse_method="docling")
        await cache.store("k2", [{"t": 2}], src, parse_method="docling")

        removed = await cache.invalidate(str(src))
        assert removed == 2
        assert await cache.get("k1") is None
        assert await cache.get("k2") is None

    async def test_invalidate_no_match(self, cache_env):
        cache, src = cache_env
        await cache.store("k1", [{"t": 1}], src, parse_method="docling")
        assert await cache.invalidate("/no/such/file") == 0
        assert await cache.get("k1") is not None


class TestClear:
    async def test_clear_removes_all(self, cache_env):
        cache, src = cache_env
        await cache.store("a", [{"x": 1}], src, parse_method="docling")
        await cache.store("b", [{"x": 2}], src, parse_method="docling")

        removed = await cache.clear()
        assert removed == 2
        assert await cache.get("a") is None
        assert await cache.get("b") is None

    async def test_clear_on_empty_cache(self, cache_env):
        cache, _ = cache_env
        assert await cache.clear() == 0


class TestStats:
    async def test_stats_empty(self, cache_env):
        cache, _ = cache_env
        s = await cache.stats()
        assert s["entries"] == 0
        assert s["size_mb"] == 0
        assert s["max_age_days"] == 30
        assert s["max_size_mb"] == 500
        assert s["oldest_entry"] is None
        assert s["newest_entry"] is None

    async def test_stats_with_entries(self, cache_env):
        cache, src = cache_env
        await cache.store("s1", [{"t": 1}], src, parse_method="docling")
        s = await cache.stats()
        assert s["entries"] == 1
        assert s["size_mb"] >= 0
        assert s["oldest_entry"] is not None
        assert s["newest_entry"] is not None


class TestAgeEviction:
    async def test_expired_entries_are_evicted_on_store(self, tmp_path):
        source_file = tmp_path / "source.pdf"
        source_file.write_bytes(b"hello world")
        cache = DocumentCache(
            project_root=tmp_path,
            max_age_days=7,
            max_size_mb=500,
        )

        # Store an entry backdated to 10 days ago
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        await _store_entry(cache, "old_key", source_file, cached_at=old_ts)

        # Confirm it exists before eviction runs
        assert "old_key" in cache._manifest

        # Storing a new entry triggers _enforce_limits
        await cache.store("new_key", [{"t": "new"}], source_file, parse_method="docling")

        # The old entry should have been evicted
        assert "old_key" not in cache._manifest
        assert await cache.get("old_key") is None
        # The new entry is fine
        assert await cache.get("new_key") is not None

    async def test_fresh_entries_are_kept(self, tmp_path):
        source_file = tmp_path / "source.pdf"
        source_file.write_bytes(b"hello world")
        cache = DocumentCache(
            project_root=tmp_path,
            max_age_days=7,
            max_size_mb=500,
        )

        await cache.store("fresh", [{"t": "ok"}], source_file, parse_method="docling")
        # Trigger enforce_limits again via another store
        await cache.store("fresh2", [{"t": "ok2"}], source_file, parse_method="docling")

        assert "fresh" in cache._manifest
        assert "fresh2" in cache._manifest


class TestSizeEviction:
    async def test_oldest_entries_evicted_when_over_limit(self, tmp_path):
        source_file = tmp_path / "source.pdf"
        source_file.write_bytes(b"hello world")

        # Use a very small size limit so entries get evicted
        cache = DocumentCache(
            project_root=tmp_path,
            max_age_days=365,
            max_size_mb=0.0001,  # ~100 bytes — virtually nothing
        )

        # Store several entries — each gzipped JSON payload will exceed the limit
        big_blocks = [{"text": "x" * 200}]
        await _store_entry(
            cache,
            "entry_old",
            source_file,
            blocks=big_blocks,
            cached_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        )
        await _store_entry(
            cache,
            "entry_mid",
            source_file,
            blocks=big_blocks,
            cached_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )

        # This store should trigger size eviction of the oldest entries
        await cache.store("entry_new", big_blocks, source_file, parse_method="docling")

        # The newest entry should survive; at least one old entry should be gone
        assert "entry_new" in cache._manifest
        remaining = {"entry_old", "entry_mid"} & set(cache._manifest.keys())  # noqa
        # At least the oldest should have been evicted
        assert "entry_old" not in cache._manifest


class TestEnvVarDefaults:
    async def test_max_age_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TREANT_CACHE_MAX_AGE_DAYS", "14")
        cache = DocumentCache(project_root=tmp_path)
        assert cache.max_age_days == 14

    async def test_max_size_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TREANT_CACHE_MAX_SIZE_MB", "256")
        cache = DocumentCache(project_root=tmp_path)
        assert cache.max_size_mb == 256.0

    async def test_constructor_overrides_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TREANT_CACHE_MAX_AGE_DAYS", "14")
        monkeypatch.setenv("TREANT_CACHE_MAX_SIZE_MB", "256")
        cache = DocumentCache(project_root=tmp_path, max_age_days=60, max_size_mb=1024)
        assert cache.max_age_days == 60
        assert cache.max_size_mb == 1024.0
