"""Extract doors, windows, and cased openings from sheet A-002 (page index 1).

Doors: fit circular arcs to 'c' (cubic bezier) drawing items; a door swing is
a ~90deg arc whose radius equals the door leaf width, with the hinge (arc
center) and one endpoint (the closed-leaf jamb) lying on a wall line.

Windows / cased openings: cluster the long wall boundary lines into
collinear "runs" (one per wall face), find gaps between consecutive
segments along a run, pair up gaps that appear on BOTH faces of a wall at
matching spans (confirms a real break, not stray geometry), then
classify each paired gap as a window if thin lines spanning the gap and
parallel to the wall exist inside the wall cavity, else a cased opening.

Writes only ~/plans/openings.json and ~/plans/openings_check.png (new files).
Does not modify any existing *_walls_m*.npy or *_rooms_v2*.json.
"""
import os, json, math
import numpy as np
import pymupdf
import cv2

PDF = os.path.expanduser("~/plans/6986025-CN Approved Plans.pdf")
OUT = os.path.expanduser("~/plans")
FT_PER_PT = 1.0 / 6.75
M_PER_PT = FT_PER_PT * 0.3048

doc = pymupdf.open(PDF)
page = doc[1]
M = page.rotation_matrix
W, H = page.rect.width, page.rect.height

drawings = page.get_drawings()

# ---------------------------------------------------------------------------
# Reproduce extract_plan.py's split/origin logic exactly so our coordinates
# land in the same frame as level1_walls_m.npy / level2_walls_m_aligned.npy
# ---------------------------------------------------------------------------
all_lines = []
for obj in drawings:
    for it in obj["items"]:
        if it[0] == "l":
            a = it[1] * M
            b = it[2] * M
            all_lines.append((a.x, a.y, b.x, b.y))
all_lines = np.array(all_lines)
Lall = np.hypot(all_lines[:, 2] - all_lines[:, 0], all_lines[:, 3] - all_lines[:, 1])

wall_lines_raw = all_lines[Lall > 2.0]
wall_lines_raw = wall_lines_raw[wall_lines_raw[:, [0, 2]].max(1) < W * 0.64]

ymid = (wall_lines_raw[:, 1] + wall_lines_raw[:, 3]) / 2
hist, edges = np.histogram(ymid, bins=60, range=(0, H))
lo, hi = int(0.30 * 60), int(0.70 * 60)
gapbin = lo + int(np.argmin(hist[lo:hi]))
SPLIT = edges[gapbin]

origins = {}
for name, mask in (("level2", wall_lines_raw[:, [1, 3]].max(1) < SPLIT),
                    ("level1", wall_lines_raw[:, [1, 3]].min(1) >= SPLIT)):
    v = wall_lines_raw[mask]
    x0 = v[:, [0, 2]].min()
    y0 = v[:, [1, 3]].min()
    origins[name] = (x0, y0)

LEVEL2_ALIGN = (-8.802, -0.156)  # from level_alignment.json


def which_level(y_raw):
    return "level2" if y_raw < SPLIT else "level1"


def to_m(x_pt, y_pt, level):
    x0, y0 = origins[level]
    xm = (x_pt - x0) * M_PER_PT
    ym = -(y_pt - y0) * M_PER_PT
    if level == "level2":
        xm += LEVEL2_ALIGN[0]
        ym += LEVEL2_ALIGN[1]
    return xm, ym


# load existing wall geometry (meters, shared level1 datum) for cross-checks
walls_m = {
    "level1": np.load(os.path.join(OUT, "level1_walls_m.npy")),
    "level2": np.load(os.path.join(OUT, "level2_walls_m_aligned.npy")),
}


def point_seg_dist(px, py, segs):
    x0, y0, x1, y1 = segs[:, 0], segs[:, 1], segs[:, 2], segs[:, 3]
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy
    L2 = np.where(L2 == 0, 1e-9, L2)
    t = np.clip(((px - x0) * dx + (py - y0) * dy) / L2, 0, 1)
    cx, cy = x0 + t * dx, y0 + t * dy
    return np.hypot(px - cx, py - cy).min()


