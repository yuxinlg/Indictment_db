#!/usr/bin/env python3
"""
M1 — Tier 0 rule extraction over the full corpus.

Input : ~/indictment_work/raw.parquet/pub_year=YYYY/part-0.parquet            (M0 output)
        ref/charges_std.csv, ref/charge_aliases.csv, ref/region_codes.csv       (R output)
Output: ~/indictment_work/docs.parquet/<table>/pub_year=YYYY/part-<rg>.parquet
            tables: documents · defendants · charges · statutes · prosecutors · sections
        ~/indictment_work/sensitive.parquet/defendants_sensitive/pub_year=YYYY/part-<rg>.parquet
            names and raw ID strings only — never leaves the laptop, never joined by default
        ~/indictment_work/logs/m1_summary.json, m1_run.log

One task = one Parquet row group of the raw store (124 tasks, 6k–20k documents each); workers
write their own part files, so nothing large crosses process boundaries. Re-running is cheap
(minutes), which is the whole point of Tier 0: iterate on rules, not on compute.

Run:
  python3 scripts/m1_extract.py                 # full corpus, 10 workers
  python3 scripts/m1_extract.py --smoke         # one row group per year, to a scratch dir
  python3 scripts/m1_extract.py --out DIR       # write elsewhere (tests)
"""
import argparse
import csv
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import date
from multiprocessing import Pool, get_context
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_rules as R  # noqa: E402
from m1_regions import RegionIndex  # noqa: E402
from ref_charges import build_dictionary, make_segmenter, resolve_charge_string  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = Path.home() / "indictment_work"
RAW = WORK / "raw.parquet"
OUT = WORK / "docs.parquet"
OUT_SENS = WORK / "sensitive.parquet"
LOG = WORK / "logs"
RAW_COLS = ["doc_id", "title", "content", "prov", "muni", "county", "url", "pub_date", "sign_date", "sign_year",
            "sign_month", "sign_date_precision", "date_source", "text_len", "quality_flags"]

