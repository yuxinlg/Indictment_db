# Region code table — validation report

Generated 2026-10-07 by scripts/ref_regions.py.

## 1. Two independent transcriptions of the 民政部 annual lists

areacodes (yescallop, vendored at the commit in ref/sources/areacodes/PROVENANCE.md) vs the workbook sheet 1980-2024民政部原始数据 (compiled independently from the same 民政部 pages).

| year | areacodes | workbook | in both | only areacodes | only workbook | same code, name differs |
|---|---|---|---|---|---|---|
| 1981 | 2,640 | 3,117 | 2,515 | 125 | 602 | 542 |
| 1982 | 2,651 | 3,141 | 2,639 | 12 | 502 | 12 |
| 1983 | 3,129 | 3,125 | 3,092 | 37 | 33 | 36 |
| 1984 | 3,169 | 3,165 | 3,146 | 23 | 19 | 9 |
| 1985 | 3,185 | 3,181 | 3,158 | 27 | 23 | 10 |
| 1986 | 3,188 | 3,184 | 3,165 | 23 | 19 | 12 |
| 1987 | 3,185 | 3,181 | 3,167 | 18 | 14 | 12 |
| 1988 | 3,199 | 3,194 | 3,181 | 18 | 13 | 10 |
| 1989 | 3,199 | 3,195 | 3,182 | 17 | 13 | 8 |
| 1990 | 3,203 | 3,199 | 3,094 | 109 | 105 | 8 |
| 1991 | 3,205 | 3,201 | 3,087 | 118 | 114 | 7 |
| 1992 | 3,206 | 3,202 | 3,058 | 148 | 144 | 7 |
| 1993 | 3,204 | 3,200 | 3,001 | 203 | 199 | 5 |
| 1994 | 3,212 | 3,208 | 2,956 | 256 | 252 | 5 |
| 1995 | 3,217 | 3,213 | 3,193 | 24 | 20 | 9 |
| 1996 | 3,227 | 3,223 | 3,205 | 22 | 18 | 7 |
| 1997 | 3,230 | 3,225 | 3,215 | 15 | 10 | 5 |
| 1998 | 3,230 | 3,225 | 3,215 | 15 | 10 | 5 |
| 1999 | 3,226 | 3,220 | 3,210 | 16 | 10 | 5 |
| 2000 | 3,231 | 3,225 | 3,218 | 13 | 7 | 4 |
| 2001 | 3,230 | 3,224 | 3,201 | 29 | 23 | 4 |
| 2002 | 3,229 | 3,223 | 3,203 | 26 | 20 | 4 |
| 2003 | 3,231 | 3,225 | 3,223 | 8 | 2 | 3 |
| 2004 | 3,232 | 3,226 | 3,224 | 8 | 2 | 1 |
| 2005 | 3,232 | 3,226 | 3,225 | 7 | 1 | 1 |
| 2006 | 3,230 | 3,224 | 3,223 | 7 | 1 | 1 |
| 2007 | 3,229 | 3,223 | 3,222 | 7 | 1 | 0 |
| 2008 | 3,229 | 3,223 | 3,222 | 7 | 1 | 0 |
| 2009 | 3,228 | 3,222 | 3,222 | 6 | 0 | 0 |
| 2010 | 3,226 | 3,220 | 3,220 | 6 | 0 | 0 |
| 2011 | 3,222 | 3,216 | 3,216 | 6 | 0 | 0 |
| 2012 | 3,219 | 3,216 | 3,216 | 3 | 0 | 0 |
| 2013 | 3,220 | 3,220 | 3,220 | 0 | 0 | 0 |
| 2014 | 3,221 | 3,221 | 3,221 | 0 | 0 | 1 |
| 2015 | 3,218 | 3,218 | 3,218 | 0 | 0 | 1 |
| 2016 | 3,219 | 3,219 | 3,219 | 0 | 0 | 0 |
| 2017 | 3,219 | 3,219 | 3,219 | 0 | 0 | 0 |
| 2018 | 3,218 | 3,218 | 3,218 | 0 | 0 | 0 |
| 2019 | 3,213 | 3,213 | 3,213 | 0 | 0 | 0 |
| 2020 | 3,211 | 3,211 | 3,211 | 0 | 0 | 0 |
| 2021 | 3,210 | 3,210 | 3,210 | 0 | 0 | 0 |
| 2022 | 3,210 | 3,210 | 3,210 | 0 | 0 | 0 |
| 2023 | 3,211 | 3,211 | 3,211 | 0 | 0 | 0 |
| 2024 | 3,213 | 3,213 | 3,213 | 0 | 0 | 0 |

Reading: identical from 2013 on; ≤ 30 code differences a year 1995–2012; larger before 1995 because areacodes re-codes county-level cities for 1990–1994 (xx90xx → xx81–xx99, per the 1991 and 1995 editions of GB/T 2260) and applies the corrections listed in ref/sources/areacodes/errata.md. Both forms are kept reachable for ID decoding because both appear on real ID cards.

## 2. Anchor: GB/T 2260-2007 (codes as of 2007-10-31)

- codes in both: **3,224**
- in the standard but not in areacodes 2007 (excluding aggregate xx00 / 市辖区 codes): none
- in areacodes 2007 but not parsed from the standard: [('530925', '双江拉祜族佤族布朗族傣族自治县'), ('622927', '积石山保安族东乡族撒拉族自治县'), ('650100', '乌鲁木齐市'), ('650121', '乌鲁木齐县'), ('652927', '乌什县')] (these are PDF line-wrap artefacts in the parse, verified by hand)
- same code, different name: [('451402', '江洲区', '江州区'), ('510802', '市中区', '利州区')]

## 3. Anchor: GB/T 2260-2007 第1号修改单 (2016)

- 撤销 250 codes: 249 present in areacodes; missing [('市辖区', '341401')]
- 设立 240 codes: 240 present in areacodes; missing none
- 更名 9 codes: all present

## 4. Table composition

| source | rows |
|---|---|
| areacodes | 6,824 |
| workbook_gb | 378 |
| workbook_unofficial | 100 |

## 5. Coverage of ID-number prefixes in the corpus

Every 6-digit prefix of an ID number in the 2.4M documents, matched against the table (any validity window).

| layer | distinct prefixes matched | share of ID occurrences |
|---|---|---|
| areacodes only | 4,891 | 99.13% |
| + workbook_gb | 5,036 | 99.46% |
| + workbook_unofficial (full table) | 5,069 | 99.83% |
