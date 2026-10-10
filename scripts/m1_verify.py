#!/usr/bin/env python3
"""
M1 verification — runs the checks listed in 方案 v2 §验证方式 against the extracted tables.

  1. row conservation: documents == raw rows, doc_id unique, def_id unique, FK integrity
  2. field hit rates vs the diagnosis baselines (regression test; |delta| > 0.5 pp is reported)
  3. province × year dashboard: cells more than 20 pp below the corpus mean (local template variants)
  4. end-to-end smoke test: 2016 广东省 危险驾驶罪 起诉书 count, tables vs raw grep
  5. statute sanity: 刑法 133条之一 must be a top-degree node
  6. internal consistency: date orderings, stage-change rate, masking rates, non-prosecution reasons

Writes ~/indictment_work/logs/m1_verify.log and m1_dashboard.csv.
Run: python3 scripts/m1_verify.py [--root DIR]
"""
import argparse
import sys
from pathlib import Path

import duckdb

WORK = Path.home() / "indictment_work"
RAW = WORK / "raw.parquet"
LOG = WORK / "logs"
EXPECTED_ROWS = 2_399_736

# document-level hit rates measured on the full file during the diagnosis (方案 v2 Tier 0 table)
BASELINES = {
    "case_no": ("文号", 97.4), "proc_name": ("检察院名称", 98.7), "measure_any": ("强制措施(任一被告)", 98.45),
    "charge_any": ("罪名并集", 99.53), "id_prefix_any": ("身份证前6位(任一被告)", 96.3), "prosecutor": ("检察官署名", 74.0),
    "statute_xf": ("刑法条文", 99.7), "court": ("法院名称", 81.6), "doc_type": ("doc_type", 98.2),
    "birth_any": ("出生年份(任一被告)", 99.7), "defendant": ("被告人分块", 87.9),
}

out_lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    out_lines.append(s)


