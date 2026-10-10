#!/usr/bin/env python3
"""
P2b — separating "same place" from "same system × level" in the L1 position network
(WORKLOG Step 5, open item 1; 条块共谋网络方案 v1 §7 稳健性 / §8.2).

Two instruments, same universe as P2 (2014–2017, system × level nodes, weight 1/(n−1)):

  A. Stratified null models.  P2 permuted position labels within signing year only, so a pair of
     village cadres from one county counted as "same system, same level" against a null in which they
     could have been matched with anybody in the country.  Here the permutation blocks are
     year × province, year × prefecture city (documents.city_code) and year × prosecuting office
     (documents.region_code).  Within a block every defendant is (almost) local, so what survives is
     the system × level structure net of locality.  Blocks with one document cannot be permuted
     (the pair multiset of a single document is invariant), so for the office level the shares are
     also reported on "live" blocks (≥ 2 documents) only.

  B. Place written in the organisation name.  P1 resolved `position_region_code` from the organisation
     text alone (never from the prosecuting office).  Each pair gets a place relation — 同县 / 同市异县 /
     同省异市 / 异省 when both sides resolve far enough, 同市(县不明) / 同省(市不明) when only a coarser
     comparison is possible, 不明 otherwise — crossed with its 条块 relation.  The permutation moves
     node and place together, so the null has the same cross-table.

Outputs (repo, aggregates only): results/p2/p2b_strata.csv, p2b_tiaokuai_city.csv, p2b_sys_matrix_city.csv,
  p2b_nodes_city.csv, p2b_place_tiaokuai.csv, p2b_place_cross.csv, p2b_tables.md,
  fig5_strata.png, fig6_place.png; ~/indictment_work/logs/p2b_summary.json

Run: python3 scripts/p2b_geography.py [--perm 1000] [--seed 20261010]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p2_network as P2  # noqa: E402
from p2_network import LEVEL_ORDER, MIN_CELL, OUT, LOG, Projection, conditional, load, set_font, universe  # noqa: E402

PLACE_CATS = ["同县", "同市异县", "同省异市", "异省", "同市(县不明)", "同省(市不明)", "不明"]
TK_CATS = ["同条同块", "同条", "同块", "跨", "层级不明"]
STRATA = {"year": "年", "year_prov": "年×省", "year_city": "年×地级市", "year_office": "年×起诉院"}


# --------------------------------------------------------------------------- strata
def strata_codes(d, kind):
    y = d.sign_year.astype(int).astype(str)
    prov = d.prov_code.astype("string").fillna("NA")
    if kind == "year":
        key = y
    elif kind == "year_prov":
        key = y + "|" + prov
    elif kind == "year_city":
        city = d.city_code.astype("string").fillna(prov + "_nocity")
        key = y + "|" + city
    elif kind == "year_office":
        key = y + "|" + d.doc_region.astype("string").fillna("NA")
    else:
        raise ValueError(kind)
    codes, uni = pd.factorize(key)
    return codes.astype(int), len(uni)


def live_mask(P, codes):
    """Pairs whose stratum holds ≥ 2 documents (the only pairs a within-stratum permutation can change)."""
    docs_per = pd.DataFrame({"s": codes, "doc": P.d.doc_id}).drop_duplicates().groupby("s").size()
    live_rows = pd.Series(codes).map(docs_per).to_numpy() >= 2
    return live_rows[P.I]


# --------------------------------------------------------------------------- place of the organisation
def place_arrays(d):
    """Per defendant: resolution level (0 none / 1 province / 2 city / 3 county) and integer codes p2, c4, c6 (−1 unknown)."""
    rc = d.pos_region.astype("string")
    rc = rc.where(rc.str.len() == 6)
    n = len(d)
    res = np.zeros(n, int)
    p2 = np.full(n, -1)
    c4 = np.full(n, -1)
    c6 = np.full(n, -1)
    ok = rc.notna().to_numpy()
    s = rc[ok]
    city_known = (s.str[2:4] != "00").to_numpy()
    county_known = (s.str[4:6] != "00").to_numpy()
    p2[ok] = s.str[:2].astype(int).to_numpy()
    c4[ok] = np.where(city_known, s.str[:4].astype(int).to_numpy(), -1)
    c6[ok] = np.where(county_known, s.astype(int).to_numpy(), -1)
    res[ok] = np.where(county_known, 3, np.where(city_known, 2, 1))
    return res, p2, c4, c6


def place_relation(res, p2, c4, c6, A, B):
    """Place relation index into PLACE_CATS for defendant index arrays A, B."""
    m = np.minimum(res[A], res[B])
    sp = p2[A] == p2[B]
    sc = (c4[A] == c4[B]) & (c4[A] >= 0)
    sk = (c6[A] == c6[B]) & (c6[A] >= 0)
    out = np.full(len(A), 6)
    m3, m2, m1 = m == 3, m == 2, m == 1
    out[m3 & sk] = 0
    out[m3 & ~sk & sc] = 1
    out[m3 & ~sk & ~sc & sp] = 2
    out[m3 & ~sp] = 3
    out[m2 & sc] = 4
    out[m2 & ~sc & sp] = 2
    out[m2 & ~sp] = 3
    out[m1 & sp] = 5
    out[m1 & ~sp] = 3
    return out


def place_cross(P, res, p2, c4, c6, n_perm=300, seed=2):
    """Weight by place relation × 条块 relation, observed and under the projection's own strata."""
    rel = np.array([[TK_CATS.index(P.relation(i, j)) for j in range(P.K)] for i in range(P.K)])

    def table(pi):
        A, B = pi[P.I], pi[P.J]
        pc = place_relation(res, p2, c4, c6, A, B)
        tk = rel[P.labels[A], P.labels[B]]
        T = np.zeros((len(PLACE_CATS), len(TK_CATS)))
        np.add.at(T, (pc, tk), P.W)
        return T

    obs = table(np.arange(len(P.labels)))
    rng = np.random.default_rng(seed)
    draws = np.zeros((n_perm,) + obs.shape)
    for p in range(n_perm):
        pi = np.arange(len(P.labels))
        for g in P.groups:
            pi[g] = pi[rng.permutation(g)]
        draws[p] = table(pi)
    return obs, draws.mean(0), draws.std(0), draws


