#!/usr/bin/env python3
"""
P1 — build the `positions` and `network_edges` (L1) tables for the duty-crime subset
(条块共谋网络方案 v1 §6 Tier 0, §9 P1, §11).

Input : ~/indictment_work/docs.parquet/{documents,defendants,charges}      (M1 output)
        ref/charges_std.csv (is_duty_crime), ref/region_codes.csv, ref/position_lexicon.csv, ref/position_titles.csv
Output: ~/indictment_work/docs.parquet/positions/pub_year=YYYY/part-0.parquet
        ~/indictment_work/docs.parquet/network_edges/pub_year=YYYY/part-0.parquet
        ~/indictment_work/logs/p1_summary.json, p1_run.log
        ~/indictment_work/gold/p1_positions_check200.csv   — 200 random defendants for manual verification

Subset: documents in which at least one charge row (either stage) resolves to a chapter 8 or 9 charge;
person defendants only (units are kept out of the network, plan §10.3).

Firm ownership fallback (plan §5 国企 vs 民营企业): an organisation that is a firm with no ownership
cue in its name is assigned by the defendant's own charges — a charge that requires 国家工作人员
status (贪污, 受贿, 挪用公款, 滥用职权 …) → 国企; a briber-side or non-state-employee charge
(行贿, 单位行贿, 对单位行贿, 非国家工作人员受贿, 职务侵占 …) → 民营企业; neither → NULL with
position_sys_rule = 'company_unknown'.

Run:  python3 scripts/p1_build.py            # full subset
      python3 scripts/p1_build.py --limit 5000
"""
import argparse
import csv
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1_positions as P  # noqa: E402
from m1_regions import RegionIndex  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = Path.home() / "indictment_work"
DOCS = WORK / "docs.parquet"
LOG = WORK / "logs"
GOLD = WORK / "gold"

S = pa
SCHEMAS = {
    "positions": pa.schema([
        ("def_id", S.string()), ("doc_id", S.string()), ("pub_year", S.int16()), ("seq_in_doc", S.int16()),
        ("position_seq", S.int16()), ("n_positions", S.int16()), ("position_raw", S.string()),
        ("position_time", S.string()), ("is_offense_time", S.bool_()), ("time_ambiguous", S.bool_()),
        ("position_sys", S.string()), ("position_sys_rule", S.string()),
        ("position_level", S.string()), ("position_level_source", S.string()),
        ("position_rank", S.string()), ("position_rank_rule", S.string()),
        ("position_title", S.string()), ("position_title_masked", S.bool_()), ("position_org", S.string()),
        ("is_status", S.bool_()),
        ("position_prov", S.string()), ("position_city", S.string()), ("position_county", S.string()),
        ("position_region_code", S.string()), ("position_region_source", S.string()),
        ("position_method", S.string()), ("raw_start", S.int16()), ("raw_end", S.int16()),
    ]),
    "network_edges": pa.schema([
        ("edge_id", S.string()), ("layer", S.string()), ("doc_id", S.string()), ("pub_year", S.int16()),
        ("def_id_a", S.string()), ("def_id_b", S.string()), ("node_a", S.string()), ("node_b", S.string()),
        ("sys_a", S.string()), ("level_a", S.string()), ("rank_a", S.string()),
        ("sys_b", S.string()), ("level_b", S.string()), ("rank_b", S.string()),
        ("directed", S.bool_()), ("charge_group", S.string()), ("charge_group_a", S.string()), ("charge_group_b", S.string()),
        ("charge_a", S.string()), ("charge_b", S.string()),
        ("tiao_kuai", S.string()), ("relation_type", S.string()), ("sign_year", S.int16()), ("prov_code", S.string()),
        ("proc_level", S.string()), ("n_defendants", S.int16()), ("weight", S.float64()),
    ]),
}