def table(con, rows, hdr):
    say("| " + " | ".join(hdr) + " |")
    say("|" + "---|" * len(hdr))
    for r in rows:
        say("| " + " | ".join("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v)) for v in r) + " |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=WORK / "docs.parquet")
    ap.add_argument("--raw", type=Path, default=RAW)
    args = ap.parse_args()
    root = args.root
    con = duckdb.connect()
    con.execute("SET threads=10")
    for t in ("documents", "defendants", "charges", "statutes", "prosecutors", "sections"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{root}/{t}/pub_year=*/*.parquet', hive_partitioning=false)")
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{args.raw}/pub_year=*/*.parquet', hive_partitioning=true)")
    ok = True

    say("# M1 verification\n")
    # ---- 1. conservation
    n_docs = con.execute("SELECT count(*) FROM documents").fetchone()[0]
    n_raw = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    n_uniq = con.execute("SELECT count(DISTINCT doc_id) FROM documents").fetchone()[0]
    say("## 1. Row conservation")
    say(f"documents rows {n_docs:,} / raw rows {n_raw:,} / distinct doc_id {n_uniq:,}")
    full = n_raw == EXPECTED_ROWS
    if n_docs != n_raw or n_uniq != n_docs:
        say("FAIL: row count or doc_id uniqueness"); ok = False
    n_def, n_def_u = con.execute("SELECT count(*), count(DISTINCT def_id) FROM defendants").fetchone()
    fk = con.execute("SELECT count(*) FROM charges c WHERE c.def_id IS NOT NULL AND c.def_id NOT IN (SELECT def_id FROM defendants)").fetchone()[0]
    errs = con.execute("SELECT count(*) FROM documents WHERE extract_flags = -1").fetchone()[0]
    say(f"defendants {n_def:,} (distinct def_id {n_def_u:,}); charges with dangling def_id {fk:,}; documents with extraction errors {errs:,}")
    if n_def != n_def_u or fk or errs:
        say("FAIL: integrity"); ok = False

    # ---- 2. hit rates vs baselines
    say("\n## 2. Field hit rates vs diagnosis baselines (document level, all document types)")
    r = con.execute("""
      WITH d AS (
        SELECT doc_id, case_no_full IS NOT NULL AS case_no, proc_name IS NOT NULL AS proc_name, court_name IS NOT NULL AS court,
               n_prosecutors > 0 AS prosecutor, doc_type IS NOT NULL AS doc_type, n_defendants > 0 AS defendant,
               n_charges_suspect + n_charges_pros > 0 AS charge_any
        FROM documents),
      x AS (
        SELECT doc_id, bool_or(detained OR arrested OR bailed OR surveilled) AS measure_any, bool_or(hukou_code IS NOT NULL) AS id_prefix_any,
               bool_or(birth_year IS NOT NULL) AS birth_any FROM defendants WHERE def_type='person' GROUP BY 1),
      s AS (SELECT doc_id, bool_or(law='刑法') AS statute_xf FROM statutes GROUP BY 1)
      SELECT 100*avg(case_no::int), 100*avg(proc_name::int), 100*avg(coalesce(measure_any,false)::int), 100*avg(charge_any::int),
             100*avg(coalesce(id_prefix_any,false)::int), 100*avg(prosecutor::int), 100*avg(coalesce(statute_xf,false)::int),
             100*avg(court::int), 100*avg(doc_type::int), 100*avg(coalesce(birth_any,false)::int), 100*avg(defendant::int)
      FROM d LEFT JOIN x USING(doc_id) LEFT JOIN s USING(doc_id)""").fetchone()
    keys = ["case_no", "proc_name", "measure_any", "charge_any", "id_prefix_any", "prosecutor", "statute_xf", "court", "doc_type", "birth_any", "defendant"]
    rows = []
    for k, v in zip(keys, r):
        label, base = BASELINES[k]
        rows.append((label, round(v, 2), base, round(v - base, 2), "ok" if v >= base - 0.5 else "BELOW"))
    table(con, rows, ["field", "M1 %", "baseline %", "delta pp", ""])
    say("A value above the baseline is expected where M1 handles more forms than the diagnosis regex did "
        "(被不起诉人 blocks, every bracket style of 文号, spaced signature labels).")

    say("\n### by document type")
    rows = con.execute("""
      SELECT doc_type, count(*) n, round(100*avg((case_no_full IS NOT NULL)::int),1), round(100*avg((proc_name IS NOT NULL)::int),1),
             round(100*avg((n_defendants>0)::int),1), round(100*avg((n_charges_suspect>0)::int),1), round(100*avg((n_charges_pros>0)::int),1),
             round(100*avg((n_prosecutors>0)::int),1), round(100*avg((court_name IS NOT NULL)::int),1), round(100*avg((region_level='county')::int),1)
      FROM documents GROUP BY 1 ORDER BY n DESC""").fetchall()
    table(con, rows, ["doc_type", "n", "文号", "检察院", "被告块", "涉嫌罪名", "起诉罪名", "署名", "法院", "县级区划"])

    # ---- 3. province × year dashboard
    say("\n## 3. Province × year dashboard (起诉书 only; cells ≥ 20 pp below the corpus mean)")
    fields = {"case_no": "case_no_full IS NOT NULL", "proc_name": "proc_name IS NOT NULL", "court": "court_name IS NOT NULL",
              "prosecutor": "n_prosecutors > 0", "defendant": "n_defendants > 0", "charge_pros": "n_charges_pros > 0",
              "charge_suspect": "n_charges_suspect > 0", "statute": "n_statutes > 0", "county": "region_level = 'county'",
              "offense_date": "offense_date_first IS NOT NULL"}
    sel = ", ".join(f"round(100*avg(({e})::int),1) AS {k}" for k, e in fields.items())
    con.execute(f"""CREATE TABLE dash AS
      SELECT coalesce(prov_std, '(unresolved)') AS prov, pub_year, count(*) AS n, {sel}
      FROM documents WHERE doc_type = '起诉书' GROUP BY 1, 2""")
    con.execute(f"COPY (SELECT * FROM dash ORDER BY prov, pub_year) TO '{LOG / 'm1_dashboard.csv'}' (HEADER, DELIMITER ',')")
    means = con.execute(f"SELECT {', '.join(f'round(100*avg(({e})::int),1)' for e in fields.values())} FROM documents WHERE doc_type='起诉书'").fetchone()
    flagged = []
    for k, m in zip(fields, means):
        rows = con.execute(f"SELECT prov, pub_year, n, {k} FROM dash WHERE n >= 200 AND {k} < {m} - 20 ORDER BY {k}").fetchall()
        for p, y, n, v in rows:
            flagged.append((k, p, y, n, v, m))
    if flagged:
        table(con, flagged, ["field", "province", "year", "docs", "cell %", "corpus %"])
    else:
        say("no cell with ≥ 200 documents is more than 20 pp below the corpus mean")
    say(f"full dashboard: {LOG / 'm1_dashboard.csv'}")

    # ---- 4. end-to-end smoke test
    say("\n## 4. End-to-end smoke test: 2016 (publication year) 广东省 危险驾驶罪 起诉书")
    a = con.execute("""SELECT count(DISTINCT d.doc_id) FROM documents d JOIN charges c USING(doc_id)
                       WHERE d.pub_year = 2016 AND d.prov_std = '广东省' AND d.doc_type = '起诉书' AND c.stage = '起诉' AND c.charge_std = '危险驾驶罪'""").fetchone()[0]
    b = con.execute("""SELECT count(*) FROM raw WHERE pub_year = 2016 AND prov LIKE '广东%' AND title LIKE '%起诉书%'
                       AND regexp_matches(content, '应当?以危险驾驶罪追究')""").fetchone()[0]
    say(f"tables: {a:,}   raw grep (title LIKE 起诉书, content ~ 应以危险驾驶罪追究): {b:,}   ratio {a/b if b else 0:.3f}")
    say("(the raw count misses untitled documents and documents whose 本院认为 uses 构成…罪; the two numbers should be within a few percent)")

    # ---- 5. statute sanity
    say("\n## 5. Statute sanity: top 刑法 articles by documents (本院认为 section)")
    rows = con.execute("""SELECT article, sub_article, count(DISTINCT doc_id) n FROM statutes WHERE law='刑法' AND section='opinion'
                          GROUP BY 1,2 ORDER BY n DESC LIMIT 8""").fetchall()
    table(con, rows, ["article", "之", "docs"])
    top = [(r[0], r[1]) for r in rows[:5]]
    if (133, 1) not in top:
        say("FAIL: 133条之一 (危险驾驶) is not among the top-5 articles — statute parsing is suspect"); ok = False
    else:
        say("133条之一 is in the top 5: ok")
    rows = con.execute("SELECT law, count(*) FROM statutes GROUP BY 1 ORDER BY 2 DESC").fetchall()
    table(con, rows, ["law", "references"])

    # ---- 6. consistency
    say("\n## 6. Internal consistency")
    r = con.execute("""SELECT count(*) FILTER (WHERE days_to_arrest < 0), count(*) FILTER (WHERE days_to_arrest > 365), count(*) FILTER (WHERE days_to_arrest IS NOT NULL),
                              quantile_cont(days_to_arrest, 0.5) FROM defendants""").fetchone()
    say(f"days_to_arrest: negative {r[0]:,} / > 365 {r[1]:,} / non-null {r[2]:,} / median {r[3]}")
    r = con.execute("""SELECT count(*) FILTER (WHERE x.date_detain > d.sign_date), count(*) FILTER (WHERE x.date_detain IS NOT NULL AND d.sign_date IS NOT NULL)
                       FROM defendants x JOIN documents d USING(doc_id)""").fetchone()
    say(f"date_detain after sign_date (impossible): {r[0]:,} of {r[1]:,} ({100*r[0]/max(r[1],1):.2f} %)")
    r = con.execute("""SELECT count(*) FILTER (WHERE age_at_offense < 14), count(*) FILTER (WHERE age_at_offense > 90), count(*) FILTER (WHERE age_at_offense IS NOT NULL) FROM defendants""").fetchone()
    say(f"age_at_offense < 14: {r[0]:,}; > 90: {r[1]:,}; non-null {r[2]:,}")
    rows = con.execute("SELECT minor_status, count(*) FROM defendants WHERE def_type='person' GROUP BY 1 ORDER BY 2 DESC").fetchall()
    table(con, rows, ["minor_status", "defendants"])
    r = con.execute("""
      WITH a AS (SELECT def_id, charge_std FROM charges WHERE stage='涉嫌' AND is_primary AND charge_std IS NOT NULL),
           b AS (SELECT def_id, charge_std FROM charges WHERE stage='起诉' AND is_primary AND charge_std IS NOT NULL)
      SELECT round(100*avg((a.charge_std<>b.charge_std)::int),2), count(*) FROM a JOIN b USING(def_id)""").fetchone()
    say(f"primary charge changed between 涉嫌 and 起诉 stage: {r[0]} % of {r[1]:,} defendants with both (plan expected 3–6 %)")
    rows = con.execute("""
      WITH a AS (SELECT def_id, charge_std FROM charges WHERE stage='涉嫌' AND is_primary AND charge_std IS NOT NULL),
           b AS (SELECT def_id, charge_std FROM charges WHERE stage='起诉' AND is_primary AND charge_std IS NOT NULL)
      SELECT a.charge_std, b.charge_std, count(*) n FROM a JOIN b USING(def_id) WHERE a.charge_std<>b.charge_std GROUP BY 1,2 ORDER BY n DESC LIMIT 10""").fetchall()
    table(con, rows, ["涉嫌", "起诉", "defendants"])
    rows = con.execute("SELECT stage, attribution, count(*) FROM charges GROUP BY 1,2 ORDER BY 1,3 DESC").fetchall()
    table(con, rows, ["stage", "attribution", "rows"])
    r = con.execute("SELECT count(*) FILTER (WHERE charge_std IS NULL), count(*) FROM charges").fetchone()
    say(f"charge rows not mapped to the standard table: {r[0]:,} of {r[1]:,} ({100*r[0]/max(r[1],1):.2f} %)")
    rows = con.execute("SELECT charge_raw, count(*) n FROM charges WHERE charge_std IS NULL GROUP BY 1 ORDER BY n DESC LIMIT 12").fetchall()
    table(con, rows, ["unmapped raw string", "rows"])
    rows = con.execute("SELECT pub_year, round(100*avg(is_name_masked::int),1), count(*) FROM defendants WHERE def_type='person' GROUP BY 1 ORDER BY 1").fetchall()
    table(con, rows, ["pub_year", "defendant names masked %", "defendants"])
    r = con.execute("SELECT round(100*avg(is_masked::int),2), count(*), count(DISTINCT name) FILTER (WHERE NOT is_masked) FROM prosecutors").fetchone()
    say(f"prosecutor signatures: {r[1]:,} rows, {r[0]} % masked, {r[2]:,} distinct unmasked names")
    rows = con.execute("SELECT nonpros_reason, nonpros_source, count(*) FROM documents WHERE doc_type IN ('不起诉决定书','附条件不起诉决定书') GROUP BY 1,2 ORDER BY 3 DESC").fetchall()
    table(con, rows, ["nonpros_reason", "source", "docs"])
    rows = con.execute("SELECT proc_level, count(*) FROM documents GROUP BY 1 ORDER BY 2 DESC").fetchall()
    table(con, rows, ["proc_level", "docs"])
    say("\n### extract_flags")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import m1_rules as R
    rows = []
    for bit, name in sorted(R.FLAG_NAMES.items()):
        n = con.execute(f"SELECT count(*) FROM documents WHERE extract_flags & {bit} > 0").fetchone()[0]
        rows.append((name, n, round(100 * n / max(n_docs, 1), 2)))
    table(con, rows, ["flag", "docs", "%"])

    say("\nRESULT:", "PASS" if ok else "FAIL", "(full corpus)" if full else "(partial run)")
    (LOG / "m1_verify.log").write_text("\n".join(out_lines), encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
