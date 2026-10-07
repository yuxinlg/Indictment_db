# Work log — 检察文书语料结构化

Running record of what was done, why, what came out, and what was left open.
One entry per step. Machine logs for each run are in `~/indictment_work/logs/`.
Plan reference: [方案 v2](users-yuxin-dropbox-indictment-db-md-us-glimmering-creek.md).

---

## 2026-10-07 — Step 0: safety fixes

**Purpose.** Stop the 7 GB raw CSV from ever entering git, and stop syncing ~158k unmasked
defendant names and ID fragments to Dropbox.

**Actions.**
- Moved `indictment.csv` (7,481,133,634 bytes) from the Dropbox project folder to
  `~/indictment_work/raw/indictment.csv`. Same APFS volume, so the move was an instant rename.
- Created the work tree outside Dropbox: `~/indictment_work/{raw, raw.parquet, docs.parquet,
  sensitive.parquet, gold, models, logs}`.
- Created `scripts/` and `ref/` inside the git repo (code and small reference tables are tracked).
- Rewrote the data section of `.gitignore`: `indictment*.csv`, `*.parquet`, `*.duckdb`,
  `/sensitive*`, `/gold/`, `/models/`. Verified with `git check-ignore`; `ref/` and `scripts/`
  remain tracked.

**Outputs.** Updated `.gitignore`; empty work tree; Dropbox folder now holds only documents and code.

**Notes.**
- Dropbox keeps deleted files for ~30 days, so the move is reversible short-term.
- The laptop is now the only full copy of the corpus. An encrypted offline backup is still needed.
- Nothing committed yet.

---

## 2026-10-07 — Step 1 (M0): CSV → Parquet, first build

**Purpose.** Turn the flat CSV into a queryable, partitioned Parquet store with a stable document
ID and quality flags, without touching the body text.

**Actions.**
- Wrote `scripts/m0_csv_to_parquet.py`: Python `csv` streaming + pyarrow batch writes (20k rows),
  zstd, one file per publication year taken from the URL (`/html/YYYYMMDD/...`).
  `doc_id = sha1(url)`. Quality flags as a bitmask.
- Smoke test on 50k rows (3.5 s), then full run.
- Wrote `scripts/m0_verify.py` (DuckDB) implementing the plan's verification checks.

**Results (first build).**
| Check | Result |
|---|---|
| Rows | 2,399,736 (exact) |
| doc_id / url unique | yes |
| pub_date present | 100 %, window 2015-08-04 → 2019-09-10 |
| Run time | 127 s (plan estimate was 25–40 min) |
| Size on disk | 1.42 GB |

Flags vs the full-file diagnosis baseline: EMPTY 22/22, SPACED_TEXT 8,075/8,075, newline-in-body
63,465/63,465 exact; MULTI_WENHAO +75, FRAGMENT −21, NON_HAN_SCRIPT −2 (regex wording differences).

**Decision made during the run.** The smoke test showed `time` values such as `0201-07-26` and
`2201-05-17` that parse as valid dates but are typos. Rule tightened: a column date is accepted only
if it is a real calendar date inside 2010-01-01 … 2019-12-31; otherwise `sign_date` is NULL and
BAD_DATE is set. The raw string is always kept in `sign_date_raw`. Effect: BAD_DATE 16,947 → 18,158
(+1,036 impossible calendar dates, +175 out-of-range typos); mean publication lag 153 → 139 days,
median 84 and p90 312 unchanged.

**Observations for later.**
- 334 documents are signed 2010–2013 (plan assumed 2014 at the earliest).
- 2019 has ~175k rows by sign year but 319k by publication year. Any time series must declare
  which year it uses.

---

## 2026-10-07 — Step 1a: recover signing dates from the body

**Purpose.** For the 18,158 rows with an unusable `time` column, read the 落款 date printed under the
prosecutor's signature instead of leaving it NULL.

**Investigation.**
- Document tails: `此致` → court → `检察员：姓名` → date → `附：` attachments. Anchors present in
  85.6 % (此致) and 88.1 % (附) of all documents.
