# Reference tables — build and cleaning logic

Two tables, two scripts, one rule: the published CSV is always regenerated from `ref/sources/` by
code, never edited by hand. Diagrams are Mermaid (rendered on GitHub; readable as text elsewhere).

## A. Region codes — `scripts/ref_regions.py` → `ref/region_codes.csv`

```mermaid
flowchart TD
  subgraph SRC["ref/sources/"]
    A1["areacodes (vendored, CC0, pinned commit)<br/>data/YYYY.txt 1981–2025 · result.csv · errata · diffs"]
    A2["Workbook (Zhihu compiler)<br/>sheet 1980-2024民政部原始数据 · sheet 汇总表"]
    A3["GB/T 2260-2007 (PDF) — official snapshot 2007-10-31"]
    A4["GB/T 2260-2007 第1号修改单 2016 (DOCX) — official"]
  end

  A1 -->|"result.csv: code · name · level · parent<br/>year_from (incl.) · year_to (excl.) · successors"| B1["backbone rows<br/>source = areacodes · is_official = 1"]
  A2 -->|"汇总表 codes absent from backbone"| Q{"数据来源 ?"}
  Q -->|"国标"| B2["aggregate codes (xx0100 市辖区 …)<br/>source = workbook_gb · is_official = 1"]
  Q -->|"非官方"| B3["黑区 codes seen on ID cards<br/>source = workbook_unofficial · is_official = 0<br/>ID decoding only"]
  Q -->|"民政部"| B4["drop — already in backbone"]
  B1 --> C["ref/region_codes.csv (7,302 rows, UTF-8 BOM)"]
  B2 --> C
  B3 --> C

  A1 --> V1["year-by-year diff of codes and names<br/>(two independent transcriptions)"]
  A2 --> V1
  A3 -->|"pypdf · parse 表1–表32"| V2["2007 snapshot vs areacodes 2007"]
  A4 -->|"parse 撤销 / 设立 / 更名"| V3["all 2016 changes present?"]
  C --> V4["ID prefixes in the corpus:<br/>coverage per layer"]
  V1 --> R["ref/region_codes_validation.md"]
  V2 --> R
  V3 --> R
  V4 --> R
```

Rules worth remembering

- Validity windows come from areacodes unchanged; `year_to` is exclusive; a code can have several
  rows (name/parent change, or re-established after abolition).
- Pre-1995 differences between the two transcriptions are documented re-codings in
  `areacodes/errata.md` (county-level cities 1990–1994: xx90xx → xx81–99). Both forms stay reachable.
- 1980–1983 rows are not used for ID decoding (ID cards start in 1984; 民政部 back-filled those years).
- Nothing from the workbook's derived sheets (汇总表 successors, 代码变迁) enters the table except the
  two flagged extra layers above.

## B. Charges — `scripts/ref_charges.py` → `ref/charges_std.csv`, `ref/charge_aliases.csv`

```mermaid
flowchart TD
  subgraph SRC["ref/sources/user_supplied/ (Wikisource EPUBs of the official texts)"]
    E0["法释〔1997〕9号 — base list"]
    E1["补充规定 (一)…(七) — 2002 … 2021"]
  end

  E0 -->|"unzip XHTML → text<br/>chapter / section / article / charge lines"| P0["412 base names · valid_from 1997-12-16"]
  P0 --> X["ERRATA (Wikisource typos):<br/>公文文→公文 · 剌探→刺探 · 抵押税款→抵扣税款"]
  E1 -->|"per block: article line · 修正案 source line<br/>charge lines · 取消…罪名 lines"| P1["apply instruments in date order"]
  X --> P1

  P1 --> Q1{"added name already open?"}
  Q1 -->|"yes"| U["update article reference"]
  Q1 -->|"no"| N["new row · valid_from = instrument date"]
  P1 --> Q2{"取消 name found?"}
  Q2 -->|"exact"| CL["close row: valid_to · abolished_by · replaced_by"]
  Q2 -->|"fuzzy ≥ 0.8 (帐/账, missing 、)"| CL
  Q2 -->|"not found"| LG["log only (最高检 1997 names, never in 最高法 list)"]
  P1 --> Q3{"older open name on the same article_key<br/>(条 + 之N + 款) with similarity ≥ 0.6 ?"}
  Q3 -->|"yes"| IM["close as implicit rename"]
  N --> T["ref/charges_std.csv<br/>540 names ever · 483 in force after 2021 · is_duty_crime = ch. 8–9"]
  U --> T
  CL --> T
  IM --> T

  T --> D["alias dictionary D (keyed 、-insensitive)<br/>official names + selective sub-names<br/>+ bracket variants 国（边）境 + MANUAL_ALIASES"]
  K[("corpus parquet")] -->|"regex: 应以…罪追究 · 涉嫌…罪"| RS["raw strings + document counts"]
  RS --> SEG["per string, try in order:<br/>1 raw · 2 、-list sharing one 罪 (抢劫、盗窃罪)<br/>3 strip 犯有/犯/罪罪 · 4 X犯罪 → X罪"]
  D --> SEG
  SEG -->|"longest match over D<br/>unique-prefix fallback for truncations"| OK["ref/charge_aliases.csv<br/>method: exact · segmented · normalised · partial"]
  SEG -->|"residue"| UN["ref/charges_unmatched.csv — manual queue"]
  T --> R2["ref/charges_validation.md"]
  OK --> R2
  UN --> R2
```

Selective sub-names (how `贩卖毒品罪` finds `走私、贩卖、运输、制造毒品罪`)

1. Split the official name at 、; peel a leading modifier (非法 / 违法 / 擅自 …); read items as verbs
   while they are in the verb lexicon; the first verb+object item (储存枪支) marks where objects start.
2. Generate: every ordered subset of verbs (plus permutations for 2–3 verbs) × one object, where the
   shared head of the first object (国家机关 + 证件) or the shared tail of the last object (公司 + 印章)
   may be distributed, or several objects joined by 、; each item alone (非法控制计算机信息系统罪);
   all with and without the modifier.
3. Official names always win over generated forms when both exist (侮辱罪 stays 第246条).

Order of precedence inside the alias build: exact official name → generated/manual alias →
truncation with a unique prefix → wrapper removal. The 犯罪→罪 rewrite is tried last so that
掩饰、隐瞒犯罪 keeps its truncation match to 掩饰、隐瞒犯罪所得、犯罪所得收益罪.

## C. Conventions shared by both

- Outputs are UTF-8 with BOM so Excel opens them correctly; never save them back from Excel.
- Each script writes its own `*_validation.md`; the numbers in the data appendix come from there.
- Sources are never modified. Corrections live in code (`ERRATA`, `MANUAL_ALIASES`) and are listed in
  the validation reports.
