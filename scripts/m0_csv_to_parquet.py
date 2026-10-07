#!/usr/bin/env python3
"""
M0 — indictment.csv -> raw.parquet, partitioned by publication year.

Source : ~/indictment_work/raw/indictment.csv   (7 columns, 2,399,736 rows)
Target : ~/indictment_work/raw.parquet/pub_year=YYYY/part-0.parquet

Design decisions (see 方案 v2):
  * Python csv module streaming + pyarrow batch writes. The csv module was
    verified to parse every row with zero errors; DuckDB's CSV reader is not
    used here so quality flags are computed in the same pass.
  * doc_id = sha1(url). url is 100% present and unique; row numbers are not
    stable across re-runs and 文号 is neither complete nor unique.
  * Partition key is the PUBLICATION year taken from the URL, not the 落款
    year in the `time` column. The two differ by a median of 84 days.
  * Body text is stored as-is. No normalisation at this layer.
  * Nothing is dropped. Problem rows get a bit in `quality_flags`.

Signing date (落款) — two sources, one rule:
  * `time` column, when it is a valid date inside 2010-01-01..2019-12-31
    -> sign_date, date_source = 'time_col'
  * otherwise the body is scanned (scripts/signdate.py) for the date printed
    under the prosecutor's signature
    -> sign_date (day precision) or only sign_year / sign_month, date_source = 'content'
  The body is scanned for every row; when both sources exist and disagree,
  DATE_CONFLICT is set. sign_date_raw always keeps the original string.

Run:
  python3 scripts/m0_csv_to_parquet.py            # full run
  python3 scripts/m0_csv_to_parquet.py --limit 50000   # smoke test
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from signdate import extract_sign_date  # noqa: E402

SRC = Path.home() / "indictment_work" / "raw" / "indictment.csv"
OUT = Path.home() / "indictment_work" / "raw.parquet"
LOG = Path.home() / "indictment_work" / "logs"
EXPECTED_ROWS = 2_399_736
BATCH = 20_000

# ---- quality flag bits ------------------------------------------------------
# Baseline counts for regression checks. The diagnosis baseline for BAD_DATE
# was 16,947 (unparseable strings only); the stricter rule used here (also
# rejects impossible or out-of-range dates) gives 18,158.
BAD_DATE = 1         # `time` column unusable; sign_* then come from the body  (18,158)
MULTI_WENHAO = 2     # >=2 文号 patterns in body, suspect merge               (6,427)
FRAGMENT = 4         # body shorter than 120 chars                            (539)
EMPTY = 8            # body empty after strip                                 (22)
NON_HAN_SCRIPT = 16  # Uyghur / Tibetan / Mongolian body                      (4,444)
SPACED_TEXT = 32     # 字 间 空 格 style text                                   (8,075)
DATE_CONFLICT = 64   # valid `time` column but body shows a different day     (~0.06%)
NO_URL_DATE = 128    # URL carries no YYYYMMDD segment                        (0)

FLAG_NAMES = {
    BAD_DATE: "BAD_DATE",
    MULTI_WENHAO: "MULTI_WENHAO",
    FRAGMENT: "FRAGMENT",
    EMPTY: "EMPTY",
    NON_HAN_SCRIPT: "NON_HAN_SCRIPT",
    SPACED_TEXT: "SPACED_TEXT",
    DATE_CONFLICT: "DATE_CONFLICT",
    NO_URL_DATE: "NO_URL_DATE",
}

RE_URL_DATE = re.compile(r"/(20\d{2})(\d{2})(\d{2})/")
RE_SIGN_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")
RE_WENHAO = re.compile(r"〔\s*\d{4}\s*〕\s*\d+\s*号")
# Arabic script (Uyghur), Tibetan, Mongolian
RE_NON_HAN = re.compile(r"[؀-ۿݐ-ݿༀ-࿿᠀-᢯]")
# five Han characters separated by single spaces, e.g. "浙 江 省 嵊 州"
RE_SPACED = re.compile(r"(?:[一-鿿] ){4}[一-鿿]")

# Plausible 落款 window. The platform ran 2014-10 .. 2019-09; a few documents
# were signed earlier and published late. Anything outside this range (e.g.
# "0201-01-08", "2201-10-04") is a typo and is treated as unusable.
SIGN_MIN = date(2010, 1, 1)
SIGN_MAX = date(2019, 12, 31)

SCHEMA = pa.schema(
    [
        ("doc_id", pa.string()),
        ("row_idx", pa.int64()),
        ("title", pa.string()),
        ("sign_date_raw", pa.string()),
        ("content", pa.string()),
        ("prov", pa.string()),
        ("muni", pa.string()),
        ("county", pa.string()),
        ("url", pa.string()),
        ("pub_date", pa.date32()),
        ("sign_date", pa.date32()),          # final, day precision only
        ("sign_year", pa.int16()),           # filled whenever year is known
        ("sign_month", pa.int8()),           # filled whenever month is known
        ("sign_date_precision", pa.string()),  # 'day' | 'month' | 'year' | NULL
        ("date_source", pa.string()),        # 'time_col' | 'content' | NULL
        ("sign_date_content", pa.date32()),  # what the body scan found (day precision), for audit
        ("text_len", pa.int32()),
        ("quality_flags", pa.int32()),
    ]
)


def parse_sign_date(s: str):
    m = RE_SIGN_DATE.match(s or "")
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if d < SIGN_MIN or d > SIGN_MAX:
        return None
    return d


def parse_pub_date(url: str):
    m = RE_URL_DATE.search(url or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


class YearWriters:
    """One ParquetWriter per publication year, opened lazily."""

    def __init__(self, root: Path):
        self.root = root
        self.writers = {}

    def get(self, year: int) -> pq.ParquetWriter:
        w = self.writers.get(year)
        if w is None:
            d = self.root / f"pub_year={year}"
            d.mkdir(parents=True, exist_ok=True)
            w = pq.ParquetWriter(
                d / "part-0.parquet",
                SCHEMA,
                compression="zstd",
                compression_level=3,
                use_dictionary=["prov", "muni", "county", "sign_date_precision", "date_source"],
                write_statistics=True,
            )
            self.writers[year] = w
        return w

    def close(self):
        for w in self.writers.values():
            w.close()


def flush(batches: dict, writers: YearWriters):
    for year, cols in batches.items():
        table = pa.table(cols, schema=SCHEMA)
        writers.get(year).write_table(table)
    batches.clear()


def new_cols():
    return {name: [] for name in SCHEMA.names}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stop after N rows (smoke test)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if args.out.exists() and any(args.out.iterdir()):
        sys.exit(f"refusing to write into non-empty {args.out}; remove it first")
    args.out.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)

    csv.field_size_limit(sys.maxsize)
    writers = YearWriters(args.out)
    batches = {}  # year -> cols
    n_in_batch = 0

    stats = {
        "rows": 0,
        "per_year": {},
        "flags": {name: 0 for name in FLAG_NAMES.values()},
        "date_source": {"time_col": 0, "content": 0, "none": 0},
        "sign_date_precision": {"day": 0, "month": 0, "year": 0, "none": 0},
        "content_scan_vs_time_col": {"agree": 0, "disagree": 0, "scan_found_nothing": 0},
        "content_has_newline": 0,
        "pub_date_min": None,
        "pub_date_max": None,
        "sign_date_min": None,
        "sign_date_max": None,
    }
    t0 = time.time()

    with open(SRC, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        expected_header = ["title", "time", "content", "prov", "muni", "county", "url"]
        if header != expected_header:
            sys.exit(f"unexpected header {header!r}")

        for row_idx, row in enumerate(reader):
            if args.limit is not None and row_idx >= args.limit:
                break
            if len(row) != 7:
                sys.exit(f"row {row_idx}: expected 7 fields, got {len(row)}")
            title, time_raw, content, prov, muni, county, url = row

            flags = 0
            pub = parse_pub_date(url)
            if pub is None:
                flags |= NO_URL_DATE
                year = 0
            else:
                year = pub.year

            # ---- signing date: column first, body scan as fallback / audit ----
            sign_col = parse_sign_date(time_raw)
            scan = extract_sign_date(content, SIGN_MIN, SIGN_MAX)
            if sign_col is not None:
                sign_date, sign_year, sign_month = sign_col, sign_col.year, sign_col.month
                precision, source = "day", "time_col"
                if scan.date is None:
                    stats["content_scan_vs_time_col"]["scan_found_nothing"] += 1
                elif scan.date == sign_col:
                    stats["content_scan_vs_time_col"]["agree"] += 1
                else:
                    stats["content_scan_vs_time_col"]["disagree"] += 1
                    flags |= DATE_CONFLICT
            else:
                flags |= BAD_DATE
                if scan.precision is not None:
                    sign_date, sign_year, sign_month = scan.date, scan.year, scan.month
                    precision, source = scan.precision, "content"
                else:
                    sign_date = sign_year = sign_month = precision = source = None

            stripped = content.strip()
            tlen = len(content)
            if not stripped:
                flags |= EMPTY
            elif len(stripped) < 120:
                flags |= FRAGMENT

            if len(RE_WENHAO.findall(content)) >= 2:
                flags |= MULTI_WENHAO
            if RE_NON_HAN.search(content):
                flags |= NON_HAN_SCRIPT
            if RE_SPACED.search(content):
                flags |= SPACED_TEXT
            if "\n" in content:
                stats["content_has_newline"] += 1

            doc_id = hashlib.sha1(url.encode("utf-8")).hexdigest()

            cols = batches.get(year)
            if cols is None:
                cols = batches[year] = new_cols()
            cols["doc_id"].append(doc_id)
            cols["row_idx"].append(row_idx)
            cols["title"].append(title)
            cols["sign_date_raw"].append(time_raw)
            cols["content"].append(content)
            cols["prov"].append(prov)
            cols["muni"].append(muni)
            cols["county"].append(county)
            cols["url"].append(url)
            cols["pub_date"].append(pub)
            cols["sign_date"].append(sign_date)
            cols["sign_year"].append(sign_year)
            cols["sign_month"].append(sign_month)
            cols["sign_date_precision"].append(precision)
            cols["date_source"].append(source)
            cols["sign_date_content"].append(scan.date)
            cols["text_len"].append(tlen)
            cols["quality_flags"].append(flags)
            n_in_batch += 1

            # stats
            stats["rows"] += 1
            stats["per_year"][year] = stats["per_year"].get(year, 0) + 1
            for bit, name in FLAG_NAMES.items():
                if flags & bit:
                    stats["flags"][name] += 1
            stats["date_source"][source or "none"] += 1
            stats["sign_date_precision"][precision or "none"] += 1
            if pub is not None:
                if stats["pub_date_min"] is None or pub < stats["pub_date_min"]:
                    stats["pub_date_min"] = pub
                if stats["pub_date_max"] is None or pub > stats["pub_date_max"]:
                    stats["pub_date_max"] = pub
            if sign_date is not None:
                if stats["sign_date_min"] is None or sign_date < stats["sign_date_min"]:
                    stats["sign_date_min"] = sign_date
                if stats["sign_date_max"] is None or sign_date > stats["sign_date_max"]:
                    stats["sign_date_max"] = sign_date

            if n_in_batch >= BATCH:
                flush(batches, writers)
                n_in_batch = 0
            if stats["rows"] % 100_000 == 0:
                el = time.time() - t0
                print(f"{stats['rows']:>10,} rows  {el:7.0f}s  {stats['rows']/el:,.0f} rows/s", file=sys.stderr, flush=True)

    flush(batches, writers)
    writers.close()

    stats["elapsed_s"] = round(time.time() - t0, 1)
    for k in ("pub_date_min", "pub_date_max", "sign_date_min", "sign_date_max"):
        stats[k] = stats[k].isoformat() if stats[k] else None
    stats["per_year"] = {str(k): v for k, v in sorted(stats["per_year"].items())}

    if args.limit is None and stats["rows"] != EXPECTED_ROWS:
        stats["ROW_COUNT_MISMATCH"] = f"expected {EXPECTED_ROWS}, got {stats['rows']}"

    summary_path = LOG / ("m0_summary.json" if args.limit is None else "m0_summary_smoke.json")
    summary_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    if "ROW_COUNT_MISMATCH" in stats:
        sys.exit(2)


if __name__ == "__main__":
    main()
