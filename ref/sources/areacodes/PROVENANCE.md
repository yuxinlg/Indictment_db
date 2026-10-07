# areacodes — vendored snapshot

- Upstream: https://github.com/yescallop/areacodes
- Commit: `e2c2b2e1fb4ce1fb76e879aa0b80e229adaf950c` (committed 2026-02-02)
- Retrieved: 2026-10-07 via `git clone --depth 1`
- License: CC0 1.0 Universal (see LICENSE) — redistribution of this snapshot is permitted.
- Files copied verbatim: data/*.txt (annual code tables 1981–2025), diff/*.diff (year-to-year
  correspondence tables with 国务院/民政部 document numbers), result.csv (consolidated table),
  errata.md (documented corrections to the 民政部 source data), diff-spec.md, diff-notes.md, README.md.
- Underlying primary sources, as declared by the upstream README: 民政部《中华人民共和国县级以上
  行政区划代码》annual lists (1980–2024), 民政部《县级以上行政区划变更情况》, 国务院公报,
  民政部全国行政区划信息查询平台, 国家地名信息库 (2025).

Cite the primary sources for the data and this repository (at the commit above) for the
consolidation, corrections and successor mappings. Do not edit files in this directory; derived
tables are built by scripts/ref_regions.py into ref/.
