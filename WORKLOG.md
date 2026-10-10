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

## 2026-10-07 — Step 3 (M1): Tier 0 rule extraction, full corpus

**Purpose.** Turn every document into the normalised tables of the plan (`documents`, `defendants`,
`charges` in two stages, `statutes`, `prosecutors`), joined to the R reference tables, with names
physically separated into a sensitive store. Rules only; nothing guessed; every failure flagged.

**Design.**
- `scripts/m1_rules.py` — pure functions over one document. Anchors (院名 → 文书类型 → 文号 → 被告人块
  → 本案由 → 查明 → 证据 → 本院认为 → 此致 → 署名 → 附) locate regions; every extractor works on its
  region only, so "检察长批准延长羁押" in the procedure paragraph can never become a signature.
- `scripts/m1_regions.py` — procuratorate name → `ref/region_codes.csv` (codes valid 2014–2019), full
  names before short names ("衡阳县" is the county, not 衡阳市 + 县), the crawler's `prov` column only as
  a province hint, 分院 / 铁路 / 林业 / 农垦 / 开发区 typed separately (`proc_special`, `proc_level`).
- `scripts/m1_extract.py` — one task per raw row group (124), 10 forked workers, each writes its own
  part files. Charge strings resolve through `ref/charge_aliases.csv` first and the same segmenter
  otherwise (`ref_charges.py` was refactored to expose `build_dictionary` / `make_segmenter` /
  `resolve_charge_string`; alias outputs unchanged as row sets).
- `scripts/m1_verify.py` — the plan's checks; `scripts/test_m1_rules.py` — 22 regression tests.

**Rules worth knowing.**
- Defendant blocks: labels 被告人 / 被不起诉人 / 犯罪嫌疑人 / 被告单位 / 被告; a later paragraph that
  re-opens with the same name (江苏 layout: identity paragraphs first, then "被告人X因涉嫌…" per
  defendant) is merged into that defendant, not counted again. Unlabelled identity lines
  ("林某某，男，…") are a fallback. A start with no person cue within 80 chars is rejected (WEAK_BLOCK).
- Birth year only from 出生/生/masked-month forms; a bare "2016年4月1日" in the block is a conviction or
  detention date. ID digits 7–10 fill the gap when the text year is masked; "46岁" gives
  `birth_year_source = 'age'`.
- Coercive measures: each keyword takes the nearest date to its left in the same sentence; 同年/同月/
  同日/次日 resolve against the last absolute date; sentences about prior convictions (判处, 劳动教养,
  刑满, 曾因 …) are skipped. `date_arrest_approved` is the date before 批准/决定 only when 逮捕 follows
  without an intervening comma or date.
- Charges: stage 涉嫌 from the defendant block (attribution explicit for free) and the 本案由 paragraph;
  stage 起诉 from 本院认为 per sentence (`应当以X罪追究`, else `构成X罪`), names resolved to blocks by exact
  or unique-prefix match; `attribution` = explicit / sole / unassigned. Junk strings (犯罪, 共同犯罪, 以罪
  …) are dropped before resolution. Multi-name strings become several rows with `is_multi`.
- Statutes: every `《…》第X条[之N][第Y款][第Z项]` run, law normalised to 刑法 / 刑诉法 / other (typos
  中国人民共和国刑法, 刑罚, 邢法; tabs inside the brackets; 第第…, missing 条 or 第 tolerated), tagged with
  the section it sits in.
- Non-prosecution reason from the 刑诉法 article cited in the decision sentence, mapped for the 1996 /
  2012 / 2018 numberings (173.1 / 177.1 法定, 173.2 / 177.2 相对, 171.4 / 175.4 存疑, 271 / 282 附条件);
  keywords only as a last resort (`nonpros_source` says which).
