#!/usr/bin/env python3
"""
P2 — first 条块 position network from the L1 in-case edges (条块共谋网络方案 v1 §7, §9 P2).

Input : ~/indictment_work/docs.parquet/{positions,network_edges,documents}   (P1 / M1 output)
Output: results/p2/  (repo; aggregates only, no names)
          p2_nodes_<variant>.csv      one row per node: size, weighted degree, betweenness, eigenvector,
                                      z-scores against the null model, 条/块 shares
          p2_pairs_<variant>.csv      one row per node pair: observed weight, null mean/sd, z, obs/exp
          p2_sys_matrix.csv           system × system observed weight, expected, z
          p2_tiaokuai.csv             同条 / 同块 / 跨 shares by level, charge group, year, court level — observed vs null
          p2_robustness.csv           top nodes and 条/块 shares under each variant
          p2_bribery_directed.csv     in-case 行贿 → 受贿 pairs, system of giver × system of taker
          fig1_sys_heatmap.png · fig2_network.png · fig3_tiaokuai.png · fig4_degree_z.png
        ~/indictment_work/logs/p2_summary.json

Method
  Universe: person defendants whose offence-time position has a known system, in documents (signed in
  the window) with at least two such defendants.  Node = system × level (cells with < MIN_CELL
  defendants are merged into the system's 层级不明 cell; 无公职 is one node).
  Two-mode projection: every pair of defendants in a document is an edge between their nodes with
  weight 1/(n−1), n = defendants of the document in the universe, so each document contributes a total
  weight of n/2 regardless of its size (plan §4 两模投影).  W_docs = number of documents in which the pair
  co-occurs is also reported.
  Null model: position labels are permuted across defendants within each signing year (document sizes,
  node frequencies and the year mix are all preserved); N_PERM draws; z = (obs − mean) / sd.
  Centrality: weighted degree (self-loops excluded), betweenness with distance = 1/weight, eigenvector
  centrality on the weighted graph; z-scores for degree and betweenness from the same draws.
  条块: a pair is 同条同块 (same cell), 同条 (same system, different level), 同块 (same level, different
  system), 跨 (both differ); pairs with an unknown level on either side are 层级不明 unless same cell.

Run: python3 scripts/p2_network.py [--perm 1000] [--seed 20261010]
"""
import argparse
import json
import sys
import time
from itertools import combinations
from pathlib import Path

import duckdb
import networkx as nx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WORK = Path.home() / "indictment_work"
DOCS = WORK / "docs.parquet"
OUT = ROOT / "results" / "p2"
LOG = WORK / "logs"

SYS_ORDER = ["党委系统", "政府部门", "人大政协", "公检法司", "国企", "事业单位", "村级组织", "金融机构", "民营企业", "无公职"]
LEVEL_ORDER = ["省", "市", "县", "区", "乡镇街道", "村社区", "不明"]
MIN_CELL = 30


# --------------------------------------------------------------------------- data
def load(con):
    con.sql(f"create view positions as select * from read_parquet('{DOCS}/positions/pub_year=*/*.parquet', hive_partitioning=1)")
    con.sql(f"create view edges as select * from read_parquet('{DOCS}/network_edges/pub_year=*/*.parquet', hive_partitioning=1)")
    con.sql(f"create view documents as select * from read_parquet('{DOCS}/documents/pub_year=*/*.parquet', hive_partitioning=1)")
    cg = con.sql("""select def_id, any_value(cg) charge_group, any_value(ch) charge from (
                     select def_id_a def_id, charge_group_a cg, charge_a ch from edges
                     union all select def_id_b, charge_group_b, charge_b from edges) group by 1""").df()
    df = con.sql("""select p.def_id, p.doc_id, p.position_sys sys, coalesce(p.position_level, '不明') as "level",
                           coalesce(p.position_rank, '不明') as "rank", p.position_sys_rule as "rule", p.position_level_source lsrc,
                           p.position_region_code pos_region, d.sign_year, d.prov_code, d.city_code, d.region_code doc_region, d.proc_level
                    from positions p join documents d using(doc_id)
                    where p.is_offense_time and p.position_sys is not null""").df()
    df = df.merge(cg, on="def_id", how="left")
    df["charge_group"] = df["charge_group"].fillna("未知")
    return df