# --------------------------------------------------------------------------- schemas
S = pa
SCHEMAS = {
    "documents": pa.schema([
        ("doc_id", S.string()), ("pub_year", S.int16()), ("doc_type", S.string()), ("doc_type_source", S.string()),
        ("doc_heading_raw", S.string()), ("title", S.string()), ("url", S.string()),
        ("pub_date", S.date32()), ("sign_date", S.date32()), ("sign_year", S.int16()), ("sign_month", S.int8()),
        ("sign_date_precision", S.string()), ("date_source", S.string()), ("pub_lag_days", S.int32()),
        ("proc_name", S.string()), ("proc_level", S.string()), ("proc_special", S.string()),
        ("region_code", S.string()), ("region_level", S.string()), ("prov_code", S.string()), ("prov_std", S.string()),
        ("city_code", S.string()), ("muni_std", S.string()), ("county_code", S.string()), ("county_std", S.string()),
        ("region_source", S.string()), ("region_ambiguous", S.bool_()), ("prov_code_col", S.string()),
        ("court_name", S.string()),
        ("case_no_full", S.string()), ("case_no_proc", S.string()), ("case_no_series", S.string()),
        ("case_no_year", S.int16()), ("case_no_seq", S.int32()), ("case_no_bracket", S.string()), ("n_case_no", S.int8()),
        ("has_plea_leniency", S.bool_()), ("has_sentencing_rec", S.bool_()),
        ("nonpros_reason", S.string()), ("nonpros_article", S.int16()), ("nonpros_paragraph", S.int8()), ("nonpros_source", S.string()),
        ("n_defendants", S.int16()), ("n_unit_defendants", S.int16()), ("n_charges_suspect", S.int16()), ("n_charges_pros", S.int16()),
        ("n_statutes", S.int16()), ("n_prosecutors", S.int8()), ("n_segments", S.int16()),
        ("offense_date_first", S.date32()), ("facts_date_min", S.date32()),
        ("text_len", S.int32()), ("quality_flags", S.int32()), ("extract_flags", S.int32()),
    ]),
    "defendants": pa.schema([
        ("def_id", S.string()), ("doc_id", S.string()), ("pub_year", S.int16()), ("seq_in_doc", S.int16()),
        ("def_type", S.string()), ("label", S.string()), ("is_name_masked", S.bool_()), ("has_alias", S.bool_()),
        ("gender", S.string()), ("birth_year", S.int16()), ("birth_year_source", S.string()), ("ethnicity", S.string()),
        ("edu_raw", S.string()), ("edu_level", S.string()), ("politics", S.string()), ("occupation_raw", S.string()),
        ("hukou_text", S.string()), ("hukou_code", S.string()), ("id_birth_year", S.int16()), ("id_shape", S.string()),
        ("has_id", S.bool_()), ("is_id_unmasked", S.bool_()),
        ("detained", S.bool_()), ("arrested", S.bool_()), ("bailed", S.bool_()), ("surveilled", S.bool_()), ("detain_kind", S.string()),
        ("date_detain", S.date32()), ("date_arrest_approved", S.date32()), ("date_arrest", S.date32()), ("date_bail", S.date32()),
        ("date_surveil", S.date32()), ("days_to_arrest", S.int32()), ("n_bail", S.int8()),
        ("custody_at_filing", S.string()), ("age_at_offense", S.int16()), ("minor_status", S.string()),
        ("n_suspect_charges", S.int8()), ("block_start", S.int32()), ("block_end", S.int32()),
    ]),
    "charges": pa.schema([
        ("doc_id", S.string()), ("pub_year", S.int16()), ("def_id", S.string()), ("seq_in_doc", S.int16()),
        ("stage", S.string()), ("source", S.string()), ("charge_raw", S.string()), ("charge_std", S.string()),
        ("resolve_method", S.string()), ("residue", S.string()), ("is_multi", S.bool_()), ("attribution", S.string()),
        ("is_primary", S.bool_()), ("pos", S.int32()), ("n_mentions", S.int8()),
    ]),
    "statutes": pa.schema([
        ("doc_id", S.string()), ("pub_year", S.int16()), ("seq", S.int16()), ("law", S.string()), ("law_raw", S.string()),
        ("article", S.int16()), ("sub_article", S.int8()), ("paragraph", S.int8()), ("item", S.int8()),
        ("section", S.string()), ("pos", S.int32()), ("ref_raw", S.string()),
    ]),
    "prosecutors": pa.schema([
        ("doc_id", S.string()), ("pub_year", S.int16()), ("seq", S.int8()), ("role", S.string()), ("name", S.string()),
        ("is_masked", S.bool_()), ("line_raw", S.string()),
    ]),
    "sections": pa.schema([
        ("doc_id", S.string()), ("pub_year", S.int16()), ("sect_type", S.string()), ("start", S.int32()), ("end", S.int32()),
    ]),
    "defendants_sensitive": pa.schema([
        ("def_id", S.string()), ("doc_id", S.string()), ("pub_year", S.int16()), ("name_raw", S.string()), ("name", S.string()),
        ("alias", S.string()), ("id_raw", S.string()),
    ]),
}
SENSITIVE_TABLES = {"defendants_sensitive"}

# --------------------------------------------------------------------------- worker state
_G = {}


def init_worker():
    _G["regions"] = RegionIndex()
    std = {r["charge_std"] for r in csv.DictReader(open(ROOT / "ref" / "charges_std.csv", encoding="utf-8-sig"))}
    D, _ = build_dictionary(std)
    _G["D"] = D
    _G["segment"] = make_segmenter(D)
    alias = {}
    for r in csv.DictReader(open(ROOT / "ref" / "charge_aliases.csv", encoding="utf-8-sig")):
        alias.setdefault(r["raw"], (r["charge_std"].split("|"), r["method"]))
    _G["alias"] = alias
    _G["cache"] = {}


def resolve_charge(raw: str):
    """-> (list of std names, method, residue). Published alias map first, same algorithm otherwise."""
    c = _G["cache"].get(raw)
    if c is not None:
        return c
    hit = _G["alias"].get(raw)
    if hit:
        names, method = hit
        res = (names, method, None) if method != "partial" else (names, "partial", "?")
    else:
        r = resolve_charge_string(raw, _G["D"], _G["segment"])
        if r is None:
            res = ([], "unresolved", raw)
        else:
            found, residue, method = r
            res = (found, "partial" if residue else method, residue)
    if len(_G["cache"]) < 200_000:
        _G["cache"][raw] = res
    return res