- Minor gate (plan §未成年人): `age_at_offense` = first date in the 查明 paragraph − birth year; because
  birth month/day are masked the true age is age or age−1, so ≤ 17 → `minor`, 18 → `ambiguous`,
  ≥ 19 → `adult`, missing → `undetermined`. `facts_date_min` is kept for a stricter gate.

**Run.** 2,399,736 documents in 192 s (10 workers), 0 errors. Smoke iterations on a 92,687-document
sample (one row group per year) drove the rules; three full runs in total.

**Outputs** (`~/indictment_work/docs.parquet/<table>/pub_year=YYYY/part-NNN.parquet`, zstd, 750 MB):
| table | rows | notes |
|---|---|---|
| documents | 2,399,736 | one per raw row; 53 columns incl. region codes, 文号 parts, flags |
| defendants | 2,983,762 | `def_id = doc_id:seq`; 11,185 units (`def_type`) |
| charges | 5,836,991 | doc × def × charge × stage; 0.43 % unmapped to the standard table |
| statutes | 7,045,011 | 刑法 ≈ 4.4 M, 刑诉法 ≈ 2.6 M, other ≈ 48 k references |
| prosecutors | 2,496,665 | 43,756 distinct unmasked names; 1.96 % masked |
| sections | 18,248,259 | (sect_type, start, end) offsets into the normalised text |
Sensitive: `~/indictment_work/sensitive.parquet/defendants_sensitive/` (name_raw, name, alias, id_raw;
144 MB) — excluded from git and Dropbox by the existing `.gitignore` rules.
Logs: `~/indictment_work/logs/m1_run.log`, `m1_summary.json`, `m1_verify.log`, `m1_dashboard.csv`.

**Verification (`m1_verify.log`, RESULT PASS).**
| check | result |
|---|---|
| Rows / doc_id / def_id unique, FK integrity, errors | exact / yes / yes / 0 / 0 |
| 文号 | 99.61 % (baseline 97.4) |
| 检察院名称 | 99.71 % (98.7) |
| 强制措施, any defendant | 98.58 % (98.45) |
| 罪名并集 | 99.32 % (99.53) |
| 身份证前6位, any defendant | 97.17 % (96.3) |
| 检察官署名 | 89.04 % of all documents = 99.1 % of 起诉书 (74.0 was an upper bound incl. noise) |
| 法院名称 | 89.45 % (99.5 % of 起诉书; 不起诉决定书 have none) |
| 被告人分块 | 99.45 % (87.9) |
| 刑法条文 | 97.2 % (99.7 counted any 第X条; `has_statute` any law = 99.74 %) |
| 出生年份, any defendant | 91.6 % — **ceiling**: 94 % of the misses are masked years (`****年`, `19**年`), the 99.7 baseline matched detention dates in the same segment |
| End-to-end: 2016 广东 危险驾驶 起诉书 | tables 10,050 vs raw grep 9,494 (ratio 1.06; grep misses untitled docs) |
| Statute network sanity | 133条之一 is #3 by documents (after 67 and 264), 刑诉法 separate |
| Stage change (primary 涉嫌 ≠ 起诉) | 4.73 % of 2.64 M defendants — inside the plan's 3–6 % band; top: 寻衅滋事→故意伤害 7,367, 开设赌场→赌博 5,778, 诈骗→合同诈骗 3,910 |
| Province × year dashboard | only 新疆 2016 (Uyghur-script documents) and 海南 2015 (n = 218) fall 20 pp below the mean |