# --------------------------------------------------------------------------- figures
def fig_strata(strata_df, path):
    import matplotlib.pyplot as plt
    rows = strata_df[strata_df.subset == "all"].copy()
    rows["label"] = rows.strata.map(STRATA)
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    ax = axes[0]
    x = np.arange(len(rows))
    ax.bar(x - 0.2, rows["null_同条同块"], width=0.4, color="#95a5a6", label="零模型：格内份额")
    ax.bar(x + 0.2, rows["null_跨"], width=0.4, color="#2980b9", label="零模型：跨份额")
    ax.axhline(rows["obs_同条同块"].iloc[0], color="#7f8c8d", ls="--", lw=1.2)
    ax.axhline(rows["obs_跨"].iloc[0], color="#2980b9", ls="--", lw=1.2)
    ax.text(-0.45, rows["obs_同条同块"].iloc[0] + 0.01, "观测格内 0.66", ha="left", fontsize=8, color="#7f8c8d")
    ax.text(-0.45, rows["obs_跨"].iloc[0] + 0.01, "观测跨 0.09", ha="left", fontsize=8, color="#2980b9")
    ax.set_xticks(x); ax.set_xticklabels(rows.label)
    ax.set_ylim(0, 0.8); ax.set_ylabel("边权重份额")
    ax.set_title("零模型越贴近地方，格内份额的期望越高；观测值不变（虚线）", fontsize=9)
    ax.legend(fontsize=8, loc="upper center")
    ax = axes[1]
    w = 0.26
    for k, (c, col) in enumerate(zip(["条", "块", "跨"], ["#2980b9", "#27ae60", "#c0392b"])):
        ax.bar(x + (k - 1) * w, rows[f"ratio_{c}"], width=w, color=col, label=c)
    ax.axhline(1, color="black", lw=0.6)
    ax.set_xticks(x); ax.set_xticklabels(rows.label)
    ax.set_ylabel("观测 / 零模型（跨出本格、层级已知的边）")
    ax.set_title("跨出本格后：条 / 块 / 跨 相对零模型，按分层方式", fontsize=9)
    ax.legend(fontsize=8)
    fig.suptitle("图5 分层零模型：地域控制得越严，条块结构还剩多少（2014–2017）", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def fig_place(pt, path):
    import matplotlib.pyplot as plt
    cats = ["同条同块", "同条", "同块", "跨", "层级不明"]
    colors = ["#7f8c8d", "#2980b9", "#27ae60", "#c0392b", "#dddddd"]
    places = [p for p in PLACE_CATS if p in set(pt.place)]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.8), sharey=True)
    for ax, col, title in ((axes[0], "share_obs", "观测"), (axes[1], "share_null", "零模型（年×地级市）")):
        bottom = np.zeros(len(places))
        for c, color in zip(cats, colors):
            vals = np.array([pt[(pt.place == p) & (pt.relation == c)][col].iloc[0] if len(pt[(pt.place == p) & (pt.relation == c)]) else 0 for p in places])
            ax.bar(places, vals, bottom=bottom, label=c, color=color)
            bottom += vals
        ax.set_title(f"{title}：按两个单位所在地的关系", fontsize=9, pad=16)
        ax.set_ylim(0, 1)
        ax.tick_params(axis="x", labelsize=8)
    axes[0].set_ylabel("边权重份额")
    axes[1].legend(loc="upper right", fontsize=8)
    wt = pt.groupby("place").weight.sum()
    for ax in axes:
        for i, p in enumerate(places):
            ax.text(i, 1.012, f"w={wt.get(p, 0):.0f}", ha="center", va="bottom", fontsize=7)
    fig.suptitle("图6 条块关系 × 单位所在地关系（所在地只取单位名里写出的地名，P1）", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


# --------------------------------------------------------------------------- markdown
def write_tables(strata_df, tk_city, sysM, nodes_cmp, pt, cross, cell_place, both_county, P, path):
    L = []
    L.append("# P2b 结果表：地域 vs 条块（自动生成，`scripts/p2b_geography.py`）\n")
    L.append(f"口径同 P2：2014–2017 年落款、至少两名已知位置被告人的职务犯罪文书 {P.n_docs:,} 份，被告人 {len(P.d):,}，节点 {P.K} 个，"
             f"边权 1/(n−1)。零模型 = 在分层块内随机重排被告人的（位置, 单位所在地）{P.n_perm} 次。\n")
    L.append("## 表A 分层零模型：边权重的条块份额\n")
    L.append("观测份额不随分层变化；变的是零模型。\"可重排\" = 落在 ≥ 2 份文书的分层块里的边权重份额；\"仅可重排\" 行只用这些边算观测与零模型。\n")
    L.append("| 分层 | 块数 | 可重排 | 格内 观测 / 零模型 (z) | 条 观测 / 零模型 (z) | 块 观测 / 零模型 (z) | 跨 观测 / 零模型 (z) | 跨出本格后 条 : 块 : 跨 obs/exp |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in strata_df.itertuples():
        name = STRATA[r.strata] + ("（仅可重排）" if r.subset == "live" else "")
        L.append(f"| {name} | {r.n_strata:,} | {r.live_share:.2f} | {r.obs_同条同块:.3f} / {r.null_同条同块:.3f} ({r.z_同条同块:+.0f}) | "
                 f"{r.obs_同条:.3f} / {r.null_同条:.3f} ({r.z_同条:+.0f}) | {r.obs_同块:.3f} / {r.null_同块:.3f} ({r.z_同块:+.0f}) | "
                 f"{r.obs_跨:.3f} / {r.null_跨:.3f} ({r.z_跨:+.0f}) | {r.ratio_条:.1f} : {r.ratio_块:.1f} : {r.ratio_跨:.1f} |")
    L.append("\n## 表B 年×地级市零模型下的分层份额（对照 P2 表B）\n")
    L.append("| 子集 | 对数 | 同条同块份额 观测 / 零模型 | 条件份额 条 | 块 | 跨 | 条 obs/exp | 块 obs/exp | 跨 obs/exp |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for sub in tk_city.subset.unique():
        ts = tk_city[tk_city.subset == sub]
        t = ts.set_index("relation")
        if t.loc[["同条", "同块", "跨"], "share_null"].sum() == 0:
            continue
        o, e, r = conditional(ts)
        L.append(f"| {sub} | {int(ts.n_pairs.sum()):,} | {t.loc['同条同块', 'share_obs']:.2f} / {t.loc['同条同块', 'share_null']:.2f} | {o[0]:.2f} | {o[1]:.2f} | {o[2]:.2f} | {r[0]:.1f} | {r[1]:.1f} | {r[2]:.1f} |")
    L.append("\n## 表C 系统 × 系统：观测 / 零模型期望，年×地级市分层（括号内为 P2 的按年分层值）\n")
    names = list(sysM.index)
    L.append("| | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    for a in names:
        cells = []
        for b in names:
            v = sysM.loc[a, b]
            cells.append("" if not isinstance(v, str) else v)
        L.append(f"| {a} | " + " | ".join(cells) + " |")
    L.append("\n## 表D 位置节点：跨系统 obs/exp 与介数 z，按年分层（P2）vs 年×地级市分层\n")
    L.append("| 节点 | 被告人 | 自份额 | 跨系统 obs/exp 年 | 年×市 | 介数 z 年 | 年×市 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in nodes_cmp.itertuples():
        L.append(f"| {r.node} | {r.n_defendants:,} | {r.self_share:.2f} | {r.cross_sys_ratio_year:.2f} | {r.cross_sys_ratio_city:.2f} | {r.betweenness_z_year:+.1f} | {r.betweenness_z_city:+.1f} |")
    L.append("\n## 表E 单位所在地关系 × 条块关系（观测权重；括号内 = 年×地级市零模型期望）\n")
    L.append("所在地只来自单位名里写出的地名（P1 `position_region_code`），无公职和打码单位为不明。\n")
    L.append("| 所在地关系 | 权重合计 | 份额 | " + " | ".join(TK_CATS) + " |")
    L.append("|---|---|---|" + "---|" * len(TK_CATS))
    tot = cross["obs"].sum()
    for i, p in enumerate(PLACE_CATS):
        w = cross["obs"][i].sum()
        if w == 0 and cross["null"][i].sum() == 0:
            continue
        cells = [f"{cross['obs'][i, k]:.0f} ({cross['null'][i, k]:.0f})" for k in range(len(TK_CATS))]
        L.append(f"| {p} | {w:.0f} | {w / tot:.2f} | " + " | ".join(cells) + " |")
    L.append("\n## 表F 按所在地关系：格内份额与条件份额，观测 vs 年×地级市零模型\n")
    L.append("| 所在地关系 | 权重 | 格内 观测 / 零模型 (z) | 条件份额 条 : 块 : 跨 观测 | 零模型 | obs/exp 条 / 块 / 跨 |")
    L.append("|---|---|---|---|---|---|")
    for p in PLACE_CATS:
        ts = pt[pt.place == p]
        if not len(ts) or ts.weight.sum() < 20:
            continue
        t = ts.set_index("relation")
        o, e, r = conditional(ts) if t.loc[["同条", "同块", "跨"], "share_null"].sum() > 0 else (np.full(3, np.nan),) * 3
        L.append(f"| {p} | {ts.weight.sum():.0f} | {t.loc['同条同块', 'share_obs']:.2f} / {t.loc['同条同块', 'share_null']:.2f} ({t.loc['同条同块', 'z']:+.0f}) | "
                 f"{o[0]:.2f} : {o[1]:.2f} : {o[2]:.2f} | {e[0]:.2f} : {e[1]:.2f} : {e[2]:.2f} | {r[0]:.1f} / {r[1]:.1f} / {r[2]:.1f} |")
    L.append("\n## 表G 格内（同系统同层级）边权重按所在地关系的分解\n")
    L.append("| 所在地关系 | 格内权重 | 份额 |")
    L.append("|---|---|---|")
    for p, w, s in cell_place:
        L.append(f"| {p} | {w:.0f} | {s:.2f} |")
    L.append(f"\n两端都定到县的边：{both_county['n']:,} 对，其中同县 {both_county['same']:.1%}；定到县的位置中 {both_county['match_office']:.1%} 就是起诉院所在县。\n")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perm", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261010)
    args = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    set_font()
    con = duckdb.connect()
    df = load(con)
    d = universe(df)
    summary = {}

    # ---- A. stratified null models on the main universe
    strata_rows, projections = [], {}
    for kind in STRATA:
        codes, n_strata = strata_codes(d, kind)
        P = Projection(d, n_perm=args.perm, seed=args.seed, betweenness=True, strata=codes)
        projections[kind] = (P, codes)
        lm = live_mask(P, codes)
        live_share = float(P.W[lm].sum() / P.W.sum())
        for subset, mask in (("all", None), ("live", lm)):
            if subset == "live" and live_share > 0.995:
                continue
            tk = P.tiaokuai(mask, kind).set_index("relation")
            o, e, r = conditional(tk.reset_index())
            strata_rows.append(dict(strata=kind, subset=subset, n_strata=n_strata, live_share=live_share if subset == "all" else 1.0,
                                    **{f"obs_{c}": tk.loc[c, "share_obs"] for c in TK_CATS}, **{f"null_{c}": tk.loc[c, "share_null"] for c in TK_CATS},
                                    **{f"z_{c}": tk.loc[c, "z"] for c in TK_CATS}, ratio_条=r[0], ratio_块=r[1], ratio_跨=r[2]))
        print(f"strata {kind}: {n_strata} blocks, live share {live_share:.2f} ({time.time() - t0:.0f}s)")
    strata_df = pd.DataFrame(strata_rows)
    strata_df.to_csv(OUT / "p2b_strata.csv", index=False, encoding="utf-8-sig")

    # by level / charge / year / court under the year × city null
    Pc, codes_c = projections["year_city"]
    tk = [Pc.tiaokuai(None, "all")]
    for lv in LEVEL_ORDER:
        if lv != "不明":
            m = Pc.pair_mask("level", lv)
            if m.sum() > 50:
                tk.append(Pc.tiaokuai(m, f"level={lv}"))
    for cgp in ["贿赂", "贪污挪用", "渎职", "其他"]:
        m = Pc.pair_mask_both("charge_group", cgp)
        if m.sum() > 50:
            tk.append(Pc.tiaokuai(m, f"charge={cgp}"))
    for y in (2014, 2015, 2016, 2017):
        tk.append(Pc.tiaokuai(Pc.doc_mask("sign_year", y), f"year={y}"))
    for pl in ("basic", "municipal"):
        m = Pc.doc_mask("proc_level", pl)
        if m.sum() > 50:
            tk.append(Pc.tiaokuai(m, f"court={pl}"))
    tk_city = pd.concat(tk, ignore_index=True)
    tk_city.to_csv(OUT / "p2b_tiaokuai_city.csv", index=False, encoding="utf-8-sig")

    # node table under year × city, compared with the P2 (year) table
    nodes_city = Pc.node_table()
    nodes_city.to_csv(OUT / "p2b_nodes_city.csv", index=False, encoding="utf-8-sig")
    nodes_year = pd.read_csv(OUT / "p2_nodes_sys_level.csv")
    nodes_cmp = nodes_year[["node", "n_defendants", "self_share", "cross_sys_ratio", "betweenness_z"]].merge(
        nodes_city[["node", "cross_sys_ratio", "betweenness_z"]], on="node", suffixes=("_year", "_city"))
    nodes_cmp = nodes_cmp[nodes_cmp.n_defendants >= MIN_CELL].sort_values("n_defendants", ascending=False)
    print(f"year×city node table done ({time.time() - t0:.0f}s)")

    # system × system under year × city (and the P2 year value in brackets)
    dS = universe(df, node="sys")
    codes_s, _ = strata_codes(dS, "year_city")
    PSc = Projection(dS, n_perm=args.perm, seed=args.seed, betweenness=False, strata=codes_s)
    PSy = Projection(dS, n_perm=args.perm, seed=args.seed, betweenness=False)
    names = PSc.nodes
    sysM = pd.DataFrame(index=names, columns=names, dtype=object)
    rows = []
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            o = PSc.M_obs[i, j]
            muc, sdc = PSc.perm_M[:, i, j].mean(), PSc.perm_M[:, i, j].std()
            muy = PSy.perm_M[:, i, j].mean()
            if o == 0 and muc == 0:
                continue
            zc = (o - muc) / sdc if sdc > 0 else np.nan
            sysM.loc[a, b] = f"{o / muc:.2f} ({o / muy:.2f})" if muc > 0 and muy > 0 else f"{o:.0f}"
            if j >= i:
                rows.append(dict(system_a=a, system_b=b, weight=round(o, 2), exp_city=round(muc, 2), ratio_city=round(o / muc, 2) if muc else np.nan,
                                 z_city=round(zc, 1) if np.isfinite(zc) else np.nan, exp_year=round(muy, 2), ratio_year=round(o / muy, 2) if muy else np.nan))
    pd.DataFrame(rows).to_csv(OUT / "p2b_sys_matrix_city.csv", index=False, encoding="utf-8-sig")
    print(f"system matrix done ({time.time() - t0:.0f}s)")

    # ---- B. place written in the organisation name
    res, p2, c4, c6 = place_arrays(d)
    obs, mu, sd, draws = place_cross(Pc, res, p2, c4, c6, n_perm=300, seed=args.seed + 1)
    cross = dict(obs=obs, null=mu)
    rows = []
    for i, p in enumerate(PLACE_CATS):
        for k, c in enumerate(TK_CATS):
            rows.append(dict(place=p, relation=c, weight=round(obs[i, k], 2), null_mean=round(mu[i, k], 2), null_sd=round(sd[i, k], 3),
                             z=round((obs[i, k] - mu[i, k]) / sd[i, k], 1) if sd[i, k] > 0 else np.nan))
    pd.DataFrame(rows).to_csv(OUT / "p2b_place_cross.csv", index=False, encoding="utf-8-sig")
    # shares within each place category, observed vs null (null share computed draw by draw)
    pt = []
    for i, p in enumerate(PLACE_CATS):
        wt = obs[i].sum()
        if wt == 0:
            continue
        shares_obs = obs[i] / wt
        with np.errstate(invalid="ignore", divide="ignore"):
            shares_null = draws[:, i, :] / draws[:, i, :].sum(1, keepdims=True)
        nm, ns = np.nanmean(shares_null, 0), np.nanstd(shares_null, 0)
        for k, c in enumerate(TK_CATS):
            pt.append(dict(place=p, relation=c, weight=round(obs[i, k], 2), share_obs=round(shares_obs[k], 3), share_null=round(nm[k], 3),
                           z=round((shares_obs[k] - nm[k]) / ns[k], 1) if ns[k] > 0 else np.nan, n_pairs=int(0)))
    pt = pd.DataFrame(pt)
    pt.to_csv(OUT / "p2b_place_tiaokuai.csv", index=False, encoding="utf-8-sig")
    # decomposition of the within-cell weight by place, and the both-county statistic
    cell_w = obs[:, 0]
    cell_place = [(p, cell_w[i], cell_w[i] / cell_w.sum()) for i, p in enumerate(PLACE_CATS) if cell_w[i] > 0]
    pc_obs = place_relation(res, p2, c4, c6, Pc.I, Pc.J)
    bc = np.isin(pc_obs, [0, 1, 2, 3])
    both_county = dict(n=int(bc.sum()), same=float((pc_obs[bc] == 0).mean()) if bc.sum() else np.nan,
                       match_office=float((d.pos_region[res == 3].astype(str) == d.doc_region[res == 3].astype(str)).mean()))
    print(f"place relation done ({time.time() - t0:.0f}s)")

    # ---- figures and tables
    fig_strata(strata_df, OUT / "fig5_strata.png")
    fig_place(pt, OUT / "fig6_place.png")
    write_tables(strata_df, tk_city, sysM, nodes_cmp, pt, cross, cell_place, both_county, Pc, OUT / "p2b_tables.md")

    summary["strata"] = strata_df.round(4).to_dict("records")
    summary["place_resolution"] = {["none", "prov", "city", "county"][int(k)]: int(v) for k, v in zip(*np.unique(res, return_counts=True))}
    summary["both_county"] = both_county
    summary["cell_place"] = [(p, float(w), float(s)) for p, w, s in cell_place]
    summary["top_betweenness_z_city"] = nodes_city[nodes_city.n_defendants >= MIN_CELL].sort_values("betweenness_z", ascending=False).head(8)[["node", "betweenness_z"]].to_dict("records")
    summary["seconds"] = round(time.time() - t0, 1)
    json.dump(summary, open(LOG / "p2b_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print(f"done in {time.time() - t0:.0f}s → {OUT}")


if __name__ == "__main__":
    main()