# --------------------------------------------------------------------------- one document
def extract_doc(row: dict, year: int, tabs: dict, stats: Counter):
    doc_id = row["doc_id"]
    text = R.normalise(row["content"] or "")
    flags = 0
    segs = R.split_segments(text)
    header = R.parse_header(text, segs, row["title"])
    blocks, flags = R.find_blocks(text, header["header_end"], flags)
    if header["proc_name"] is None:
        flags |= R.FLAG_NO_PROC
    if header["doc_type"] is None:
        flags |= R.FLAG_NO_DOCTYPE
    if header["case_no"] is None:
        flags |= R.FLAG_NO_CASE_NO
    if header["n_case_no"] > 1:
        flags |= R.FLAG_MULTI_CASE_NO

    # defendants
    persons = []
    for i, b in enumerate(blocks):
        b["name"] = b["name_raw"]
        if R._is_person_label(b["label"]):
            p = R.parse_person_block(b)
            p["def_type"] = "person"
            if p["birth_year"] is None and p.get("age_stated") and row["sign_year"] and 10 <= p["age_stated"] <= 100:
                p["birth_year"] = row["sign_year"] - p["age_stated"]   # "46岁": year precision only
                p["birth_year_source"] = "age"
        else:
            p = dict(name=b["name_raw"], alias=None, is_name_masked=bool(R.RE_MASK.search(b["name_raw"])), gender=None,
                     birth_year=None, birth_year_source=None, ethnicity=None, edu_raw=None, edu_level=None, politics=None,
                     hukou_text=None, occupation_raw=None, id_raw=None, id_prefix=None, id_birth_year=None, id_shape=None,
                     is_id_unmasked=False, suspect_charges=[(m.group(1), b["start"] + m.start(1)) for m in R.RE_SUSPECT.finditer(b["text"])],
                     def_type="unit", **{k: None for k in ("date_detain", "date_arrest_approved", "date_arrest", "date_bail", "date_surveil", "days_to_arrest", "detain_kind")},
                     detained=False, arrested=False, bailed=False, surveilled=False, n_bail=0, n_detain=0)
        p["label"] = b["label"]
        p["block"] = b
        persons.append(p)

    sections, anchors = R.locate_sections(text, segs, header["header_end"], blocks)
    sec = {k: (s, e) for k, s, e in sections}
    if "procedure" not in sec:
        flags |= R.FLAG_NO_PROCEDURE
    if "facts" not in sec:
        flags |= R.FLAG_NO_FACTS
    if "opinion" not in sec:
        flags |= R.FLAG_NO_OPINION
    if "closing" not in sec:
        flags |= R.FLAG_NO_CLOSING

    # statutes
    statutes = R.parse_statutes(text)
    for st in statutes:
        st["section"] = next((k for k, s, e in sections if s <= st["pos"] < e), "other")

    # prosecutors
    if "closing" in sec:
        tail_start = sec["closing"][0]
    elif "opinion" in sec:
        tail_start = max(sec["opinion"][0], len(text) - 600)
    else:
        tail_start = max(0, len(text) - 600)
    pros = R.parse_prosecutors(text, tail_start)
    if not pros:
        flags |= R.FLAG_NO_PROSECUTOR

    court = R.parse_court(text, sec["closing"][0] if "closing" in sec else None)

    # region / procuratorate
    reg = _G["regions"].resolve_proc(header["proc_name"], row["prov"], row["muni"], row["county"])
    prov_code_col = _G["regions"].norm_prov(row["prov"])

    # non-prosecution reason
    doc_type = header["doc_type"]
    np_reason = np_art = np_para = np_src = None
    if doc_type in ("不起诉决定书", "附条件不起诉决定书"):
        op = sec.get("opinion")
        np_reason, np_art, np_para, np_src = R.nonpros_reason(text, op[0] if op else None, op[1] if op else None, statutes)
        if doc_type == "附条件不起诉决定书" and np_reason is None:
            np_reason, np_src = "附条件不起诉", "doc_type"

    # offense date / age
    offense, facts_min = R.facts_dates(text, *sec["facts"]) if "facts" in sec else (None, None)
    custody_region = anchors.get("attachments")
    if custody_region is None and "closing" in sec:
        custody_region = sec["closing"][0]
    custody = R.custody_at_filing(text, custody_region, blocks)

    # ---- charges
    charge_rows = []   # dicts
    seen = {}          # (def_idx, stage, key) -> row

    def add_charge(def_idx, stage, source, raw, pos, attribution):
        if raw in R.JUNK_CHARGES:
            return
        names, method, residue = resolve_charge(raw)
        if not names:
            keys = [(None, raw)]
        else:
            keys = [(n, raw) for n in names]
        for std, _ in keys:
            k = (def_idx, stage, std if std else "RAW:" + raw)
            if k in seen:
                seen[k]["n_mentions"] += 1
                continue
            r = dict(def_idx=def_idx, stage=stage, source=source, charge_raw=raw, charge_std=std, resolve_method=method,
                     residue=residue if (residue and residue != "?") else None, is_multi=len(names) > 1,
                     attribution=attribution, is_primary=False, pos=pos, n_mentions=1)
            seen[k] = r
            charge_rows.append(r)
        if method in ("unresolved", "partial"):
            nonlocal_flags[0] |= R.FLAG_CHARGE_UNRESOLVED

    nonlocal_flags = [flags]
    n_person = sum(1 for p in persons if p["def_type"] == "person")
    # stage 涉嫌: per block
    for i, p in enumerate(persons):
        for raw, pos in p["suspect_charges"]:
            add_charge(i, "涉嫌", "block", raw, pos, "explicit")
    # stage 涉嫌: referral paragraph
    if "procedure" in sec:
        for names, raw, pos in R.charges_from_referral(text, *sec["procedure"]):
            if names:
                idxs = R.match_names(names, [p["block"] for p in persons])
                if any(x is None and R.looks_like_name(n) for n, x in zip(names, idxs)):
                    nonlocal_flags[0] |= R.FLAG_NAME_UNRESOLVED
                resolved = [x for x in idxs if x is not None]
                if resolved:
                    for x in resolved:
                        add_charge(x, "涉嫌", "referral", raw, pos, "explicit")
                elif len(persons) == 1:
                    add_charge(0, "涉嫌", "referral", raw, pos, "sole")
                else:
                    add_charge(None, "涉嫌", "referral", raw, pos, "unassigned")
            elif len(persons) == 1:
                add_charge(0, "涉嫌", "referral", raw, pos, "sole")
            else:
                add_charge(None, "涉嫌", "referral", raw, pos, "unassigned")
    # stage 起诉: opinion
    if "opinion" in sec:
        for names, raw, pos, pat in R.charges_from_opinion(text, *sec["opinion"]):
            src = "opinion_" + pat
            if names:
                idxs = R.match_names(names, [p["block"] for p in persons])
                if any(x is None and R.looks_like_name(n) for n, x in zip(names, idxs)):
                    nonlocal_flags[0] |= R.FLAG_NAME_UNRESOLVED
                resolved = [x for x in idxs if x is not None]
                if resolved:
                    for x in resolved:
                        add_charge(x, "起诉", src, raw, pos, "explicit")
                elif len(persons) == 1:
                    add_charge(0, "起诉", src, raw, pos, "sole")
                else:
                    add_charge(None, "起诉", src, raw, pos, "unassigned")
            elif len(persons) == 1:
                add_charge(0, "起诉", src, raw, pos, "sole")
            else:
                add_charge(None, "起诉", src, raw, pos, "unassigned")
    flags = nonlocal_flags[0]
    # primary = first resolved charge per defendant at stage 起诉 (fallback 涉嫌)
    for stage in ("起诉", "涉嫌"):
        firsts = {}
        for r in charge_rows:
            if r["stage"] == stage and r["charge_std"] and r["def_idx"] is not None and r["def_idx"] not in firsts:
                firsts[r["def_idx"]] = r
        for r in firsts.values():
            r["is_primary"] = True
        if stage == "起诉" and firsts:
            marked = set(firsts)
        else:
            marked = set()
        if stage == "涉嫌":
            for r in firsts.values():
                if r["def_idx"] in marked:
                    r["is_primary"] = False

    # ---- emit rows
    pub_date, sign_date = row["pub_date"], row["sign_date"]
    lag = (pub_date - sign_date).days if (pub_date and sign_date) else None
    cn = header["case_no"] or {}
    T = tabs
    T["documents"].append(dict(
        doc_id=doc_id, pub_year=year, doc_type=doc_type, doc_type_source=header["doc_type_source"], doc_heading_raw=header["doc_heading_raw"],
        title=row["title"], url=row["url"], pub_date=pub_date, sign_date=sign_date, sign_year=row["sign_year"], sign_month=row["sign_month"],
        sign_date_precision=row["sign_date_precision"], date_source=row["date_source"], pub_lag_days=lag,
        proc_name=header["proc_name"], proc_level=reg["proc_level"], proc_special=reg["proc_special"],
        region_code=reg["region_code"], region_level=reg["region_level"], prov_code=reg["prov_code"], prov_std=reg["prov_std"],
        city_code=reg["city_code"], muni_std=reg["muni_std"], county_code=reg["county_code"], county_std=reg["county_std"],
        region_source=reg["region_source"], region_ambiguous=reg["region_ambiguous"], prov_code_col=prov_code_col,
        court_name=court,
        case_no_full=cn.get("case_no_full"), case_no_proc=cn.get("case_no_proc"), case_no_series=cn.get("case_no_series"),
        case_no_year=cn.get("case_no_year"), case_no_seq=cn.get("case_no_seq"), case_no_bracket=cn.get("case_no_bracket"),
        n_case_no=header["n_case_no"],
        has_plea_leniency="认罪认罚" in text, has_sentencing_rec="量刑建议" in text,
        nonpros_reason=np_reason, nonpros_article=np_art, nonpros_paragraph=np_para, nonpros_source=np_src,
        n_defendants=n_person, n_unit_defendants=len(persons) - n_person,
        n_charges_suspect=sum(1 for r in charge_rows if r["stage"] == "涉嫌"), n_charges_pros=sum(1 for r in charge_rows if r["stage"] == "起诉"),
        n_statutes=len(statutes), n_prosecutors=len(pros), n_segments=len(segs),
        offense_date_first=offense, facts_date_min=facts_min,
        text_len=row["text_len"], quality_flags=row["quality_flags"], extract_flags=flags,
    ))
    for i, p in enumerate(persons):
        def_id = f"{doc_id}:{i}"
        age, mstat = R.minor_status(p["birth_year"], offense) if p["def_type"] == "person" else (None, None)
        b = p["block"]
        T["defendants"].append(dict(
            def_id=def_id, doc_id=doc_id, pub_year=year, seq_in_doc=i, def_type=p["def_type"], label=p["label"],
            is_name_masked=p["is_name_masked"], has_alias=p["alias"] is not None, gender=p["gender"], birth_year=p["birth_year"],
            birth_year_source=p.get("birth_year_source"), ethnicity=p["ethnicity"], edu_raw=p["edu_raw"], edu_level=p["edu_level"],
            politics=p["politics"], occupation_raw=p["occupation_raw"], hukou_text=p["hukou_text"], hukou_code=p["id_prefix"],
            id_birth_year=p["id_birth_year"], id_shape=p["id_shape"], has_id=p["id_raw"] is not None, is_id_unmasked=p["is_id_unmasked"],
            detained=p["detained"], arrested=p["arrested"], bailed=p["bailed"], surveilled=p["surveilled"], detain_kind=p["detain_kind"],
            date_detain=p["date_detain"], date_arrest_approved=p["date_arrest_approved"], date_arrest=p["date_arrest"], date_bail=p["date_bail"],
            date_surveil=p["date_surveil"], days_to_arrest=p["days_to_arrest"], n_bail=p["n_bail"],
            custody_at_filing=custody[i], age_at_offense=age, minor_status=mstat,
            n_suspect_charges=len(p["suspect_charges"]), block_start=b["start"], block_end=b["end"],
        ))
        T["defendants_sensitive"].append(dict(def_id=def_id, doc_id=doc_id, pub_year=year, name_raw=text[b["start"]: b["name_end"]],
                                              name=p["name"], alias=p["alias"], id_raw=p["id_raw"]))
    for r in charge_rows:
        T["charges"].append(dict(doc_id=doc_id, pub_year=year, def_id=f"{doc_id}:{r['def_idx']}" if r["def_idx"] is not None else None,
                                 seq_in_doc=r["def_idx"], stage=r["stage"], source=r["source"], charge_raw=r["charge_raw"],
                                 charge_std=r["charge_std"], resolve_method=r["resolve_method"], residue=r["residue"], is_multi=r["is_multi"],
                                 attribution=r["attribution"], is_primary=r["is_primary"], pos=r["pos"], n_mentions=min(r["n_mentions"], 127)))
    for j, st in enumerate(statutes):
        T["statutes"].append(dict(doc_id=doc_id, pub_year=year, seq=j, law=st["law"], law_raw=st["law_raw"][:60], article=_i16(st["article"]),
                                  sub_article=_i8(st["sub_article"]), paragraph=_i8(st["paragraph"]), item=_i8(st["item"]),
                                  section=st["section"], pos=st["pos"], ref_raw=st["ref_raw"][:60]))
    for j, pr in enumerate(pros[:120]):
        T["prosecutors"].append(dict(doc_id=doc_id, pub_year=year, seq=j, role=pr["role"], name=pr["name"], is_masked=pr["is_masked"],
                                     line_raw=pr["line_raw"]))
    for k, s, e in sections:
        T["sections"].append(dict(doc_id=doc_id, pub_year=year, sect_type=k, start=s, end=e))

    # ---- stats
    stats["docs"] += 1
    stats["doc_type=" + str(doc_type)] += 1
    for bit, name in R.FLAG_NAMES.items():
        if flags & bit:
            stats["flag_" + name] += 1
    stats["has_proc"] += header["proc_name"] is not None
    stats["has_case_no"] += header["case_no"] is not None
    stats["has_court"] += court is not None
    stats["has_region_county"] += reg["region_level"] == "county"
    stats["has_region_any"] += reg["region_code"] is not None
    stats["has_prosecutor"] += bool(pros)
    stats["has_statute"] += bool(statutes)
    stats["has_statute_xf"] += any(s["law"] == "刑法" for s in statutes)
    stats["has_defendant"] += n_person > 0
    stats["has_charge_suspect"] += any(r["stage"] == "涉嫌" for r in charge_rows)
    stats["has_charge_pros"] += any(r["stage"] == "起诉" for r in charge_rows)
    stats["has_charge_any_std"] += any(r["charge_std"] for r in charge_rows)
    stats["has_offense_date"] += offense is not None
    stats["n_defendants_total"] += n_person
    stats["n_charge_rows"] += len(charge_rows)
    stats["charge_rows_unresolved"] += sum(1 for r in charge_rows if r["charge_std"] is None)
    stats["charge_rows_unassigned"] += sum(1 for r in charge_rows if r["attribution"] == "unassigned")
    for p in persons:
        if p["def_type"] != "person":
            continue
        stats["def_has_birth"] += p["birth_year"] is not None
        stats["def_has_id_prefix"] += p["id_prefix"] is not None
        stats["def_has_gender"] += p["gender"] is not None
        stats["def_has_edu"] += p["edu_raw"] is not None
        stats["def_has_measure"] += any((p["detained"], p["arrested"], p["bailed"], p["surveilled"]))
        stats["def_has_measure_date"] += any((p["date_detain"], p["date_arrest"], p["date_bail"], p["date_surveil"]))
        stats["def_name_masked"] += p["is_name_masked"]
        stats["def_id_unmasked"] += p["is_id_unmasked"]
    if doc_type in ("不起诉决定书", "附条件不起诉决定书"):
        stats["nonpros_docs"] += 1
        stats["nonpros_reason=" + str(np_reason)] += 1
        stats["nonpros_source=" + str(np_src)] += 1
    if reg["prov_code"] and prov_code_col and reg["prov_code"] != prov_code_col:
        stats["prov_name_vs_column_disagree"] += 1