Other numbers: multi-defendant documents 298,011 (12.4 %; plan 287,179); n_defendants = 0 only 13,223
(0.55 %; the plan's 290,989 were mostly 被不起诉人 blocks); 认罪认罚 by signing year 2016 0.1 % → 2017
8.1 → 2018 15.6 → 2019 34.1; non-prosecution reasons 相对 174,700 / 存疑 46,144 / 法定 11,406 (97.4 %
from the cited article); minors 2,687 + 23,412 ambiguous; `date_detain` after `sign_date` 0.38 %
(source typos / prior-case dates), `days_to_arrest` negative 0.28 %.

**Decisions made during the run.**
- 刑事抗诉书 (5,984) and 刑事申诉复查决定书 (5,820) get documents rows but no defendant parsing (their
  structure differs; 0.5 % of the corpus).
- `documents.title` is kept as-is (it carries unmasked names in ~6 % of documents) because
  `docs.parquet` never leaves the laptop; any copy to Dropbox must drop or mask it.
- Fields not in the plan's schema were not extracted (prior convictions, residence text, 速裁/简易程序).
- `charges_std.csv` in the repo had Excel-mangled dates (`12/16/97`); regenerated with ISO dates.
  Alias build extended (29 manual aliases, `的犯罪` / `分别以` forms): 99.57 % of mentions resolved
  (was 99.48).

**Open items.**
- [ ] Commit `scripts/`, `ref/`, `WORKLOG.md` (nothing committed in this step).
- [ ] Delete `~/indictment_work/raw.parquet.prev` (1.4 GB, superseded) — still pending from Step 1.
- [ ] Remaining 0.43 % unmapped charge strings (`XX罪`, truncations such as 危险罪 / 贩卖罪) stay NULL by
      design; nothing further to gain without guessing.
- [ ] M2 gold set will measure precision; the rates above are recall-style hit rates only.

---

## 2026-10-07 — Direction: 条块共谋网络 (replaces M1.5 as the next step)

Decision (user): skip M1.5 (籍贯同配性) for now; the research interest is 共谋 and 差序 on the
duty-crime subset, with nodes = 条块 positions (system × level × rank), not persons (names are masked).
Written up in [条块共谋网络方案v1.md](条块共谋网络方案v1.md) with M1-measured numbers.

Two corrections to 方案 v2 came out of the measurement: 行贿 and 受贿 are almost always prosecuted
separately (only 549 documents hold both), so cross-case edges live in the 查明事实 narrative
("另案处理" in 27.3 % of duty-crime documents, counterpart positions in 65.7 %); and position
sequences (调任/升任) appear in only 2.6 % of documents, so the "职务流动层" cannot be the backbone —
the collusion layer (in-case + narrative) is. The duty-crime subset effectively ends in 2017 (监察体制
改革): 2015 11,100 / 2016 12,676 / 2017 11,172 / 2018 4,161 / 2019 477 by signing year.

2026-10-09 — taxonomy fixed by the user (plan §5): ten systems as proposed; six levels with 县 and
区 kept apart; rank only 正职 / 副职 / 一般人员; title (`position_title`) and organisation
(`position_org`) kept verbatim in their own fields. Node granularity still open.
Same day, two additions: positions become their own table (`positions`, one row per def_id ×
`position_seq`, 0 = first described; 26.5 % of duty-crime position strings carry more than one
position) and each position records the place written in the organisation name
(`position_prov/city/county/region_code`, resolved through `ref/region_codes.csv`; masked or absent
places stay NULL, the prosecuting office's location is never used as a fill-in). On a 3,000-string
sample the organisation text alone resolves to county 41 %, city 9 %, province 10 %.

---

## 2026-10-09 — Step 4 (P1): positions table and L1 in-case edges, duty-crime subset

**Purpose.** Plan v1 §9 P1: parse every duty-crime defendant's position string
(`defendants.occupation_raw`) into the `positions` table (one row per position, taxonomy of §5), and
build the L1 in-case edges (`network_edges`). Rules only, every decision traceable through a `_rule`
column; nothing is filled from the prosecuting office.

**Subset.** Documents in which at least one charge row (either stage, any attribution) resolves to a
chapter 8/9 charge: 51,012 documents, 65,711 person defendants (units excluded, plan §10.3). The plan's
figures (50,907 / 65,913) were a slightly different cut; this definition is now the one in code.
56,986 defendants (86.7 %) have a position string; 8,725 have none; 699 strings contain no position
at all (曾用名, 捕前住, 汉族, 正科级干部 …).

**Design** (`scripts/p1_positions.py`, pure functions; `scripts/p1_build.py`, driver; two hand-maintained
lexicons `ref/position_lexicon.csv` (481 organisation keywords, SOE / private cues) and
`ref/position_titles.csv` (≈300 titles → rank + system hint; status words)).
- String → fragments on "；" (M1 joined identity fields with it) → non-position fragments dropped
  (曾用名, 捕前住, 证件, 非人大代表, 汉族, hukou-like "陕西西安人", grades, dates) → pieces on 、/兼/并任.
  A titled piece without an organisation inherits the previous one ("党组书记、局长", "教师兼出纳");
  an organisation-only piece before a titled one inherits the title ("A公司、B公司实际控制人"); a short
  sub-unit after 兼 is attached to the previous organisation ("副主任兼妇女主任"); a time marker covers
  the whole list ("现任A兼B").
- Per piece: time marker (原任 / 现任 / 案发时 / 不明, also "局原副局长", "村原党支部书记") → title = the
  last title word, verbatim (副主任科员, 调研员, 主任医师 are their own titles) → organisation = the rest,
  verbatim → system from the rightmost organisation keyword; **attached organs** (党组, 党委, 工会, 纪检,
  分局, 药房, 合作社) take the system of the host to their left ("局党组" → 政府, "公司党委" → 企业,
  "村党支部" → 村级), standalone → their own default; keywords inside a place name are ignored
  (金山店镇 ≠ 店); overlapping keywords resolve longest-first (人民政府 over 民政). Level from the deepest
  place token; named tokens with a coded suffix (省/市/县/区/旗/州) are validated against
  `ref/region_codes.csv`, 县级市 → 县, masked "**市" stays 市 (flagged `suffix_masked`); village
  organisations are always 村社区. Rank from the title (副 prefix → 副职; 非领导职务 → 一般人员).
- Firms: 国企 / 民营企业 from a name cue when there is one (国网, 烟草, 供销, 个体 …); otherwise from the
  defendant's own charges — a charge that requires 国家工作人员 (贪污, 受贿, 挪用公款, 滥用职权 …) → 国企,
  a briber-side or non-state charge (行贿, 单位行贿, 非国家工作人员受贿, 职务侵占 …) → 民营. **73 % of firm
  positions (11,412 of 15,732) are decided by charge**, marked `charge:公职` / `charge:行贿` in
  `position_sys_rule`; 266 stay NULL (`company_unknown`).
- Offence-time row (plan §5): 案发时 > 原任 > 不明 > 现任, then first described; status rows (农民, 无业)
  and 人大代表 / 政协委员 memberships never beat a job; `time_ambiguous` when an unmarked row wins among
  several jobs.
- Place of the organisation: only what the name says, resolved with the prosecuting province as a
  disambiguation hint; masked → NULL.
- `m1_regions._short` had a bug (greedy `[一-龥]{1,4}族` ate the base of 贡山独龙族怒族自治县, 德宏傣族景颇族
  自治州, 黔东南苗族侗族自治州 …) — fixed with the explicit list of ethnic groups; **M1 re-run** (192 s),
  `m1_verify` PASS, 检察院名称 99.71 % unchanged.

**Run.** 9 s for the subset. 21 unit tests (`scripts/test_p1_positions.py`) pass; M1 tests pass.

**Outputs** (`~/indictment_work/docs.parquet/`, zstd):
| table | rows | notes |
|---|---|---|
| positions | 71,800 | one per def_id × position_seq; 28 columns incl. `*_rule` / `*_source` provenance, `raw_start/raw_end` offsets inside `occupation_raw`, `n_positions` |
| network_edges | 34,273 | L1, all pairs of person defendants in 7,950 multi-defendant documents; node columns split (sys/level/rank × a/b), `directed` for 行贿→受贿 pairs (276), `charge_group(_a/_b)`, `tiao_kuai`, `weight = 1/(n−1)` |
Logs `logs/p1_run.log`, `p1_summary.json`. Manual-check sample `gold/p1_positions_check200.csv` (200
random defendants, seed 20261009, with empty `ok_*` columns to fill).

**Results.**
| offence-time rows (one per parsed defendant) | 56,287 |
|---|---|
| system known | 94.4 % |
| level known | 79.9 % |
| rank known | 64.5 % (24 % of rows have a masked title "**", 9 % are status words) |
| title known / masked | 73.2 % / 24.2 % |
| place resolved to any code / to county | 74.2 % / 47.5 % |
| time marker 不明 / 原任 / 案发时 / 现任 | 57 / 34 / 6 / 3 % |
| defendants with ≥ 2 positions | 12,066 (21.4 %) |

System of the offence-time position: 政府部门 17,953 · 村级组织 9,211 · 国企 6,586 · 民营企业 6,138 ·
事业单位 5,282 · 无公职 3,586 · 公检法司 1,891 · 党委系统 1,237 · 金融机构 714 · 人大政协 518 · NULL 3,171.
Distinct nodes: 133 full sys × level × rank combinations (211 counting partial ones). Largest:
村级组织|村社区|正职 4,473; 无公职 3,586; 政府部门|县|正职 2,184; 村级组织|村社区|一般人员 2,055;
政府部门|县|一般人员 1,763; 政府部门|县|副职 1,161; 政府部门|乡镇街道|一般人员 1,003.

Edges: both systems known on 25,410 (74 %), all three dimensions on both sides 10,253; 30,266 edges fall
in the 2014–2017 window. Most frequent system pairs: 村级–村级 5,415 · 政府–政府 3,662 · 无公职–无公职
3,638 · 国企–国企 2,057 · 事业–事业 1,231 · 政府–无公职 1,038 · 民营–民营 958 · 无公职–村级 903 ·
政府–村级 819 · 国企–民营 591. `tiao_kuai` has four values — 同条 (same system, different level) 548,
同块 (same level, different system) 851, 同条同块 (same system and level) 11,568, 跨 1,874 — and is NULL on
19,432 edges because 无公职 / status rows carry no level by design; P2 should also compute a system-only
version. Directed 行贿→受贿 pairs inside one document: 民营→政府 49, 无公职→政府 32, 民营→国企 24.

**Verification.** The plan asks for 200 strings checked by hand. I read the 200-defendant sample myself
(259 rows) and fixed the systematic errors it showed (党组 under a 委, 药房 inside a 医院, 村民 after a
place, 和 inside place names such as 永和县, grades like 正科级 inside the string, parenthetical "；"
splits, 安监 vs 监狱 ties, 案发前曾任 downgraded to 原任, 人大代表 winning the offence-time slot …). On the
regenerated sample my own reading puts the offence-time row at roughly: system 96–97 %, level 96–97 %,
rank ≈ 99 %, title/organisation boundary ≈ 97 %, splitting/time ≈ 97 %. **This is a self-assessment, not
the independent check the plan calls for** — the CSV is ready for that.

**Known limitations (documented, not fixed).**
- Level is the deepest *named* place: "吉安市**局泰和分局" → 市 although 泰和 is a county; a firm named
  "江西**有限公司" gets level 省. Both are flagged by `position_level_source`.
- "**院", "**部", "**处", "**组" alone give no system (院 may be 法院 / 医院 / 学院); 4,348 rows have no system.
- Masked "**市" cannot be told from a 县级市; `proc_level` of the document is available as a proxy at
  analysis time.
- 常委 / 委员 of a party committee are 一般人员 under the literal "正副 in the unit" rule.
- The charge-based firm ownership makes 国企 / 民营 partly endogenous to the charge; robustness runs in P2
  should drop firm nodes or merge them.

**Open items.**
- [ ] Fill `ok_*` in `~/indictment_work/gold/p1_positions_check200.csv` (independent accuracy figures).
- [ ] Node granularity (plan §10.2): with rank missing on 35 % of rows, start P2 on system × level (60 cells,
      ~50 occupied) and use the three-way nodes as a robustness check.
- [ ] Commit `scripts/`, `ref/`, `WORKLOG.md` — nothing has been committed since Step 0.
- [ ] Still outstanding: delete `~/indictment_work/raw.parquet.prev`; offline backup of the CSV.

---

## 2026-10-10 — Step 5 (P2): first 条块 position network from the L1 edges

**Purpose.** Plan v1 §7 / §9 P2: centralities, 条内 / 块内 shares and a two-mode null model on the
in-case edges, to judge whether the line is worth P3–P5.

**Design** (`scripts/p2_network.py`, 112 s).
- Universe: 2014–2017 signing years, defendants whose offence-time position has a known system, in
  documents with ≥ 2 such defendants: 5,661 documents, 16,031 defendants. Node = system × level, cells
  with < 30 defendants merged into the system's 层级不明 cell; 无公职 is one node → 47 nodes.
- Projection: every within-document pair is an edge of weight 1/(n−1) (plan §4), so each document
  contributes n/2 regardless of size; W_docs (documents in which the pair co-occurs) also reported.
- Null model: position labels permuted across defendants within each signing year (document sizes,
  node frequencies, year mix fixed), 1,000 draws; z-scores for every pair weight, for weighted degree
  (self-loops excluded), betweenness (distance = 1/weight) and for the 条块 shares.
- 条块 of a pair: 同条同块 (same cell) / 同条 (same system, other level) / 同块 (same level, other
  system) / 跨 / 层级不明; shares overall and by level, charge group (both sides), year, court level;
  also the conditional split 条 : 块 : 跨 among pairs that leave the cell.
- Variants: no documents with ≥ 8 defendants, all years, basic courts only, municipal and above
  (n = 348, too small), without charge-inferred firm nodes, three-way nodes (142 cells).
- Outputs in the repo, aggregates only: `results/p2/` — `p2_tables.md` (tables A–H), one-page
  conclusions `P2_summary.md`, CSVs (`p2_nodes_*`, `p2_pairs_sys_level`, `p2_sys_matrix`,
  `p2_level_matrix`, `p2_tiaokuai`, `p2_robustness`, `p2_bribery_directed`), figures `fig1_sys_heatmap`
  (system × system obs/expected), `fig2_network` (条块 grid: columns = systems, rows = levels, vertical
  edges = 条, horizontal = 块, red/blue = above/below the null), `fig3_tiaokuai`, `fig4_degree_z`.
  `logs/p2_summary.json`.

**Results** (details and numbers in `results/p2/P2_summary.md`).
| | observed | null |
|---|---|---|
| share of pair weight inside one cell (same system, same level) | 0.66 | 0.13 (z +230) |
| share 跨 (both differ) | 0.09 | 0.42 |
| among pairs leaving the cell, 条 : 块 : 跨 | 26 : 25 : 49 | 14 : 7 : 79 |
| 块 / 条 / 跨 relative to null | 3.5× / 1.9× / 0.6× | |
- Within-cell share 0.65–0.69 in every variant; the 块 > 条 > 跨 ordering holds in every year, court
  level and variant. 贿赂 and 渎职 pairs are the most 块 (4.5×, 4.4×), 贪污挪用 the most cell-bound.
  乡镇街道 is the most open level (32 % within cell; its outside edges are 61 % 跨, mostly to village
  organisations — the largest cross edge of the network, yet 0.59× the null).
- 村级组织 is its own cluster (self 2.76× expected, 0.07–0.54× with every other system except 党委系统
  1.08×; betweenness z −189). Bridges by betweenness z, consistent across variants: 无公职 (+17…+20),
  政府部门 at 区 / 乡镇街道 / 市 / 县 (+11 / +10 / +8 / +5), 国企|省 (+11), 国企|市 (+6); 事业单位|不明
  (+13) is an unknown-level artefact. No position type co-defends across systems at the random rate;
  the least segregated are party-committee cells (0.64–0.81× expected), the most 公检法司 (0.21–0.29×).
- Over-represented pairs are same-place party / government / institution / SOE combinations
  (党委|乡镇街道–政府|乡镇街道 3.8×, 党委|区–政府|区 5.4×, 政府|省–事业|省 6.8×, …); under-represented are all
  village pairs and the vertical government pairs 政府|县–政府|区 (0.11×), 政府|市–政府|县 (0.20×).
- Hypothesis "国企 as bridge between government and firms": weak (positive betweenness at 省 / 市, but
  国企–政府 0.45×, 国企–民营 0.64×). Directed 行贿→受贿 in-case pairs: 276 — not a bribery network.

**Decisions / caveats recorded.** Degree z is negative for every node (the network is homophilous), so
the node table reports `self_share` and the cross-system obs/exp ratio instead. The null model does not
control geography (same place ≈ same cell for village cadres); a province- or court-stratified
permutation is the first item for P5. 15 % of weight sits in 层级不明 pairs. The L1 layer sees
co-defendants of one indictment, i.e. mostly joint 贪污 / 挪用 / 渎职; cross-case bribery needs L2.

**Open items.**
- [ ] Stratified null model (by province / prosecuting office); use `position_region_code` to separate
      "same place" from "same system".
- [ ] Decide on P3–P5 (gold set + GLiNER for L2 / L3). P2's reading: worth it — the three open questions
      (direction of bribery, who the 无公职 brokers are, whether vertical government collusion is absent
      or split across cases) all need the narrative layer.
- [ ] Commit `scripts/`, `ref/`, `results/p2/`, `WORKLOG.md` (nothing committed since Step 0).

---

## 2026-10-10 — Step 5b (P2b): same place vs same system × level

**Purpose.** P2's first open item. The P2 null model permuted position labels within signing year only,
so "same system, same level" and "same place" were confounded. Separate them with (a) null models
stratified by locality and (b) the place written in the organisation name (`position_region_code`, P1).

**Design** (`scripts/p2b_geography.py`, 85 s; `p2_network.py` gained a `strata` argument on
`Projection` and three region columns in `load`).
- Same universe as P2 (5,661 documents, 16,031 defendants, 47 system × level nodes, weight 1/(n−1)).
- Strata: year (= P2), year × province (121 blocks), year × prefecture city (`documents.city_code`,
  1,027 blocks), year × prosecuting office (`documents.region_code`, 3,362 blocks). Node label and
  organisation place are permuted together. A block with one document cannot be permuted (its pair
  multiset is invariant), so the office level is also reported on "live" blocks (≥ 2 documents) only:
  96 % of pair weight is live at city level, 63 % at office level.
- Place relation of a pair from the two organisation codes at the finest level both resolve to:
  同县 / 同市异县 / 同省异市 / 异省, or 同市(县不明) / 同省(市不明) when only a coarser comparison is
  possible, 不明 otherwise (无公职 and masked organisations). Crossed with the 条块 relation, observed vs
  the year × city null.
- Outputs in `results/p2/`: `p2b_strata.csv`, `p2b_tiaokuai_city.csv`, `p2b_sys_matrix_city.csv`,
  `p2b_nodes_city.csv`, `p2b_place_cross.csv`, `p2b_place_tiaokuai.csv`, `p2b_tables.md` (tables A–G),
  `P2b_summary.md`, `fig5_strata.png`, `fig6_place.png`; `logs/p2b_run.log`, `p2b_summary.json`.

**Results** (`P2b_summary.md`).
| null model | within-cell share expected (obs 0.66) | 跨 expected (obs 0.09) | 条 : 块 : 跨 obs/exp, pairs leaving the cell |
|---|---|---|---|
| year (P2) | 0.13 | 0.42 | 1.9 : 3.5 : 0.6 |
| year × province | 0.16 | 0.40 | 1.7 : 3.1 : 0.6 |
| year × city | 0.27 | 0.34 | 1.6 : 2.4 : 0.7 |
| year × office, live blocks | 0.42 | 0.24 | 1.5 : 1.6 : 0.8 |
- The locality confound is large. Within-cell collusion stays significant under every stratification
  (z +65 … +205) but the excess shrinks from 5× to 1.3–1.6×.
- 块 > 条 > 跨 is mostly locality. Under the city null the unconditional 同块 share equals the null
  (0.048 vs 0.049), 同条 is below it (0.049 vs 0.072), 跨 far below (0.092 vs 0.336). The ordering survives
  only in the conditional sense (given the pair leaves the cell) and nearly vanishes at office level.
- Co-indictment is county-local: of 9,066 pairs with both organisations resolved to a county, 97.4 %
  are the same county; the city null expects ~1,690 weight of 同市异县 pairs, observed 19. So the city
  null still understates locality and the office null (37 % immovable) overstates it; the truth lies
  between (within-cell 1.5–2.5×, 块 conditional 1.4–2.4×).
- System self-pairing ratios fall (党委 13.5 → 3.7, 公检法司 27 → 4.1, 金融 37 → 5.4, 村级 2.8 → 1.9) but
  all stay > 1 and nearly all cross-system pairs stay < 1; village organisations remain a closed cluster.
- Bridges: 无公职 (+19 → +17) and 国企|省 (+9 → +12) are robust to locality; the government cells halve
  (区 +12 → +7, 乡镇街道 +9 → +5, 市 +10 → +3, 县 +4 → +3).
- By organisation place: 同县 pairs carry 48 % of weight, within-cell 0.80 vs null 0.65 (z +16),
  条 / 块 / 跨 1.4 / 1.4 / 0.8 — no 条–块 difference inside a county. Cross-place pairs are < 1 % of weight.

**Decisions / fixes.**
- `universe()` now sorts rows by (doc_id, def_id): DuckDB returned rows in a run-dependent order, which
  made the permutation draws and hence the z-scores vary ±10 % between runs (means and ratios unchanged).
  P2 was re-run (`p2_run.log`, 114 s); `p2_tables.md` and `P2_summary.md` now carry the reproducible
  z-values (within-cell z +230 → +198, 村级 betweenness z −189 → −179, etc.). The previous outputs are
  not kept.
- `P2_summary.md` got a header note pointing to P2b; conclusions 2 and 4 are superseded as described.
- Reading for the research line: the 条块 contrast is not a robust finding on L1; what is robust is
  closure within unit types and segregation between systems, both hard to separate from how 窝案 are
  bundled into one indictment. This supports moving the core question to brokers (P3 on 中间人 triples)
  rather than 条块 shares.

**Open items.**
- [ ] Nothing in P2 separates "same unit" from "same unit type" — needs organisation-name disambiguation
      or L2.
- [x] Committed on 2026-10-10 (one commit on `main` covering everything since Step 0: M1, R updates, P1,
      P2, P2b, notes; `lit/` added to `.gitignore`; not pushed). Earlier steps' "commit" items are closed
      by the same commit.

## Next

P3 as reframed on 2026-10-10: a broker-targeted extraction (行贿人 → 中间人 → 办事官员 position triples
from the 19 % of facts sections with intermediary cues), with the legally labelled roles (介绍贿赂罪,
刑法 388 斡旋受贿, 利用影响力受贿罪; ≈ 800 defendants) as the Tier 0 baseline. The general L2/L3 gold set
is deferred.
