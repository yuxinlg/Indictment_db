#!/usr/bin/env python3
"""
M0 verification — run after m0_csv_to_parquet.py.

Checks (方案 v2 §验证方式):
  1. Row conservation: exactly 2,399,736 rows.
  2. doc_id / url uniqueness.
  3. pub_date 100% present, window 2015-08-04 .. 2019-09-10.
  4. Quality-flag counts vs baseline.
  5. Signing-date provenance: date_source / precision breakdown, DATE_CONFLICT.
  6. Publication lag (pub_date - sign_date) median / mean / p90 vs baseline 84 / 153 / 312.
  7. Per-year counts by publication year AND by sign year (the two must differ).
  8. Smoke query: 2016 Guangdong 危险驾驶 起诉书 count (recorded for later cross-check).
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path.home() / "indictment_work" / "raw.parquet"
GLOB = str(ROOT / "pub_year=*" / "*.parquet")
EXPECTED_ROWS = 2_399_736

BASELINE_FLAGS = {
    "BAD_DATE": (1, 18_158),       # stricter than the 16,947 diagnosis figure, see converter
    "MULTI_WENHAO": (2, 6_427),
    "FRAGMENT": (4, 539),
    "EMPTY": (8, 22),
    "NON_HAN_SCRIPT": (16, 4_444),
    "SPACED_TEXT": (32, 8_075),
    "DATE_CONFLICT": (64, None),
    "NO_URL_DATE": (128, 0),
}

con = duckdb.connect()
con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{GLOB}', hive_partitioning=true)")

ok = True


def check(label, cond, detail=""):
    global ok
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{mark}] {label} {detail}")


# 1. rows
n = con.execute("SELECT count(*) FROM raw").fetchone()[0]
check("row count", n == EXPECTED_ROWS, f"{n:,} (expected {EXPECTED_ROWS:,})")

# 2. uniqueness
n_doc, n_url = con.execute("SELECT count(DISTINCT doc_id), count(DISTINCT url) FROM raw").fetchone()
check("doc_id unique", n_doc == n, f"{n_doc:,}")
check("url unique", n_url == n, f"{n_url:,}")

# 3. pub_date
nn, dmin, dmax = con.execute("SELECT count(pub_date), min(pub_date), max(pub_date) FROM raw").fetchone()
check("pub_date non-null 100%", nn == n, f"{nn:,}")
check("pub_date window", str(dmin) == "2015-08-04" and str(dmax) == "2019-09-10", f"{dmin} .. {dmax}")
yr_part_ok = con.execute("SELECT count(*) FROM raw WHERE pub_year <> year(pub_date)").fetchone()[0]
check("partition key == year(pub_date)", yr_part_ok == 0, f"{yr_part_ok} mismatches")

# 4. flags
print("\nquality flags vs baseline:")
print(f"  {'flag':<16}{'got':>10}{'baseline':>10}{'diff':>8}")
for name, (bit, base) in BASELINE_FLAGS.items():
    got = con.execute(f"SELECT count(*) FROM raw WHERE (quality_flags & {bit}) <> 0").fetchone()[0]
    if base is None:
        print(f"  {name:<16}{got:>10,}{'—':>10}{'':>8}")
    else:
        print(f"  {name:<16}{got:>10,}{base:>10,}{got-base:>+8,}")
has_nl = con.execute(r"SELECT count(*) FROM raw WHERE contains(content, chr(10))").fetchone()[0]
print(f"  {'(has newline)':<16}{has_nl:>10,}{63_465:>10,}{has_nl-63_465:>+8,}")

# 5. signing-date provenance
print("\nsigning date provenance (date_source x precision):")
for src, prec, c in con.execute(
    "SELECT coalesce(date_source,'NULL'), coalesce(sign_date_precision,'NULL'), count(*) FROM raw GROUP BY 1,2 ORDER BY 3 DESC"
).fetchall():
    print(f"  {src:<10}{prec:<8}{c:>10,}  {100*c/n:6.2f}%")
sd_nonnull, sy_nonnull = con.execute("SELECT count(sign_date), count(sign_year) FROM raw").fetchone()
print(f"  sign_date non-null {sd_nonnull:,} ({100*sd_nonnull/n:.3f}%)   sign_year non-null {sy_nonnull:,} ({100*sy_nonnull/n:.3f}%)")
consist = con.execute(
    "SELECT count(*) FROM raw WHERE sign_date IS NOT NULL AND (sign_year <> year(sign_date) OR sign_month <> month(sign_date))"
).fetchone()[0]
check("sign_year/sign_month consistent with sign_date", consist == 0, f"{consist} inconsistent")
prec_ok = con.execute(
    "SELECT count(*) FROM raw WHERE (sign_date IS NOT NULL) <> (sign_date_precision = 'day')"
).fetchone()[0]
check("sign_date present iff precision = day", prec_ok == 0, f"{prec_ok} violations")

# 6. publication lag
lag = con.execute(
    """
    SELECT median(lag), avg(lag), quantile_cont(lag, 0.9),
           sum(CASE WHEN lag < 0 THEN 1 ELSE 0 END)::DOUBLE / count(*) * 100
    FROM (SELECT date_diff('day', sign_date, pub_date) AS lag FROM raw WHERE sign_date IS NOT NULL)
    """
).fetchone()
print(f"\npublication lag days: median {lag[0]:.0f} (84)  mean {lag[1]:.0f} (153)  p90 {lag[2]:.0f} (312)  negative {lag[3]:.2f}% (0.02%)")

# 7. per-year, both definitions
print("\nrows by year — publication year vs sign year:")
rows = con.execute(
    """
    WITH p AS (SELECT pub_year AS y, count(*) AS by_pub FROM raw GROUP BY 1),
         s AS (SELECT sign_year AS y, count(*) AS by_sign FROM raw WHERE sign_year IS NOT NULL GROUP BY 1)
    SELECT coalesce(p.y, s.y) AS y, coalesce(by_pub, 0), coalesce(by_sign, 0)
    FROM p FULL OUTER JOIN s ON p.y = s.y ORDER BY 1
    """
).fetchall()
print(f"  {'year':<6}{'by_pub':>10}{'by_sign':>10}")
for y, bp, bs in rows:
    print(f"  {y:<6}{bp:>10,}{bs:>10,}")

# 8. smoke query for later cross-check against M1
smoke = con.execute(
    """
    SELECT count(*) FROM raw
    WHERE pub_year = 2016 AND prov IN ('广东', '广东省')
      AND contains(title, '危险驾驶') AND contains(title, '起诉书')
    """
).fetchone()[0]
print(f"\nsmoke: 2016 (pub year) 广东 危险驾驶 起诉书 by title = {smoke:,}")

# size on disk
total = sum(p.stat().st_size for p in ROOT.rglob("*.parquet"))
print(f"\nparquet size on disk: {total/1e9:.2f} GB")

sys.exit(0 if ok else 1)