def _i16(v):
    return None if v is None or v > 32767 else v


def _i8(v):
    return None if v is None or v > 127 else v


# --------------------------------------------------------------------------- task
def run_task(task):
    year, path, rg, out_root, out_sens = task
    t0 = time.time()
    pf = pq.ParquetFile(path)
    tbl = pf.read_row_group(rg, columns=RAW_COLS)
    rows = tbl.to_pylist()
    tabs = {k: [] for k in SCHEMAS}
    stats = Counter()
    for row in rows:
        try:
            extract_doc(row, year, tabs, stats)
        except Exception as ex:  # keep going; record the failure
            stats["EXTRACT_ERRORS"] += 1
            stats["err:" + type(ex).__name__ + ":" + str(ex)[:80]] += 1
            tabs["documents"].append(dict(doc_id=row["doc_id"], pub_year=year, title=row["title"], url=row["url"], pub_date=row["pub_date"],
                                          sign_date=row["sign_date"], text_len=row["text_len"], quality_flags=row["quality_flags"],
                                          extract_flags=-1, **{f.name: None for f in SCHEMAS["documents"] if f.name not in
                                                               ("doc_id", "pub_year", "title", "url", "pub_date", "sign_date", "text_len", "quality_flags", "extract_flags")}))
            if stats["EXTRACT_ERRORS"] <= 3:
                import traceback
                stats["trace:" + traceback.format_exc()[-300:]] += 1
    for name, schema in SCHEMAS.items():
        root = out_sens if name in SENSITIVE_TABLES else out_root
        d = root / name / f"pub_year={year}"
        d.mkdir(parents=True, exist_ok=True)
        recs = tabs[name]
        cols = {f.name: [r.get(f.name) for r in recs] for f in schema}
        t = pa.table(cols, schema=schema)
        pq.write_table(t, d / f"part-{rg:03d}.parquet", compression="zstd", compression_level=3)
    stats["elapsed_s"] = round(time.time() - t0, 1)
    return (year, rg, len(rows), stats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="one row group per year into a scratch directory")
    ap.add_argument("--out", type=Path, default=None, help="output root (default ~/indictment_work/docs.parquet)")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--years", type=str, default=None, help="comma list, e.g. 2016,2017")
    ap.add_argument("--max-tasks", type=int, default=None)
    args = ap.parse_args()

    out_root = args.out or OUT
    out_sens = (args.out / "sensitive") if args.out else OUT_SENS
    if args.smoke and args.out is None:
        out_root = WORK / "scratch_m1_smoke"
        out_sens = out_root / "sensitive"
    for p in (out_root, out_sens):
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)
    LOG.mkdir(parents=True, exist_ok=True)

    tasks = []
    years = [int(y) for y in args.years.split(",")] if args.years else None
    for d in sorted(RAW.glob("pub_year=*")):
        year = int(d.name.split("=")[1])
        if years and year not in years:
            continue
        path = d / "part-0.parquet"
        n_rg = pq.ParquetFile(path).metadata.num_row_groups
        for rg in range(n_rg):
            if args.smoke and rg != n_rg // 2:
                continue
            tasks.append((year, str(path), rg, out_root, out_sens))
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    print(f"{len(tasks)} tasks -> {out_root}", file=sys.stderr)

    t0 = time.time()
    total = Counter()
    done_rows = 0
    ctx = get_context("fork")
    with ctx.Pool(args.workers, initializer=init_worker) as pool:
        for year, rg, n, stats in pool.imap_unordered(run_task, tasks):
            done_rows += n
            total.update(stats)
            el = time.time() - t0
            print(f"  {year} rg{rg:03d} {n:>6,} docs  {stats['elapsed_s']:6.1f}s   total {done_rows:>10,}  {el:6.0f}s  {done_rows/el:,.0f} docs/s",
                  file=sys.stderr, flush=True)
    total["elapsed_s"] = round(time.time() - t0, 1)

    docs = total["docs"] or 1
    summary = {"docs": total["docs"], "elapsed_s": total["elapsed_s"], "errors": total.get("EXTRACT_ERRORS", 0), "rates": {}, "counts": {}}
    for k, v in sorted(total.items()):
        if k.startswith("has_"):
            summary["rates"][k] = round(100 * v / docs, 2)
        elif k.startswith("def_"):
            summary["rates"][k] = round(100 * v / max(total["n_defendants_total"], 1), 2)
        elif k not in ("docs", "elapsed_s"):
            summary["counts"][k] = v
    name = "m1_summary_smoke.json" if args.smoke or args.out else "m1_summary.json"
    (LOG / name).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