# ---------------------------------------------------------------------------
# DOORS: fit circular arcs to 'c' items
# ---------------------------------------------------------------------------
def fit_arc(p0, p1, p2, p3):
    t0 = np.array([p1.x - p0.x, p1.y - p0.y])
    t3 = np.array([p3.x - p2.x, p3.y - p2.y])
    n0, n3 = np.linalg.norm(t0), np.linalg.norm(t3)
    if n0 < 1e-6 or n3 < 1e-6:
        return None
    t0, t3 = t0 / n0, t3 / n3
    normal0 = np.array([-t0[1], t0[0]])
    normal1 = np.array([-t3[1], t3[0]])
    P0 = np.array([p0.x, p0.y])
    P3 = np.array([p3.x, p3.y])
    A = np.array([normal0, -normal1]).T
    b = P3 - P0
    if abs(np.linalg.det(A)) < 1e-9:
        return None
    s, u = np.linalg.solve(A, b)
    C = P0 + s * normal0
    R0, R3 = np.linalg.norm(P0 - C), np.linalg.norm(P3 - C)
    if R0 < 1e-6 or R3 < 1e-6:
        return None
    v0, v3 = (P0 - C) / R0, (P3 - C) / R3
    cosang = np.clip(np.dot(v0, v3), -1, 1)
    sweep = math.degrees(math.acos(cosang))
    return C, P0, P3, R0, R3, sweep


raw_doors = []
for obj in drawings:
    for it in obj["items"]:
        if it[0] != "c":
            continue
        _, p0, p1, p2, p3 = it
        p0r, p1r, p2r, p3r = p0 * M, p1 * M, p2 * M, p3 * M
        res = fit_arc(p0r, p1r, p2r, p3r)
        if res is None:
            continue
        C, P0, P3, R0, R3, sweep = res
        if C[0] >= W * 0.64:
            continue
        if abs(R0 - R3) > 0.15 * max(R0, R3):
            continue
        if not (60 <= sweep <= 120):
            continue
        R = (R0 + R3) / 2
        R_m = R * M_PER_PT
        if not (0.5 <= R_m <= 2.2):
            continue
        lvl = which_level(C[1])
        Cx, Cy = to_m(C[0], C[1], lvl)
        P0x, P0y = to_m(P0[0], P0[1], lvl)
        P3x, P3y = to_m(P3[0], P3[1], lvl)
        segs = walls_m[lvl]
        dC = point_seg_dist(Cx, Cy, segs)
        d0 = point_seg_dist(P0x, P0y, segs)
        d3 = point_seg_dist(P3x, P3y, segs)
        wall_pt, open_pt, d_wall = ((P0x, P0y), (P3x, P3y), d0) if d0 < d3 else ((P3x, P3y), (P0x, P0y), d3)
        if dC > 0.4 or d_wall > 0.4:
            continue  # hinge or far jamb not credibly on a wall
        cx_o, cy_o = (Cx + wall_pt[0]) / 2, (Cy + wall_pt[1]) / 2
        wall_ang = math.degrees(math.atan2(wall_pt[1] - Cy, wall_pt[0] - Cx)) % 180.0
        cross = (wall_pt[0] - Cx) * (open_pt[1] - Cy) - (wall_pt[1] - Cy) * (open_pt[0] - Cx)
        swing = "left" if cross > 0 else "right"
        conf = max(0.0, min(1.0, 1.0
                             - abs(R0 - R3) / max(R0, R3)
                             - abs(sweep - 90) / 90.0
                             - dC / 0.4 * 0.3
                             - d_wall / 0.4 * 0.3))
        raw_doors.append(dict(level=lvl, x=cx_o, y=cy_o, width_m=round(float(np.hypot(*(np.array(wall_pt) - [Cx, Cy]))), 3),
                               wall_angle_deg=round(wall_ang, 1), swing=swing, confidence=round(float(conf), 2),
                               hinge=(Cx, Cy)))

# de-dup: same path is sometimes stroked twice -> cluster by (level, x, y) within 0.35m
raw_doors.sort(key=lambda d: -d["confidence"])
doors = []
for d in raw_doors:
    dup = False
    for kept in doors:
        if kept["level"] == d["level"] and math.hypot(kept["x"] - d["x"], kept["y"] - d["y"]) < 0.35:
            dup = True
            break
    if not dup:
        doors.append(d)

print("door arc candidates (pre-dedup):", len(raw_doors), " after dedup:", len(doors))
for lvl in ("level1", "level2"):
    print(" ", lvl, sum(1 for d in doors if d["level"] == lvl))

# ---------------------------------------------------------------------------
# WINDOWS / CASED OPENINGS: cluster wall lines into runs, find gaps, pair
# gaps across the two faces of a wall, classify by presence of spanning
# lines inside the gap.
# ---------------------------------------------------------------------------
def cluster_lines(segs, angle_tol_deg=3.0, rho_tol=0.12):
    N = len(segs)
    ang = np.degrees(np.arctan2(segs[:, 3] - segs[:, 1], segs[:, 2] - segs[:, 0])) % 180.0
    nang = np.radians(ang + 90.0)
    rho = segs[:, 0] * np.cos(nang) + segs[:, 1] * np.sin(nang)
    used = np.zeros(N, bool)
    idxs = np.arange(N)
    clusters = []
    for i in range(N):
        if used[i]:
            continue
        da = np.minimum(np.abs(ang - ang[i]), 180 - np.abs(ang - ang[i]))
        dr = np.abs(rho - rho[i])
        sel = (da < angle_tol_deg) & (dr < rho_tol) & (~used)
        members = idxs[sel]
        used[members] = True
        clusters.append(members)
    return clusters, ang, rho