BRIBE_GIVE = {"行贿罪", "单位行贿罪", "对单位行贿罪", "对有影响力的人行贿罪"}
BRIBE_TAKE = {"受贿罪", "单位受贿罪", "利用影响力受贿罪"}
BRIBE_MID = {"介绍贿赂罪"}
EMBEZZLE = {"贪污罪", "挪用公款罪", "私分国有资产罪", "私分罚没财物罪", "巨额财产来源不明罪", "隐瞒境外存款罪"}
STATE_ROLE = EMBEZZLE | BRIBE_TAKE | {"滥用职权罪", "玩忽职守罪", "国有公司、企业、事业单位人员失职罪", "国有公司、企业、事业单位人员滥用职权罪",
                                      "徇私舞弊低价折股、出售国有资产罪", "徇私枉法罪", "徇私舞弊不移交刑事案件罪"}
PRIVATE_ROLE = BRIBE_GIVE | BRIBE_MID | {"对非国家工作人员行贿罪", "非国家工作人员受贿罪", "职务侵占罪", "挪用资金罪", "串通投标罪"}


def charge_group(charge, chapter):
    if charge in BRIBE_GIVE or charge in BRIBE_TAKE or charge in BRIBE_MID:
        return "贿赂"
    if charge in EMBEZZLE:
        return "贪污挪用"
    if chapter == "渎职罪":
        return "渎职"
    return "其他" if charge else None


def tiao_kuai(sa, la, sb, lb):
    if not sa or not sb or not la or not lb:
        return None
    if sa == sb:
        return "同条同块" if la == lb else "同条"
    return "同块" if la == lb else "跨"