- Forms found in the bad rows: Arabic `2016年3月21日`; Chinese numerals `二〇一五年十月二十一日`;
  mixed `二0一六年三月`; zero look-alikes (Greek `Ο`, Latin `O`, `○`); blank day `2016年8月  日`;
  blank month `二〇一六年    月   日`; masked `20**年**月**日` / `****年**月**日` / `xxxx年x月x日`;
  typos `2017年1月131日`, `2018年7444月11日`, `205年12月31日`; full-width digits `201７年`;
  `元月` for January; Uyghur `2016-يىلى 04-ئاينىڭ 07-كۈنى`; Kazakh `2016-جىلى10-ايدىڭ11-كۇنى`;
  Korean `2016년9월13일`; missing trailing `日` when the date is the last thing before `附`.

**Actions.**
- Wrote `scripts/signdate.py`. Window = text after the last `此致` (else last 2,500 chars),
  whitespace removed, cut at the first attachment marker (`附：/附件/附注/附项`, `ئىزاھات`, `قوسىمشا`,
  `부록`). Take the LAST date-shaped token in the window; a masked token still counts as the last
  token so narrative dates earlier in the window are never picked up. Report precision
  day / month / year / none. 27 unit tests covering every form above.
- Calibration: on 150,092 rows whose column date is valid, the body date equals the column
  exactly in 99.92 % of cases (92 differ, 24 not found, 1 month-only).
- Throughput 63k rows/s, so the scan runs on every row (~40 s extra) and doubles as an audit.
- Integrated into the converter; schema gained `sign_year`, `sign_month`, `sign_date_precision`,
  `date_source` ('time_col' | 'content'), `sign_date_content`; new flag DATE_CONFLICT (bit 64).
  Rule: column date if valid, else body; partial dates fill `sign_year`/`sign_month` only — no day
  is ever imputed.
- Set the old store aside as `raw.parquet.prev`, rebuilt (163 s), re-verified. Updated
  `m0_verify.py` with provenance and consistency checks.

**Results (second build, current).**
| Recovery on the 18,158 BAD_DATE rows | Rows |
|---|---|
| Full date | 13,443 |
| Month only | 1,334 |
| Year only | 916 |
| Unrecoverable | 2,465 |

Unrecoverable breakdown: 1,147 masked year (`****年`), 1,008 no date in the signature block
(mostly truncated documents), 228 unparseable typos, 82 masked non-Han documents.

Corpus-wide: `sign_date` non-null 99.80 %, `sign_year` non-null 99.90 %. All verification checks
pass; lag statistics unchanged (median 84, p90 312, 0.01 % negative).

DATE_CONFLICT: 1,656 rows (0.07 %) where column and body disagree. 53 by 1–3 days, 185 by 4–31
days, 1,222 by 1–12 months, 196 by more than a year. In 1,654 both dates are ≤ the publication
date, so the column value is kept and the row is flagged. For the 334 documents signed 2010–2013,
the body agrees in 239, disagrees in 63, has no date in 32 — the 2013 cluster is mostly real,
2010–2012 roughly half typos.

**Outputs.**
- `~/indictment_work/raw.parquet/pub_year=2015..2019/part-0.parquet` (1.43 GB), 18 columns.
- `~/indictment_work/logs/m0_run.log`, `m0_summary.json`, `m0_verify.log`, `m0_summary_smoke.json`.
- `scripts/m0_csv_to_parquet.py`, `scripts/m0_verify.py`, `scripts/signdate.py`.

**Open items.**
- [ ] Delete the superseded copy: `rm -rf ~/indictment_work/raw.parquet.prev` (1.42 GB).
- [ ] 3-digit-year typos such as `205年12月31日` could be read as 2015 given the corpus window.
      Decided to ignore for now (228 rows, mixed causes).
- [ ] Commit `.gitignore`, `scripts/`, `WORKLOG.md`.
- [ ] Encrypted offline backup of `~/indictment_work/raw/indictment.csv`.

---

## 2026-10-07 — Step 2 (R): reference tables

**Purpose.** Build the two hand-verifiable, versioned reference tables every later step joins on:
the standard charge list (标准罪名表) and the time-varying administrative-division code table.