def universe(df, years=(2014, 2017), max_doc=None, proc_levels=None, drop_charge_firms=False, node="sys_level"):
    d = df.copy()
    if years:
        d = d[(d.sign_year >= years[0]) & (d.sign_year <= years[1])]
    if proc_levels:
        d = d[d.proc_level.isin(proc_levels)]
    if drop_charge_firms:
        d = d[~d.rule.fillna("").str.contains("charge:")]
    if node == "sys_level":
        d["node"] = np.where(d.sys == "无公职", "无公职", d.sys + "|" + d.level)
        # merge small cells into the system's 不明 cell
        cnt = d.node.value_counts()
        small = {k for k, v in cnt.items() if v < MIN_CELL and "|" in k}
        d.loc[d.node.isin(small), "node"] = d.loc[d.node.isin(small), "sys"] + "|不明"
    elif node == "sys_level_rank":
        d["node"] = np.where(d.sys == "无公职", "无公职", d.sys + "|" + d.level + "|" + d["rank"])
        cnt = d.node.value_counts()
        small = {k for k, v in cnt.items() if v < MIN_CELL and "|" in k}
        d.loc[d.node.isin(small), "node"] = d.loc[d.node.isin(small), "sys"] + "|" + d.loc[d.node.isin(small), "level"] + "|不明"
        cnt = d.node.value_counts()
        small = {k for k, v in cnt.items() if v < MIN_CELL and "|" in k}
        d.loc[d.node.isin(small), "node"] = d.loc[d.node.isin(small), "sys"] + "|不明|不明"
    elif node == "sys":
        d["node"] = d.sys
    elif node == "level":
        d["node"] = np.where(d.sys == "无公职", "无公职", d.level)
    n_in_doc = d.groupby("doc_id").def_id.transform("count")
    d = d[n_in_doc >= 2]
    if max_doc:
        n_in_doc = d.groupby("doc_id").def_id.transform("count")
        d = d[n_in_doc < max_doc]
    # DuckDB returns rows in a run-dependent order; fix it so the permutation draws (and hence the z-scores) are reproducible
    return d.sort_values(["doc_id", "def_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- projection + null model
class Projection:
    def __init__(self, d, n_perm=1000, seed=20261010, betweenness=True, strata=None):
        """strata: optional integer array (one per row of d) defining the permutation blocks of the null
        model; default = signing year.  P2b passes year × province / city / office codes."""
        self.d = d
        self.nodes = sorted(d.node.unique(), key=lambda x: (SYS_ORDER.index(x.split("|")[0]) if x.split("|")[0] in SYS_ORDER else 99,
                                                             LEVEL_ORDER.index(x.split("|")[1]) if "|" in x and x.split("|")[1] in LEVEL_ORDER else 99, x))
        self.K = len(self.nodes)
        idx = {n: i for i, n in enumerate(self.nodes)}
        self.labels = d.node.map(idx).to_numpy()
        self.years = d.sign_year.to_numpy()
        self.strata = self.years if strata is None else np.asarray(strata)
        doc_codes, doc_uni = pd.factorize(d.doc_id)
        self.doc_of = doc_codes
        # within-document pairs
        I, J, Wt, Dc = [], [], [], []
        for doc, grp in d.groupby(doc_codes).indices.items():
            n = len(grp)
            w = 1.0 / (n - 1)
            for a, b in combinations(grp, 2):
                I.append(a); J.append(b); Wt.append(w); Dc.append(doc)
        self.I, self.J, self.W, self.Dc = np.array(I), np.array(J), np.array(Wt), np.array(Dc)
        self.n_docs = len(doc_uni)
        self.M_obs = self._matrix(self.labels)
        self.docs_obs = self._doc_matrix(self.labels)
        rng = np.random.default_rng(seed)
        self.perm_M = np.zeros((n_perm, self.K, self.K))
        self.perm_deg = np.zeros((n_perm, self.K))
        self.perm_btw = np.zeros((n_perm, self.K))
        groups = self.groups = [np.where(self.strata == y)[0] for y in np.unique(self.strata)]
        for p in range(n_perm):
            lab = self.labels.copy()
            for g in groups:
                lab[g] = lab[rng.permutation(g)]
            M = self._matrix(lab)
            self.perm_M[p] = M
            self.perm_deg[p] = self._degree(M)
            if betweenness:
                self.perm_btw[p] = self._betweenness(M)
        self.n_perm = n_perm

    def _matrix(self, lab):
        a, b = lab[self.I], lab[self.J]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        M = np.zeros((self.K, self.K))
        np.add.at(M, (lo, hi), self.W)
        return M + np.triu(M, 1).T          # symmetric, diagonal = self-loop weight

    def _doc_matrix(self, lab):
        a, b = lab[self.I], lab[self.J]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        key = pd.DataFrame({"d": self.Dc, "lo": lo, "hi": hi}).drop_duplicates()
        M = np.zeros((self.K, self.K))
        np.add.at(M, (key.lo.to_numpy(), key.hi.to_numpy()), 1)
        return M + np.triu(M, 1).T

    def _degree(self, M):
        return M.sum(axis=1) - np.diag(M)

    def _graph(self, M):
        G = nx.Graph()
        G.add_nodes_from(range(self.K))
        for i in range(self.K):
            for j in range(i + 1, self.K):
                if M[i, j] > 0:
                    G.add_edge(i, j, weight=M[i, j], dist=1.0 / M[i, j])
        return G

    def _betweenness(self, M):
        b = nx.betweenness_centrality(self._graph(M), weight="dist", normalized=True)
        return np.array([b[i] for i in range(self.K)])

    # ---- tables
    def node_table(self):
        M = self.M_obs
        deg = self._degree(M)
        btw = self._betweenness(M)
        G = self._graph(M)
        try:
            eig = nx.eigenvector_centrality(G, weight="weight", max_iter=2000)
            eig = np.array([eig[i] for i in range(self.K)])
        except Exception:
            eig = np.full(self.K, np.nan)
        mu, sd = self.perm_deg.mean(0), self.perm_deg.std(0)
        bmu, bsd = self.perm_btw.mean(0), self.perm_btw.std(0)
        sizes = self.d.node.value_counts()
        ndocs = self.d.groupby("node").doc_id.nunique()
        sysn = [n.split("|")[0] for n in self.nodes]
        lvl = [n.split("|")[1] if "|" in n else "-" for n in self.nodes]
        sys_arr = np.array(sysn)
        cross_mask = sys_arr[:, None] != sys_arr[None, :]
        cross_obs = (M * cross_mask).sum(1)
        cross_perm = (self.perm_M * cross_mask[None]).sum(2)
        cmu, csd = cross_perm.mean(0), cross_perm.std(0)
        rows = []
        for i, n in enumerate(self.nodes):
            tot = deg[i]
            same_sys = sum(M[i, j] for j in range(self.K) if j != i and sysn[j] == sysn[i])
            same_lvl = sum(M[i, j] for j in range(self.K) if j != i and sysn[j] != sysn[i] and lvl[j] == lvl[i] and lvl[i] not in ("-", "不明"))
            cross = sum(M[i, j] for j in range(self.K) if j != i and sysn[j] != sysn[i] and (lvl[j] != lvl[i] or lvl[i] in ("-", "不明")))
            rows.append(dict(node=n, system=sysn[i], level=lvl[i], n_defendants=int(sizes.get(n, 0)), n_documents=int(ndocs.get(n, 0)),
                             self_weight=round(M[i, i], 2), self_share=round(M[i, i] / (M[i, i] + tot), 3) if (M[i, i] + tot) else np.nan,
                             cross_sys_w=round(cross_obs[i], 2), cross_sys_exp=round(cmu[i], 2),
                             cross_sys_ratio=round(cross_obs[i] / cmu[i], 2) if cmu[i] > 0 else np.nan,
                             cross_sys_z=round((cross_obs[i] - cmu[i]) / csd[i], 1) if csd[i] > 0 else np.nan,
                             degree_w=round(tot, 2), degree_null_mean=round(mu[i], 2), degree_z=round((tot - mu[i]) / sd[i], 2) if sd[i] > 0 else np.nan,
                             betweenness=round(btw[i], 4), betweenness_null_mean=round(bmu[i], 4), betweenness_z=round((btw[i] - bmu[i]) / bsd[i], 2) if bsd[i] > 0 else np.nan,
                             eigenvector=round(eig[i], 4), share_same_system=round(same_sys / tot, 3) if tot else np.nan,
                             share_same_level_other_system=round(same_lvl / tot, 3) if tot else np.nan, share_cross=round(cross / tot, 3) if tot else np.nan))
        return pd.DataFrame(rows)

    def pair_table(self):
        mu, sd = self.perm_M.mean(0), self.perm_M.std(0)
        rows = []
        for i in range(self.K):
            for j in range(i, self.K):
                o = self.M_obs[i, j]
                if o == 0 and mu[i, j] == 0:
                    continue
                rows.append(dict(node_a=self.nodes[i], node_b=self.nodes[j], weight=round(o, 2), n_documents=int(self.docs_obs[i, j]),
                                 null_mean=round(mu[i, j], 2), null_sd=round(sd[i, j], 3), z=round((o - mu[i, j]) / sd[i, j], 2) if sd[i, j] > 0 else np.nan,
                                 obs_over_exp=round(o / mu[i, j], 2) if mu[i, j] > 0 else np.nan, relation=self.relation(i, j)))
        return pd.DataFrame(rows).sort_values("weight", ascending=False)

    def relation(self, i, j):
        a, b = self.nodes[i], self.nodes[j]
        sa, sb = a.split("|")[0], b.split("|")[0]
        la = a.split("|")[1] if "|" in a else "-"
        lb = b.split("|")[1] if "|" in b else "-"
        if i == j:
            return "同条同块"
        if la in ("-", "不明") or lb in ("-", "不明"):
            return "同条" if sa == sb else "层级不明"
        if sa == sb:
            return "同条同块" if la == lb else "同条"
        return "同块" if la == lb else "跨"

    def tiaokuai(self, mask=None, label="all"):
        """Shares of 同条同块 / 同条 / 同块 / 跨 / 层级不明 for the pairs selected by mask (bool over pairs), observed vs null."""
        cats = ["同条同块", "同条", "同块", "跨", "层级不明"]
        rel = np.array([[cats.index(self.relation(i, j)) for j in range(self.K)] for i in range(self.K)])
        sel = np.ones(len(self.I), bool) if mask is None else mask
        a, b = self.labels[self.I[sel]], self.labels[self.J[sel]]
        w = self.W[sel]
        tot = w.sum()
        obs = np.array([w[rel[a, b] == c].sum() for c in range(5)]) / tot if tot else np.full(5, np.nan)
        # null: the same pairs with permuted labels — recompute from stored matrices is not pair-selective, so redo quickly
        rng = np.random.default_rng(1)
        groups = self.groups
        nulls = []
        for p in range(min(self.n_perm, 300)):
            lab = self.labels.copy()
            for g in groups:
                lab[g] = lab[rng.permutation(g)]
            aa, bb = lab[self.I[sel]], lab[self.J[sel]]
            nulls.append([w[rel[aa, bb] == c].sum() / tot for c in range(5)] if tot else [np.nan] * 5)
        nulls = np.array(nulls)
        mu, sd = nulls.mean(0), nulls.std(0)
        rows = []
        for c, name in enumerate(cats):
            rows.append(dict(subset=label, relation=name, share_obs=round(obs[c], 3), share_null=round(mu[c], 3),
                             z=round((obs[c] - mu[c]) / sd[c], 1) if sd[c] > 0 else np.nan, weight=round(w[rel[a, b] == c].sum(), 1), n_pairs=int((rel[a, b] == c).sum())))
        return pd.DataFrame(rows)

    def pair_mask(self, col, value):
        v = self.d[col].to_numpy()
        return (v[self.I] == value) | (v[self.J] == value)

    def pair_mask_both(self, col, value):
        v = self.d[col].to_numpy()
        return (v[self.I] == value) & (v[self.J] == value)

    def doc_mask(self, col, value):
        v = self.d[col].to_numpy()
        return v[self.I] == value


# --------------------------------------------------------------------------- figures
def set_font():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager as fm, rcParams
    names = {f.name for f in fm.fontManager.ttflist}
    for cand in ("Hiragino Sans GB", "PingFang SC", "Arial Unicode MS", "STHeiti", "Noto Sans CJK SC"):
        if cand in names:
            rcParams["font.family"] = cand
            break
    rcParams["axes.unicode_minus"] = False


def fig_sys_heatmap(sysP, path):
    import matplotlib.pyplot as plt
    K, names = sysP.K, sysP.nodes
    obs, mu, sd = sysP.M_obs, sysP.perm_M.mean(0), sysP.perm_M.std(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.log2(obs / mu)
    z = (obs - mu) / np.where(sd > 0, sd, np.nan)
    fig, ax = plt.subplots(figsize=(8.6, 7.4))
    vmax = np.nanmax(np.abs(ratio[np.isfinite(ratio)]))
    vmax = min(vmax, 4)
    im = ax.imshow(ratio, cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(K)); ax.set_yticks(range(K))
    ax.set_xticklabels(names, rotation=45, ha="right"); ax.set_yticklabels(names)
    for i in range(K):
        for j in range(K):
            if obs[i, j] > 0 or mu[i, j] > 0:
                txt = f"{obs[i, j]:.0f}\n" + (f"z={z[i, j]:+.0f}" if np.isfinite(z[i, j]) else "")
                ax.text(j, i, txt, ha="center", va="center", fontsize=7, color="black" if abs(ratio[i, j]) < 2.5 else "white")
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.set_label("log2(观测 / 零模型期望)")
    ax.set_title("图1 系统 × 系统 同案共谋权重（2014–2017，案件归一权重；格内：观测权重 / z 分数）", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def fig_network(P, nodes_df, path, min_weight=8.0, min_z=3.0):
    """条块 grid: columns = systems (条), rows = levels (块). Vertical edges are 同条, horizontal 同块, diagonal 跨."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    M = P.M_obs
    mu, sd = P.perm_M.mean(0), P.perm_M.std(0)
    colors = dict(zip(SYS_ORDER, ["#c0392b", "#2980b9", "#8e44ad", "#16a085", "#d35400", "#27ae60", "#f39c12", "#7f8c8d", "#e67e22", "#95a5a6"]))
    rows = [l for l in LEVEL_ORDER]            # 省 … 村社区, 不明
    def xy(n):
        sy = n.split("|")[0]
        lv = n.split("|")[1] if "|" in n else "不明"
        return SYS_ORDER.index(sy), len(rows) - 1 - rows.index(lv)
    size = nodes_df.set_index("node").n_defendants
    selfsh = nodes_df.set_index("node").self_share
    fig, ax = plt.subplots(figsize=(15, 9.5))
    # edges
    drawn = []
    for i in range(P.K):
        for j in range(i + 1, P.K):
            w = M[i, j]
            if w <= 0:
                continue
            z = (w - mu[i, j]) / sd[i, j] if sd[i, j] > 0 else 0
            if w < min_weight and z < min_z:
                continue
            x1, y1 = xy(P.nodes[i]); x2, y2 = xy(P.nodes[j])
            col = "#c0392b" if z >= min_z else ("#2980b9" if z <= -min_z else "#999999")
            ax.plot([x1, x2], [y1, y2], color=col, lw=0.5 + 1.6 * np.sqrt(w / 10), alpha=0.75 if col != "#999999" else 0.45, zorder=1,
                    solid_capstyle="round")
            drawn.append((P.nodes[i], P.nodes[j], w, z))
    # nodes
    for n in P.nodes:
        x, y = xy(n)
        ns = size.get(n, 1)
        ax.scatter([x], [y], s=40 + 9 * np.sqrt(ns) * 2.2, color=colors.get(n.split("|")[0], "#999"), zorder=3, edgecolors="white", linewidths=0.8)
        ax.text(x, y - 0.33, f"n={ns}\n自{selfsh.get(n, float('nan')) * 100:.0f}%", ha="center", va="top", fontsize=7, zorder=4)
    ax.set_xticks(range(len(SYS_ORDER))); ax.set_xticklabels(SYS_ORDER, fontsize=10)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(list(reversed(rows)), fontsize=10)
    ax.set_xlim(-0.6, len(SYS_ORDER) - 0.4); ax.set_ylim(-0.9, len(rows) - 0.4)
    ax.set_xlabel("条（系统）"); ax.set_ylabel("块（层级）")
    ax.grid(True, color="#eeeeee", zorder=0)
    ax.set_title(f"图2 条块位置网络（2014–2017，{P.n_docs:,} 份多被告文书）。节点大小 = 被告人数；“自” = 与同一位置类型共案的权重份额。\n"
                 f"边：竖线 = 同条（同系统跨层级），横线 = 同块（同层级跨系统），斜线 = 跨。红 = 高于零模型 (z ≥ {min_z:.0f})，蓝 = 低于 (z ≤ −{min_z:.0f})，灰 = 不显著；只画权重 ≥ {min_weight:.0f} 或 z ≥ {min_z:.0f} 的边，线宽 ∝ √权重",
                 fontsize=9.5, loc="left")
    handles = [Line2D([0], [0], color="#c0392b", lw=2, label="高于零模型"), Line2D([0], [0], color="#2980b9", lw=2, label="低于零模型"),
               Line2D([0], [0], color="#999999", lw=2, label="不显著")]
    ax.legend(handles=handles, loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return pd.DataFrame(drawn, columns=["node_a", "node_b", "weight", "z"])


def fig_tiaokuai(tk, path):
    import matplotlib.pyplot as plt
    sub = tk[tk.subset.str.startswith("level=")].copy()
    sub["level"] = sub.subset.str.replace("level=", "")
    cats = ["同条同块", "同条", "同块", "跨"]
    levels = [l for l in LEVEL_ORDER if l in set(sub.level) and l != "不明"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    for ax, col, title in ((axes[0], "share_obs", "观测"), (axes[1], "share_null", "零模型")):
        bottom = np.zeros(len(levels))
        for c, color in zip(cats, ["#7f8c8d", "#2980b9", "#27ae60", "#c0392b"]):
            vals = np.array([sub[(sub.level == l) & (sub.relation == c)][col].iloc[0] if len(sub[(sub.level == l) & (sub.relation == c)]) else 0 for l in levels])
            ax.bar(levels, vals, bottom=bottom, label=c, color=color)
            bottom += vals
        ax.set_title(f"{title}：按被告人所在层级的边分布（排除层级不明）")
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("边权重份额")
    axes[1].legend(loc="upper right", fontsize=8)
    fig.suptitle("图3 同条 / 同块 / 跨 的份额 —— 观测 vs 零模型（2014–2017）", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def fig_degree_z(nodes_df, path):
    import matplotlib.pyplot as plt
    df = nodes_df[nodes_df.n_defendants >= MIN_CELL].sort_values("degree_z")
    colors = dict(zip(SYS_ORDER, ["#c0392b", "#2980b9", "#8e44ad", "#16a085", "#d35400", "#27ae60", "#f39c12", "#7f8c8d", "#e67e22", "#95a5a6"]))
    fig, axes = plt.subplots(1, 2, figsize=(13, 0.28 * len(df) + 1.5))
    for ax, col, title in ((axes[0], "degree_z", "加权度 z 分数（与多少种其他位置共案，相对零模型）"),
                           (axes[1], "betweenness_z", "介数 z 分数（桥接不同位置，相对零模型）")):
        d2 = df.sort_values(col)
        ax.barh(d2.node, d2[col], color=[colors.get(s, "#999") for s in d2.system])
        ax.axvline(0, color="black", lw=0.6); ax.axvline(3, color="grey", lw=0.5, ls="--"); ax.axvline(-3, color="grey", lw=0.5, ls="--")
        ax.set_title(title, fontsize=9)
        ax.tick_params(axis="y", labelsize=7)
    fig.suptitle("图4 位置节点的中心性 z 分数（系统×层级，2014–2017）", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


# --------------------------------------------------------------------------- markdown tables
def conditional(tk_sub):
    """Shares among pairs that leave the cell with both levels known: 同条 / 同块 / 跨, observed vs null."""
    t = tk_sub.set_index("relation")
    o = np.array([t.loc[c, "share_obs"] for c in ("同条", "同块", "跨")]); e = np.array([t.loc[c, "share_null"] for c in ("同条", "同块", "跨")])
    o, e = o / o.sum(), e / e.sum()
    return o, e, o / e


def write_tables(nodes, pairs, pS, tk, rob, br, P, path):
    L = []
    L.append("# P2 结果表（自动生成，`scripts/p2_network.py`）\n")
    L.append(f"口径：2014–2017 年落款、至少两名已知位置被告人的职务犯罪文书 {P.n_docs:,} 份，被告人 {len(P.d):,}，节点（系统×层级）{P.K} 个；"
             f"边权 = 1/(n−1)（案件归一）；零模型 = 按年份内随机重排位置标签 {P.n_perm} 次。\n")
    # A. 条块 shares
    L.append("## 表A 边权重的条块分布：观测 vs 零模型\n")
    L.append("| 关系 | 含义 | 观测份额 | 零模型份额 | z | 条件份额（跨出本格且层级已知）观测 | 条件份额零模型 | 观测/零模型 |")
    L.append("|---|---|---|---|---|---|---|---|")
    t = tk[tk.subset == "all"].set_index("relation")
    o, e, r = conditional(tk[tk.subset == "all"])
    meaning = {"同条同块": "同系统同层级（同一类单位内部）", "同条": "同系统、不同层级（上下级）", "同块": "同层级、不同系统", "跨": "系统与层级都不同", "层级不明": "一端层级不明（含无公职）"}
    cond = {"同条": 0, "同块": 1, "跨": 2}
    for c in ("同条同块", "同条", "同块", "跨", "层级不明"):
        k = cond.get(c)
        L.append(f"| {c} | {meaning[c]} | {t.loc[c, 'share_obs']:.3f} | {t.loc[c, 'share_null']:.3f} | {t.loc[c, 'z']:+.0f} | "
                 + (f"{o[k]:.2f} | {e[k]:.2f} | {r[k]:.2f} |" if k is not None else "— | — | — |"))
    # B. by level / charge / year / court
    L.append("\n## 表B 分层：本格内份额与条件份额（条 : 块 : 跨）\n")
    L.append("| 子集 | 对数 | 同条同块份额 | 条件份额 条 | 块 | 跨 | 条 obs/exp | 块 obs/exp | 跨 obs/exp |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for sub in tk.subset.unique():
        if sub == "all":
            continue
        ts = tk[tk.subset == sub]
        if ts.set_index("relation").loc[["同条", "同块", "跨"], "share_null"].sum() == 0:
            continue
        o, e, r = conditional(ts)
        npairs = int(ts.n_pairs.sum())
        L.append(f"| {sub} | {npairs:,} | {ts.set_index('relation').loc['同条同块', 'share_obs']:.2f} | {o[0]:.2f} | {o[1]:.2f} | {o[2]:.2f} | {r[0]:.1f} | {r[1]:.1f} | {r[2]:.1f} |")
    # C. nodes
    L.append("\n## 表C 位置节点（被告人 ≥ 30）\n")
    L.append("自份额 = 与同一位置类型共案的权重 /（自 + 对外）；跨系统 obs/exp = 与其他系统共案的权重相对零模型；介数 z 为桥接指标。\n")
    L.append("| 节点 | 被告人 | 文书 | 自份额 | 对外加权度 | 跨系统 obs/exp | 跨系统 z | 介数 | 介数 z |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    nd = nodes[nodes.n_defendants >= MIN_CELL].sort_values("n_defendants", ascending=False)
    for r_ in nd.itertuples():
        L.append(f"| {r_.node} | {r_.n_defendants:,} | {r_.n_documents:,} | {r_.self_share:.2f} | {r_.degree_w:.0f} | {r_.cross_sys_ratio:.2f} | {r_.cross_sys_z:+.0f} | {r_.betweenness:.3f} | {r_.betweenness_z:+.1f} |")
    # D. system matrix
    L.append("\n## 表D 系统 × 系统（观测权重 / 零模型期望；括号 z）\n")
    K, names = pS.K, pS.nodes
    mu, sd = pS.perm_M.mean(0), pS.perm_M.std(0)
    L.append("| | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * K)
    for i in range(K):
        cells = []
        for j in range(K):
            o_ = pS.M_obs[i, j]
            if o_ == 0 and mu[i, j] == 0:
                cells.append("")
            else:
                z = (o_ - mu[i, j]) / sd[i, j] if sd[i, j] > 0 else float("nan")
                cells.append(f"{o_ / mu[i, j]:.2f} ({z:+.0f})" if mu[i, j] > 0 else f"{o_:.0f}")
        L.append(f"| {names[i]} | " + " | ".join(cells) + " |")
    # E. over / under represented pairs
    L.append("\n## 表E 显著高于零模型的跨格对（z ≥ 3，权重 ≥ 3，不含本格）\n")
    L.append("| 位置 A | 位置 B | 关系 | 观测权重 | 文书数 | 期望 | obs/exp | z |")
    L.append("|---|---|---|---|---|---|---|---|")
    pp = pairs[(pairs.node_a != pairs.node_b) & (pairs.z >= 3) & (pairs.weight >= 3)].sort_values("z", ascending=False)
    for r_ in pp.itertuples():
        L.append(f"| {r_.node_a} | {r_.node_b} | {r_.relation} | {r_.weight:.0f} | {r_.n_documents} | {r_.null_mean:.1f} | {r_.obs_over_exp:.2f} | {r_.z:+.1f} |")
    L.append("\n## 表F 最低于零模型的跨格对（z ≤ −8，期望 ≥ 20）\n")
    L.append("| 位置 A | 位置 B | 关系 | 观测权重 | 期望 | obs/exp | z |")
    L.append("|---|---|---|---|---|---|---|")
    pp = pairs[(pairs.node_a != pairs.node_b) & (pairs.z <= -8) & (pairs.null_mean >= 20)].sort_values("z")
    for r_ in pp.itertuples():
        L.append(f"| {r_.node_a} | {r_.node_b} | {r_.relation} | {r_.weight:.0f} | {r_.null_mean:.0f} | {r_.obs_over_exp:.2f} | {r_.z:+.0f} |")
    # G. robustness
    L.append("\n## 表G 稳健性\n")
    L.append("| 变体 | 被告人 | 文书 | 节点 | 同条同块份额 | 条 | 块 | 跨 | 介数 z 前三 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r_ in rob.itertuples():
        top3 = "; ".join(r_.top_betweenness_z.split("; ")[:3])
        L.append(f"| {r_.variant} | {r_.defendants:,} | {r_.documents:,} | {r_.nodes} | {r_.share_同条同块:.3f} | {r_.share_同条:.3f} | {r_.share_同块:.3f} | {r_.share_跨:.3f} | {top3} |")
    # H. bribery
    L.append("\n## 表H 同案行贿→受贿配对（2014–2017，仅 549 份两类被告同案的文书中的一部分）\n")
    L.append("| 行贿方系统 | 受贿方系统 | 配对数 | 文书数 |")
    L.append("|---|---|---|---|")
    for r_ in br.head(12).itertuples():
        L.append(f"| {r_.giver_system if isinstance(r_.giver_system, str) else '不明'} | {r_.taker_system if isinstance(r_.taker_system, str) else '不明'} | {r_.n_pairs} | {r_.n_documents} |")
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
    summary = {}

    # ---- main variant: sys × level, 2014–2017
    d = universe(df)
    P = Projection(d, n_perm=args.perm, seed=args.seed)
    nodes = P.node_table()
    pairs = P.pair_table()
    nodes.to_csv(OUT / "p2_nodes_sys_level.csv", index=False, encoding="utf-8-sig")
    pairs.to_csv(OUT / "p2_pairs_sys_level.csv", index=False, encoding="utf-8-sig")
    summary["main"] = dict(defendants=int(len(d)), documents=int(P.n_docs), nodes=int(P.K), pairs=int(len(P.I)), perm=args.perm)
    print(f"main: {len(d):,} defendants, {P.n_docs:,} documents, {P.K} nodes, {len(P.I):,} pairs ({time.time() - t0:.0f}s)")

    # 条块 shares: overall, by level (of either side), by charge group (both sides), by year, by court level
    tk = [P.tiaokuai(None, "all")]
    for lv in LEVEL_ORDER:
        if lv == "不明":
            continue
        m = P.pair_mask("level", lv)
        if m.sum() > 50:
            tk.append(P.tiaokuai(m, f"level={lv}"))
    for cgp in ["贿赂", "贪污挪用", "渎职", "其他"]:
        m = P.pair_mask_both("charge_group", cgp)
        if m.sum() > 50:
            tk.append(P.tiaokuai(m, f"charge={cgp}"))
    for y in (2014, 2015, 2016, 2017):
        tk.append(P.tiaokuai(P.doc_mask("sign_year", y), f"year={y}"))
    for pl in ("basic", "municipal"):
        m = P.doc_mask("proc_level", pl)
        if m.sum() > 50:
            tk.append(P.tiaokuai(m, f"court={pl}"))
    tk = pd.concat(tk, ignore_index=True)
    tk.to_csv(OUT / "p2_tiaokuai.csv", index=False, encoding="utf-8-sig")

    # ---- system-only and level-only projections
    dS = universe(df, node="sys")
    PS = Projection(dS, n_perm=args.perm, seed=args.seed)
    nS = PS.node_table(); nS.to_csv(OUT / "p2_nodes_sys.csv", index=False, encoding="utf-8-sig")
    pS = PS.pair_table(); pS.to_csv(OUT / "p2_sys_matrix.csv", index=False, encoding="utf-8-sig")
    dL = universe(df, node="level")
    PL = Projection(dL, n_perm=args.perm, seed=args.seed)
    nL = PL.node_table(); nL.to_csv(OUT / "p2_nodes_level.csv", index=False, encoding="utf-8-sig")
    pL = PL.pair_table(); pL.to_csv(OUT / "p2_level_matrix.csv", index=False, encoding="utf-8-sig")
    print(f"system / level projections done ({time.time() - t0:.0f}s)")

    # ---- robustness variants (fewer permutations)
    variants = {
        "main_2014_2017": dict(),
        "no_large_cases_lt8": dict(max_doc=8),
        "all_years": dict(years=None),
        "basic_courts_only": dict(proc_levels=["basic"]),
        "municipal_courts": dict(proc_levels=["municipal", "branch", "provincial"]),
        "no_charge_inferred_firms": dict(drop_charge_firms=True),
        "three_way_nodes": dict(node="sys_level_rank"),
    }
    rob = []
    for name, kw in variants.items():
        dv = universe(df, **kw)
        if len(dv) < 100:
            continue
        Pv = P if name == "main_2014_2017" else Projection(dv, n_perm=300, seed=args.seed)
        nv = Pv.node_table()
        nv_ok = nv[nv.n_defendants >= MIN_CELL]
        topdeg = nv_ok.sort_values("degree_z", ascending=False).head(6)
        topbtw = nv_ok.sort_values("betweenness_z", ascending=False).head(6)
        shares = Pv.tiaokuai(None, name).set_index("relation")
        rob.append(dict(variant=name, defendants=len(dv), documents=Pv.n_docs, nodes=Pv.K,
                        top_degree_z="; ".join(f"{r.node} ({r.degree_z:+.0f})" for r in topdeg.itertuples()),
                        top_betweenness_z="; ".join(f"{r.node} ({r.betweenness_z:+.1f})" for r in topbtw.itertuples()),
                        **{f"share_{c}": shares.loc[c, "share_obs"] for c in ("同条同块", "同条", "同块", "跨", "层级不明")},
                        **{f"z_{c}": shares.loc[c, "z"] for c in ("同条同块", "同条", "同块", "跨")}))
        if name == "three_way_nodes":
            nv.to_csv(OUT / "p2_nodes_sys_level_rank.csv", index=False, encoding="utf-8-sig")
        print(f"variant {name}: {len(dv):,} defendants ({time.time() - t0:.0f}s)")
    rob = pd.DataFrame(rob)
    rob.to_csv(OUT / "p2_robustness.csv", index=False, encoding="utf-8-sig")

    # ---- directed bribery pairs inside one document (2014–2017)
    br = con.sql("""select sys_a giver_system, sys_b taker_system, count(*) n_pairs, count(distinct doc_id) n_documents
                    from edges where directed and sign_year between 2014 and 2017 group by 1,2 order by 3 desc""").df()
    br.to_csv(OUT / "p2_bribery_directed.csv", index=False, encoding="utf-8-sig")

    # ---- figures
    fig_sys_heatmap(PS, OUT / "fig1_sys_heatmap.png")
    fig_network(P, nodes, OUT / "fig2_network.png")
    fig_tiaokuai(tk, OUT / "fig3_tiaokuai.png")
    fig_degree_z(nodes, OUT / "fig4_degree_z.png")

    write_tables(nodes, pairs, PS, tk, rob, br, P, OUT / "p2_tables.md")
    summary["tiaokuai_all"] = tk[tk.subset == "all"].to_dict("records")
    summary["top_degree_z"] = nodes[nodes.n_defendants >= MIN_CELL].sort_values("degree_z", ascending=False).head(10)[["node", "n_defendants", "degree_w", "degree_z", "betweenness_z"]].to_dict("records")
    summary["top_betweenness_z"] = nodes[nodes.n_defendants >= MIN_CELL].sort_values("betweenness_z", ascending=False).head(10)[["node", "n_defendants", "betweenness", "betweenness_z"]].to_dict("records")
    summary["seconds"] = round(time.time() - t0, 1)
    json.dump(summary, open(LOG / "p2_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print(f"done in {time.time() - t0:.0f}s → {OUT}")


if __name__ == "__main__":
    main()