def run_gaps(segs, cluster_idx, ang, rho, min_gap=0.55, max_gap=3.6):
    if len(cluster_idx) < 2:
        return None
    mean_ang = math.radians(float(np.mean(ang[cluster_idx])))
    mean_rho = float(np.mean(rho[cluster_idx]))
    dirv = np.array([math.cos(mean_ang), math.sin(mean_ang)])
    nvec = np.array([math.cos(mean_ang + math.pi / 2), math.sin(mean_ang + math.pi / 2)])
    p_line0 = mean_rho * nvec
    ivals = []
    for i in cluster_idx:
        x0, y0, x1, y1 = segs[i]
        t0 = np.dot([x0, y0] - p_line0, dirv)
        t1 = np.dot([x1, y1] - p_line0, dirv)
        ivals.append((min(t0, t1), max(t0, t1)))
    ivals.sort()
    merged = [list(ivals[0])]
    for a, b in ivals[1:]:
        if a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps = []
    for (a0, b0), (a1, b1) in zip(merged, merged[1:]):
        g = a1 - b0
        if min_gap <= g <= max_gap:
            gaps.append((b0, a1))
    return dict(ang=mean_ang, rho=mean_rho, dirv=dirv, nvec=nvec, p_line0=p_line0, gaps=gaps)


results_by_level = {}
for lvl in ("level1", "level2"):
    segs = walls_m[lvl]
    clusters, ang, rho = cluster_lines(segs)
    runs = []
    for idx in clusters:
        r = run_gaps(segs, idx, ang, rho)
        if r and r["gaps"]:
            runs.append(r)

    # pair runs that are parallel & close together (wall thickness) with overlapping gap spans
    paired = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            A, B = runs[i], runs[j]
            da = min(abs(A["ang"] - B["ang"]), math.pi - abs(A["ang"] - B["ang"]))
            if math.degrees(da) > 4:
                continue
            drho = abs(A["rho"] - B["rho"])
            if not (0.04 <= drho <= 0.55):
                continue
            for ga in A["gaps"]:
                for gb in B["gaps"]:
                    lo_, hi_ = max(ga[0], gb[0]), min(ga[1], gb[1])
                    if hi_ <= lo_:
                        continue
                    overlap = (hi_ - lo_) / max(ga[1] - ga[0], gb[1] - gb[0])
                    if overlap > 0.55:
                        mid_t = (lo_ + hi_) / 2
                        mid_rho = (A["rho"] + B["rho"]) / 2
                        width = hi_ - lo_
                        pt = mid_rho * A["nvec"] + mid_t * A["dirv"]
                        wall_ang = math.degrees(A["ang"]) % 180.0
                        paired.append(dict(x=pt[0], y=pt[1], width_m=width, wall_angle_deg=wall_ang,
                                            t_lo=lo_, t_hi=hi_, dirv=A["dirv"], nvec=A["nvec"],
                                            rho_lo=min(A["rho"], B["rho"]), rho_hi=max(A["rho"], B["rho"])))
    # drop gaps explained by a perpendicular wall crossing near the gap (T/corner
    # junctions produce a small line-endpoint gap that is not a real opening):
    # flag only when a roughly-perpendicular wall segment has an ENDPOINT that
    # nearly coincides with one of the gap's two boundary corners.
    T_TOL, R_TOL = 0.12, 0.25
    filtered = []
    n_junction = 0
    for g in paired:
        dirv, nvec = g["dirv"], g["nvec"]
        x0, y0, x1, y1 = segs[:, 0], segs[:, 1], segs[:, 2], segs[:, 3]
        seg_ang = np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 180.0
        wall_ang_mod = g["wall_angle_deg"] % 180.0
        dang = np.minimum(np.abs(seg_ang - wall_ang_mod), 180 - np.abs(seg_ang - wall_ang_mod))
        perp = dang > 60
        is_junction = False
        for pend in ((x0, y0), (x1, y1)):
            px, py = pend
            t = (px - g["x"]) * dirv[0] + (py - g["y"]) * dirv[1] + (g["t_lo"] + g["t_hi"]) / 2
            r = px * nvec[0] + py * nvec[1]
            near_lo = perp & (np.abs(t - g["t_lo"]) < T_TOL) & (r > g["rho_lo"] - R_TOL) & (r < g["rho_hi"] + R_TOL)
            near_hi = perp & (np.abs(t - g["t_hi"]) < T_TOL) & (r > g["rho_lo"] - R_TOL) & (r < g["rho_hi"] + R_TOL)
            if near_lo.any() or near_hi.any():
                is_junction = True
                break
        if is_junction:
            n_junction += 1
        else:
            filtered.append(g)
    print(lvl, "  dropped as wall-junction:", n_junction)
    paired = filtered

    results_by_level[lvl] = paired
    print(lvl, "wall-line clusters:", len(clusters), " runs-with-gaps:", len(runs), " paired gaps:", len(paired))