**Sources (all under `ref/sources/`).**
- `user_supplied/`: Wikisource EPUB exports of 法释〔1997〕9号 and 补充规定 (一)–(七); the full
  GB/T 2260-2007 standard (PDF, 247 pp.); its 2016 第1号修改单 (DOCX); the Zhihu compiler's workbook
  (民政部 annual lists 1980–2024 transcribed + 国标/非官方 codes); two unused workbooks (address library,
  医保 codes).
- `areacodes/`: vendored snapshot of github.com/yescallop/areacodes at commit `e2c2b2e1…` (2026-02-02),
  CC0. Per-year 民政部 tables 1981–2025, consolidated `result.csv`, year-to-year diffs with 国务院/民政部
  document numbers, and an errata file. See `PROVENANCE.md` there.

**Citation decision.** Region codes: cite 民政部 annual lists, GB/T 2260-2007 and its 2016 amendment as
sources; cite the areacodes repository at the pinned commit for consolidation and corrections; the
workbook is used only as an independent cross-check and is credited for that. Charges: cite each
instrument by its 法释 number and date; Wikisource is the access medium (official texts are public
domain under 著作权法 第五条). mca.gov.cn is not reachable from outside China, which is why the
secondary transcriptions are validated against the two official documents we do have.

**Region table (`scripts/ref_regions.py` → `ref/region_codes.csv`, 7,302 rows).**
- Backbone: areacodes `result.csv` (6,824 rows, validity windows, successor codes).
- Extra layers, flagged in `source` / `is_official`: 378 GB/T aggregate codes (xx0100 市辖区 …) and
  100 non-official "黑区" codes from the workbook, the latter for ID decoding only.
- Validation (`ref/region_codes_validation.md`): areacodes vs workbook identical 2013–2024, ≤ 30 codes
  apart 1995–2012, larger before 1995 due to documented re-coding of county-level cities 1990–1994.
  Against GB/T 2260-2007: 3,224 codes agree, no real discrepancy. Against the 2016 amendment: 240/240
  new codes and 249/250 abolished codes present. Corpus ID prefixes decoded: 99.13 % with areacodes,
  99.83 % with all layers (2.24 M ID numbers).

**Charge table (`scripts/ref_charges.py` → `ref/charges_std.csv`, 540 rows).**
- 1997 base (412 names) + seven supplements applied in order, closing renamed/abolished names with
  `valid_to`, `abolished_by`, `replaced_by`. **483 names in force after 法释〔2021〕2号, matching the
  published count.** 489 names usable somewhere in the 2015–2019 window; 54 duty-crime names.
- Article references keyed with 之N and 款 (`article_key`) so 第125条第1款/第2款 and 第135条/之一 stay
  distinct. Implicit renames (new name re-stated under the same article with no 取消 line, e.g.
  操纵证券交易价格罪 → 操纵证券、期货交易价格罪) are detected and marked "(implicit)".
- Three Wikisource transcription errors corrected and logged: 公文文, 剌探, 抵押税款 (→ 抵扣).
- Alias map (`ref/charge_aliases.csv`, 11,870 raw strings): longest-match segmentation over the
  official names plus 11,439 generated sub-names of selective charges (贩卖毒品罪 → 走私、贩卖、运输、
  制造毒品罪; 伪造国家机关证件罪 → 伪造、变造、买卖国家机关公文、证件、印章罪). **99.48 % of charge
  mentions fully resolved**; 0.51 % residue in `ref/charges_unmatched.csv` (masked XX罪, 犯罪, typos).

**Documentation.** `ref/PIPELINE.md` — flowcharts of both build pipelines and the cleaning rules.
All CSV outputs are written with a UTF-8 BOM (Excel on Mac otherwise misreads them).

**Open items.**
- [ ] Manual pass over `ref/charges_unmatched.csv` (top entries are noise; a few typos like 滥发林木罪
      could be added to MANUAL_ALIASES).
- [ ] Commit `ref/`, `scripts/`, `WORKLOG.md`, `.gitignore` (16 MB in ref/, all under GitHub limits).
- [ ] Still outstanding from Step 1: delete `~/indictment_work/raw.parquet.prev`; offline backup of the CSV.

---

## Next

Step 3 (M1): Tier 0 rule extraction on the full corpus, consuming `ref/charges_std.csv`,
`ref/charge_aliases.csv` and `ref/region_codes.csv`.