def node(sys, level, rank):
    if not sys:
        return None
    return f"{sys}|{level or '-'}|{rank or '-'}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="first N defendants only (dev)")
    ap.add_argument("--out", default=str(DOCS))
    args = ap.parse_args()
    out_dir = Path(args.out)
    LOG.mkdir(parents=True, exist_ok=True); GOLD.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log = open(LOG / "p1_run.log", "w", encoding="utf-8")

    def say(*a):
        msg = " ".join(str(x) for x in a)
        print(msg); log.write(msg + "\n"); log.flush()

    con = duckdb.connect()
    for t in ("documents", "defendants", "charges"):
        con.sql(f"create view {t} as select * from read_parquet('{DOCS}/{t}/pub_year=*/*.parquet', hive_partitioning=1)")
    con.sql(f"create table cs as select charge_std, chapter, is_duty_crime from read_csv('{ROOT / 'ref' / 'charges_std.csv'}', header=true)")
    con.sql("""create table duty_docs as select distinct c.doc_id from charges c join cs using(charge_std) where cs.is_duty_crime = 1""")
    con.sql("""create table defs as
        select d.def_id, d.doc_id, d.pub_year, d.seq_in_doc, d.occupation_raw
        from defendants d join duty_docs using(doc_id) where d.def_type = 'person'""")
    con.sql("""create table docs as
        select m.doc_id, m.sign_year, m.prov_code, m.proc_level,
               (select count(*) from defs f where f.doc_id = m.doc_id) as n_person
        from documents m join duty_docs using(doc_id)""")
    # one primary charge per defendant: 起诉 stage first (explicit/sole), else 涉嫌; plus the set of all charges
    con.sql("""create table prim as
        select def_id, charge_std, chapter from (
          select c.def_id, c.charge_std, cs.chapter,
                 row_number() over (partition by c.def_id order by
                    case when c.stage = '起诉' then 0 else 1 end,
                    case when c.attribution in ('explicit','sole') then 0 else 1 end,
                    case when c.is_primary then 0 else 1 end, c.pos) rn
          from charges c join duty_docs using(doc_id) left join cs using(charge_std)
          where c.charge_std is not null and c.attribution in ('explicit','sole')) where rn = 1""")
    con.sql("""create table allch as select def_id, list(distinct charge_std) charges from charges
               where doc_id in (select doc_id from duty_docs) and charge_std is not null and attribution in ('explicit','sole') group by 1""")
    n_docs = con.sql("select count(*) from duty_docs").fetchone()[0]
    n_defs = con.sql("select count(*) from defs").fetchone()[0]
    say(f"duty-crime documents {n_docs:,}; person defendants {n_defs:,}")

    lim = f" limit {args.limit}" if args.limit else ""
    defs = con.sql(f"""select f.def_id, f.doc_id, f.pub_year, f.seq_in_doc, f.occupation_raw, m.prov_code,
                              p.charge_std, p.chapter, a.charges
                       from defs f join docs m using(doc_id) left join prim p using(def_id) left join allch a using(def_id)
                       order by f.pub_year, f.doc_id, f.seq_in_doc{lim}""").fetchall()
    ix = RegionIndex()
    lp = P.LevelParser(ix)
    rows_by_year = {}
    node_of = {}         # def_id -> (sys, level, rank)
    cg_of = {}           # def_id -> (charge_group, charge_std)
    stats = Counter()
    rule_counts = Counter()
    region_cache = {}
    for def_id, doc_id, pub_year, seq, occ, prov_hint, charge, chapter, charges in defs:
        cg_of[def_id] = (charge_group(charge, chapter), charge)
        chset = set(charges or [])
        if not occ:
            stats["def_no_occupation"] += 1
            node_of[def_id] = (None, None, None)
            continue
        prow = P.parse_occupation(occ, lp)
        P.choose_offense_time(prow)
        stats["def_with_occupation"] += 1
        if not prow:
            stats["def_zero_positions"] += 1
        for d in prow:
            sys_ = d["position_sys"]
            rule = d["position_sys_rule"]
            if sys_ == "企业":
                if chset & STATE_ROLE:
                    sys_, rule = "国企", (rule or "") + ";charge:公职"
                elif chset & PRIVATE_ROLE:
                    sys_, rule = "民营企业", (rule or "") + ";charge:行贿"
                else:
                    sys_, rule = None, (rule or "") + ";company_unknown"
            # region of the organisation (plan §4): only what is written in the name, hint = prosecuting province
            prov = city = county = code = None
            src = "none"
            if d["place_str"]:
                key = (d["place_str"], prov_hint)
                if key not in region_cache:
                    pc, cc, kc, _amb = ix.resolve_location(d["place_str"], prov_hint)
                    # the hint may only disambiguate; a province that comes solely from the hint is not a finding
                    if pc and not cc and not kc and not any(d["place_str"].startswith(k) for k in ix.prov):
                        pc = None
                    region_cache[key] = (pc, cc, kc)
                pc, cc, kc = region_cache[key]
                if pc:
                    prov = ix.rows[pc]["name"]; code = pc; src = "org_text"
                if cc and cc in ix.rows:
                    city = ix.rows[cc]["name"]; code = cc
                if kc and kc in ix.rows:
                    county = ix.rows[kc]["name"]; code = kc
                    if city is None and ix.rows[kc]["city_name"]:
                        city = ix.rows[kc]["city_name"]
            rec = dict(def_id=def_id, doc_id=doc_id, pub_year=pub_year, seq_in_doc=seq, position_seq=d["position_seq"],
                       n_positions=len(prow), position_raw=d["position_raw"], position_time=d["position_time"],
                       is_offense_time=d["is_offense_time"], time_ambiguous=d["time_ambiguous"],
                       position_sys=sys_, position_sys_rule=rule, position_level=d["position_level"],
                       position_level_source=d["position_level_source"], position_rank=d["position_rank"],
                       position_rank_rule=d["position_rank_rule"], position_title=d["position_title"],
                       position_title_masked=d["position_title_masked"], position_org=d["position_org"], is_status=d["is_status"],
                       position_prov=prov, position_city=city, position_county=county, position_region_code=code,
                       position_region_source=src, position_method="rule", raw_start=d["raw_start"], raw_end=d["raw_end"])
            rows_by_year.setdefault(pub_year, []).append(rec)
            if d["is_offense_time"]:
                node_of[def_id] = (sys_, d["position_level"], d["position_rank"])
            rule_counts[(rule or "").split(";")[0].split(":")[0] or "none"] += 1
            stats["positions"] += 1
            stats["pos_sys_known"] += sys_ is not None
            stats["pos_level_known"] += d["position_level"] is not None
            stats["pos_rank_known"] += d["position_rank"] is not None
            stats["pos_title_known"] += d["position_title"] is not None
            stats["pos_title_masked"] += d["position_title_masked"]
            stats["pos_status"] += d["is_status"]
            stats["pos_region_resolved"] += src == "org_text"
            stats["pos_county_resolved"] += county is not None
        node_of.setdefault(def_id, (None, None, None))
    say(f"parsed {stats['positions']:,} positions for {stats['def_with_occupation']:,} defendants in {time.time() - t0:.0f}s")

    # ---- write positions
    pos_dir = out_dir / "positions"
    if pos_dir.exists():
        import shutil; shutil.rmtree(pos_dir)
    for y, recs in sorted(rows_by_year.items()):
        d = pos_dir / f"pub_year={y}"; d.mkdir(parents=True, exist_ok=True)
        tbl = pa.Table.from_pylist(recs, schema=SCHEMAS["positions"])
        pq.write_table(tbl, d / "part-0.parquet", compression="zstd")

    # ---- L1 edges: all pairs of person defendants in the same document
    doc_meta = {r[0]: r[1:] for r in con.sql("select doc_id, sign_year, prov_code, proc_level, n_person from docs").fetchall()}
    by_doc = {}
    for def_id, doc_id, pub_year, seq, *_ in defs:
        by_doc.setdefault((doc_id, pub_year), []).append((seq, def_id))
    edges_by_year = {}
    estats = Counter()
    for (doc_id, pub_year), members in by_doc.items():
        if len(members) < 2:
            continue
        sign_year, prov_code, proc_level, n_person = doc_meta[doc_id]
        members.sort()
        w = 1.0 / (len(members) - 1)
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (sa_seq, a), (sb_seq, b) = members[i], members[j]
                ga, ca = cg_of.get(a, (None, None)); gb, cb = cg_of.get(b, (None, None))
                directed = False
                if ca in BRIBE_GIVE and cb in BRIBE_TAKE:
                    directed = True
                elif cb in BRIBE_GIVE and ca in BRIBE_TAKE:
                    directed = True; a, b, ga, gb, ca, cb, sa_seq, sb_seq = b, a, gb, ga, cb, ca, sb_seq, sa_seq
                na, nb = node_of.get(a, (None,) * 3), node_of.get(b, (None,) * 3)
                cg = ga if ga == gb else "|".join(sorted(x for x in (ga, gb) if x)) or None
                rec = dict(edge_id=f"{doc_id}:{sa_seq}-{sb_seq}", layer="L1", doc_id=doc_id, pub_year=pub_year,
                           def_id_a=a, def_id_b=b, node_a=node(*na), node_b=node(*nb),
                           sys_a=na[0], level_a=na[1], rank_a=na[2], sys_b=nb[0], level_b=nb[1], rank_b=nb[2],
                           directed=directed, charge_group=cg, charge_group_a=ga, charge_group_b=gb, charge_a=ca, charge_b=cb,
                           tiao_kuai=tiao_kuai(na[0], na[1], nb[0], nb[1]), relation_type=None, sign_year=sign_year,
                           prov_code=prov_code, proc_level=proc_level, n_defendants=len(members), weight=w)
                edges_by_year.setdefault(pub_year, []).append(rec)
                estats["edges"] += 1
                estats["edges_both_nodes"] += (na[0] is not None and nb[0] is not None)
                estats["edges_directed"] += directed
                estats[f"tk:{rec['tiao_kuai']}"] += 1
        estats["docs_multi"] += 1
    edge_dir = out_dir / "network_edges"
    if edge_dir.exists():
        import shutil; shutil.rmtree(edge_dir)
    for y, recs in sorted(edges_by_year.items()):
        d = edge_dir / f"pub_year={y}"; d.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(recs, schema=SCHEMAS["network_edges"]), d / "part-0.parquet", compression="zstd")
    say(f"L1 edges {estats['edges']:,} in {estats['docs_multi']:,} multi-defendant documents; "
        f"both nodes known {estats['edges_both_nodes']:,}; directed (行贿→受贿) {estats['edges_directed']:,}")

    # ---- manual-check sample: 200 defendants with an occupation string (fixed seed)
    rnd = random.Random(20261009)
    with_occ = [r for r in defs if r[4]]
    sample = rnd.sample(with_occ, min(200, len(with_occ)))
    sample_ids = {r[0] for r in sample}
    srows = [r for recs in rows_by_year.values() for r in recs if r["def_id"] in sample_ids]
    srows.sort(key=lambda r: (r["def_id"], r["position_seq"]))
    cols = ["def_id", "position_seq", "n_positions", "occupation_raw", "position_raw", "position_time", "is_offense_time", "position_sys",
            "position_level", "position_rank", "position_title", "position_org", "position_prov", "position_city", "position_county",
            "position_sys_rule", "ok_sys", "ok_level", "ok_rank", "ok_title", "ok_org", "ok_split", "note"]
    occ_of = {r[0]: r[4] for r in sample}
    with open(GOLD / "p1_positions_check200.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in srows:
            w.writerow({**{c: r.get(c, "") for c in cols}, "occupation_raw": occ_of[r["def_id"]]})
    # defendants in the sample with zero rows (string entirely non-position) are listed too
    with open(GOLD / "p1_positions_check200.csv", "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        for r in sample:
            if not any(s["def_id"] == r[0] for s in srows):
                w.writerow(dict(def_id=r[0], position_seq="", n_positions=0, occupation_raw=r[4]))

    # ---- summary
    pos_view = f"read_parquet('{pos_dir}/pub_year=*/*.parquet', hive_partitioning=1)"
    dist = {}
    for col in ("position_sys", "position_level", "position_rank", "position_time", "position_level_source", "position_region_source"):
        dist[col] = {str(k): v for k, v in con.sql(f"select {col}, count(*) from {pos_view} group by 1 order by 2 desc").fetchall()}
    dist["offense_time_nodes"] = {str(k): v for k, v in con.sql(
        f"select position_sys || '|' || coalesce(position_level,'-') || '|' || coalesce(position_rank,'-'), count(*) from {pos_view} "
        f"where is_offense_time and position_sys is not null group by 1 order by 2 desc limit 60").fetchall()}
    dist["top_titles"] = {str(k): v for k, v in con.sql(f"select position_title, count(*) from {pos_view} where position_title is not null group by 1 order by 2 desc limit 60").fetchall()}
    dist["n_positions_per_def"] = {str(k): v for k, v in con.sql(f"select n_positions, count(distinct def_id) from {pos_view} group by 1 order by 1").fetchall()}
    summary = dict(run_at=time.strftime("%Y-%m-%d %H:%M:%S"), duty_docs=n_docs, person_defendants=n_defs, stats=dict(stats),
                   sys_rule_counts=dict(rule_counts), edges=dict(estats), distributions=dist, seconds=round(time.time() - t0, 1))
    json.dump(summary, open(LOG / "p1_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(json.dumps({k: v for k, v in summary.items() if k != "distributions"}, ensure_ascii=False, indent=1))
    for k, v in dist.items():
        say(k, json.dumps(v, ensure_ascii=False))
    say(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