# de-dup paired gaps (nearby duplicates from multiple face-pairings)
for lvl in results_by_level:
    lst = sorted(results_by_level[lvl], key=lambda d: -d["width_m"])
    kept = []
    for d in lst:
        if any(math.hypot(k["x"] - d["x"], k["y"] - d["y"]) < 0.4 and abs(k["width_m"] - d["width_m"]) < 0.5 for k in kept):
            continue
        kept.append(d)
    results_by_level[lvl] = kept
    print(lvl, "paired gaps after dedup:", len(kept))

# broader unfiltered short-line set (for spanning-line window signature), per level, in meters
short_lines_by_level = {"level1": [], "level2": []}
for x0, y0, x1, y1 in all_lines:
    lvl = which_level((y0 + y1) / 2)
    if max(x0, x1) >= W * 0.64:
        continue
    xm0, ym0 = to_m(x0, y0, lvl)
    xm1, ym1 = to_m(x1, y1, lvl)
    short_lines_by_level[lvl].append((xm0, ym0, xm1, ym1))
for lvl in short_lines_by_level:
    short_lines_by_level[lvl] = np.array(short_lines_by_level[lvl])

openings_out = []

for d in doors:
    openings_out.append(dict(kind="door", level=d["level"], x=round(d["x"], 3), y=round(d["y"], 3),
                              width_m=d["width_m"], height_m=2.03, wall_angle_deg=d["wall_angle_deg"],
                              swing=d["swing"], confidence=d["confidence"], source="pdf_arc"))

for lvl in ("level1", "level2"):
    door_pts = [(d["x"], d["y"]) for d in doors if d["level"] == lvl]
    short_segs = short_lines_by_level[lvl]
    for g in results_by_level[lvl]:
        # skip if this gap is already accounted for by a detected door
        if any(math.hypot(g["x"] - dx, g["y"] - dy) < max(g["width_m"] / 2 + 0.35, 0.6) for dx, dy in door_pts):
            continue
        dirv, nvec = g["dirv"], g["nvec"]
        span_len = 0.0
        n_span = 0
        if len(short_segs):
            mx0, my0, mx1, my1 = short_segs[:, 0], short_segs[:, 1], short_segs[:, 2], short_segs[:, 3]
            midx, midy = (mx0 + mx1) / 2, (my0 + my1) / 2
            t_mid = (midx - g["x"]) * dirv[0] + (midy - g["y"]) * dirv[1] + (g["t_lo"] + g["t_hi"]) / 2
            rho_mid = midx * nvec[0] + midy * nvec[1]
            seg_ang = np.degrees(np.arctan2(my1 - my0, mx1 - mx0)) % 180.0
            wall_ang_mod = g["wall_angle_deg"] % 180.0
            dang = np.minimum(np.abs(seg_ang - wall_ang_mod), 180 - np.abs(seg_ang - wall_ang_mod))
            seg_len = np.hypot(mx1 - mx0, my1 - my0)
            inside = ((t_mid > g["t_lo"] + 0.03) & (t_mid < g["t_hi"] - 0.03) &
                      (rho_mid > g["rho_lo"] - 0.03) & (rho_mid < g["rho_hi"] + 0.03) &
                      (dang < 12) & (seg_len > 0.4 * g["width_m"]))
            n_span = int(inside.sum())
        kind = "window" if n_span >= 1 else "opening"
        height = 1.2 if kind == "window" else 2.03
        conf = 0.55 if kind == "window" else 0.45
        openings_out.append(dict(kind=kind, level=lvl, x=round(float(g["x"]), 3), y=round(float(g["y"]), 3),
                                  width_m=round(float(g["width_m"]), 3), height_m=height,
                                  wall_angle_deg=round(float(g["wall_angle_deg"]), 1), swing="none",
                                  confidence=conf, source="pdf_gap_window" if kind == "window" else "pdf_gap"))

with open(os.path.join(OUT, "openings.json"), "w") as f:
    json.dump({"openings": openings_out}, f, indent=1)

from collections import Counter
c = Counter((o["level"], o["kind"]) for o in openings_out)
print("FINAL COUNTS:", dict(c))
print("total openings:", len(openings_out))
print("wrote", os.path.join(OUT, "openings.json"))
