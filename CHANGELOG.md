# Changelog

All notable changes to **Treant** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-16

### Added
- **Cache TTL & size limits** — `DocumentCache` now accepts `max_age_days`
  (default 30) and `max_size_mb` (default 500) with automatic eviction of the
  oldest entries when limits are exceeded.
- **`treant cache` CLI subcommand** — `treant cache stats`,
  `treant cache clear`, and `treant cache invalidate <file>` expose cache
  management from the command line.
- `DocumentCache.clear()` method to remove all cached entries at once.
- Environment variable overrides `TREANT_CACHE_MAX_AGE_DAYS` and
  `TREANT_CACHE_MAX_SIZE_MB`.
- 14 new unit tests for cache eviction, stats, clear, and env-var defaults.
- `CHANGELOG.md` (this file) and Git release tags.

### Changed
- CLI now uses subcommands (`treant parse <file>`, `treant cache …`).
  Bare `treant <file>` invocations remain supported for backwards
  compatibility.
- `DocumentCache.stats()` now returns `max_age_days`, `max_size_mb`,
  `oldest_entry`, and `newest_entry` in addition to previous fields.

## [0.1.3] — 2026-09-16

### Added
- Image parsing support (`.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp`, `.webp`)
  routed through `parse_image`.
- Structured JSON output (`--output-format json`).
- Table serialisation formats (`--table-format pipe|csv|markdown`).

### Changed
- Docling parser performance improvements.

## [0.1.2] — 2026-08-28

### Fixed
- Google Document AI in-memory OOM risk when processing large documents.

## [0.1.1] — 2026-08-13

### Added
- Filesystem-backed `DocumentCache` with BLAKE2b content hashing and
  staleness detection.
- POSIX advisory locking for manifest safety across threads / processes.

### Fixed
- Cache directory path resolution issues.

## [0.1.0] — 2026-08-09

### Added
- Initial project setup.
- PDF, HTML, Office document, and text file parsing via Docling and
  Google Document AI backends.
- CLI entry point (`treant`).
- Unit test suite.

[0.2.0]: https://github.com/manoje8/treant/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/manoje8/treant/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/manoje8/treant/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/manoje8/treant/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/manoje8/treant/releases/tag/v0.1.0
