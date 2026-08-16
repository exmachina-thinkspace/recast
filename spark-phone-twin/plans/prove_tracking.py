"""
Simulate a walking phone user through a real floor plan and measure how
well scan_match.localize() tracks the walk when each frame's estimate
seeds the next (the actual streaming-tracking regime), vs. the isolated
snapshot tests that were run before.

Ground truth path: real free-space Dijkstra route through the level2 plan
(so it is guaranteed not to cross walls), through several rooms + a
corridor, resampled at 1.2 m/s / 5 Hz.

Synthetic observation per pose: 68-deg HFOV raycast against the real wall
segments (nearest-hit => naturally handles occlusion / front-face-only),
capped at 8 m range, corrupted with 3cm Gaussian point noise, a +/-10%
per-frame range-scale error (monocular depth drift), and ~20% random ray
dropout (furniture/people occlusion).

Tracking: frame 0 gets a generous (but not exact) prior. Every later frame
is localized using ONLY the previous frame's estimate as prior -- ground
truth is never fed back in. This is the actual test of streaming tracking.
"""
import json
import sys
import time

import numpy as np
import shapely.geometry as geom
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

sys.path.insert(0, "/home/acer01/arlo-vision")
import scan_match as sm

PLANS = "/home/acer01/plans"
RNG = np.random.default_rng(20260815)

# ---------------------------------------------------------------- load plan
walls = np.load(f"{PLANS}/level2_walls_m_aligned.npy").astype(np.float64)
rooms_raw = json.load(open(f"{PLANS}/level2_rooms_v2_aligned.json"))
room_polys = []
for r in rooms_raw:
    p = geom.Polygon(r["poly"])
    if p.is_valid and p.area > 1:
        room_polys.append(p)
print(f"[plan] {len(walls)} wall segments, {len(room_polys)} room polygons")


def room_label(x, y):
    pt = geom.Point(x, y)
    for i, p in enumerate(room_polys):
        if p.contains(pt):
            return i
    return -1


def point_wall_clearance(pts, walls):
    """min distance from each 2D point to nearest wall segment."""
    p = pts[:, None, :]
    a = walls[None, :, 0:2]
    b = walls[None, :, 2:4]
    ab = b - a
    ab_len2 = (ab ** 2).sum(-1) + 1e-12
    t = np.clip(((p - a) * ab).sum(-1) / ab_len2, 0, 1)
    proj = a + t[..., None] * ab
    d = np.linalg.norm(p - proj, axis=-1)
    return d.min(axis=1)


def segments_cross_any_wall(p0s, p1s, walls, eps=1e-9):
    """vectorized: does path segment (p0->p1) cross any wall segment?
    Returns bool array, one per path segment."""
    hits = np.zeros(len(p0s), dtype=bool)
    for i in range(len(p0s)):
        o, d = p0s[i], p1s[i] - p0s[i]
        A = walls[:, 0:2]
        S = walls[:, 2:4] - A
        denom = d[0] * S[:, 1] - d[1] * S[:, 0]
        ok = np.abs(denom) > eps
        AO = A - o
        t = np.where(ok, (AO[:, 0] * S[:, 1] - AO[:, 1] * S[:, 0]) / np.where(ok, denom, 1), -1)
        u = np.where(ok, (AO[:, 0] * d[1] - AO[:, 1] * d[0]) / np.where(ok, denom, 1), -1)
        hits[i] = np.any(ok & (t > eps) & (t < 1 - eps) & (u >= 0) & (u <= 1))
    return hits


# ---------------------------------------------------------- freespace A*
RES = 0.15
CLEAR_M = 0.10  # doorway gaps in this vectorized plan are narrow; 0.22/0.15 seal them shut
xy = walls.reshape(-1, 2)
x0, y0 = xy.min(0) - 1.0
x1, y1 = xy.max(0) + 1.0
W = int(np.ceil((x1 - x0) / RES)) + 1
H = int(np.ceil((y1 - y0) / RES)) + 1
img = np.full((H, W), 255, np.uint8)
import cv2
for wx0, wy0, wx1, wy1 in walls:
    p0 = (int(round((wx0 - x0) / RES)), int(round((wy0 - y0) / RES)))
    p1 = (int(round((wx1 - x0) / RES)), int(round((wy1 - y0) / RES)))
    cv2.line(img, p0, p1, 0, thickness=1)
dt = cv2.distanceTransform(img, cv2.DIST_L2, 5).astype(np.float32) * RES
free = dt > CLEAR_M
print(f"[grid] {W}x{H} @ {RES}m, free cells {free.sum()}/{free.size}")


def world_to_grid(px, py):
    return int(round((py - y0) / RES)), int(round((px - x0) / RES))  # row, col


def grid_to_world(r, c):
    return x0 + c * RES, y0 + r * RES


def nearest_free(r, c):
    if free[r, c]:
        return r, c
    ys, xs = np.where(free)
    d2 = (ys - r) ** 2 + (xs - c) ** 2
    k = np.argmin(d2)
    return int(ys[k]), int(xs[k])


# build sparse 8-connected graph over free cells only
idx_map = -np.ones((H, W), dtype=np.int64)
free_rc = np.argwhere(free)
idx_map[free_rc[:, 0], free_rc[:, 1]] = np.arange(len(free_rc))
n_nodes = len(free_rc)
rows, cols, data = [], [], []
offsets = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
           (-1, -1, np.sqrt(2)), (-1, 1, np.sqrt(2)), (1, -1, np.sqrt(2)), (1, 1, np.sqrt(2))]
for dr, dc, w in offsets:
    rr = free_rc[:, 0] + dr
    cc = free_rc[:, 1] + dc
    valid = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
    rr_v, cc_v = rr[valid], cc[valid]
    src = np.arange(n_nodes)[valid]
    dst = idx_map[rr_v, cc_v]
    ok2 = dst >= 0
    rows.append(src[ok2]); cols.append(dst[ok2]); data.append(np.full(ok2.sum(), w * RES))
rows = np.concatenate(rows); cols = np.concatenate(cols); data = np.concatenate(data)
graph = coo_matrix((data, (rows, cols)), shape=(n_nodes, n_nodes)).tocsr()
print(f"[graph] {n_nodes} nodes, {graph.nnz} edges")


def astar_path(px0, py0, px1, py1):
    r0, c0 = world_to_grid(px0, py0)
    r1, c1 = world_to_grid(px1, py1)
    r0, c0 = nearest_free(r0, c0)
    r1, c1 = nearest_free(r1, c1)
    s0 = idx_map[r0, c0]
    s1 = idx_map[r1, c1]
    dist, pred = dijkstra(graph, indices=s0, return_predecessors=True)
    if not np.isfinite(dist[s1]):
        raise RuntimeError("no path found between waypoints")
    path_idx = [s1]
    cur = s1
    while cur != s0:
        cur = pred[cur]
        path_idx.append(cur)
    path_idx.reverse()
    pts = free_rc[path_idx]
    return np.array([grid_to_world(r, c) for r, c in pts])


# ------------------------------------------------------------ choose waypoints
# 3 rooms spread across the floor -> forces corridor travel + a direction change
WAYPOINTS = [(9.52, -6.27), (26.22, -12.28), (28.55, -6.40)]
print(f"[waypoints] {WAYPOINTS}  (rooms containing them: "
      f"{[room_label(*w) for w in WAYPOINTS]})")

full_path = [WAYPOINTS[0]]
for a, b in zip(WAYPOINTS[:-1], WAYPOINTS[1:]):
    leg = astar_path(*a, *b)
    full_path.extend(leg[1:].tolist())
full_path = np.array(full_path)
seg_len = np.hypot(np.diff(full_path[:, 0]), np.diff(full_path[:, 1]))
print(f"[route] {len(full_path)} grid waypoints, total length {seg_len.sum():.1f} m")

# -------------------------------------------------- resample at 1.2 m/s, 5 Hz
SPEED = 1.2
DT = 0.2  # 5 Hz
cum = np.concatenate([[0], np.cumsum(seg_len)])
total_len = cum[-1]
n_frames = int(total_len / (SPEED * DT)) + 1
n_frames = max(60, min(150, n_frames))
sample_dist = np.linspace(0, total_len, n_frames)
traj_xy = np.column_stack([
    np.interp(sample_dist, cum, full_path[:, 0]),
    np.interp(sample_dist, cum, full_path[:, 1]),
])

# heading = direction of travel, smoothed slightly
raw_dxdy = np.gradient(traj_xy, axis=0)
heading = np.degrees(np.arctan2(raw_dxdy[:, 1], raw_dxdy[:, 0]))
# light smoothing (3-tap) to avoid grid-jaggedness in heading while keeping real turns
k = np.array([0.25, 0.5, 0.25])
heading_unwrapped = np.unwrap(np.radians(heading))
heading_smooth = np.degrees(np.convolve(heading_unwrapped, k, mode="same"))
heading_smooth[0], heading_smooth[-1] = heading[0], heading[-1]

print(f"[traj] {n_frames} frames, {total_len:.1f} m, "
      f"duration {total_len / SPEED:.1f} s, "
      f"heading range {heading_smooth.min():.0f}..{heading_smooth.max():.0f} deg")

# ---------------------------------------------------- validate free-space
clear = point_wall_clearance(traj_xy, walls)
seg_hits = segments_cross_any_wall(traj_xy[:-1], traj_xy[1:], walls)
print(f"[validate] min clearance to nearest wall = {clear.min():.3f} m "
      f"(threshold used for planning = {CLEAR_M} m); "
      f"wall-crossing segments = {seg_hits.sum()}/{len(seg_hits)}")
assert seg_hits.sum() == 0, "ground-truth path crosses a wall!"

# turn magnitude check
dtheta = np.abs(((np.diff(heading_smooth) + 180) % 360) - 180)
print(f"[turns] max single-frame heading change = {dtheta.max():.1f} deg; "
      f"cumulative turning = {dtheta.sum():.1f} deg over path")

# ------------------------------------------------------------ raycast synth
HFOV = 68.0
N_RAYS = 90
RANGE_MAX = 8.0
NOISE_XY_STD = 0.03
SCALE_ERR = 0.10
DROPOUT_P = 0.20
ZBAND_LO, ZBAND_HI = 0.95, 2.55  # inside scan_match's wall-band filter
K_Z = 6  # vertical samples per surviving ray


def raycast_frame(px, py, theta_deg, rng):
    offs = np.linspace(-HFOV / 2, HFOV / 2, N_RAYS)
    angs = np.radians(theta_deg + offs)
    dirs = np.column_stack([np.cos(angs), np.sin(angs)])
    o = np.array([px, py])
    A = walls[:, 0:2]
    S = walls[:, 2:4] - A
    best_t = np.full(N_RAYS, np.inf)
    for i in range(N_RAYS):
        d = dirs[i]
        denom = d[0] * S[:, 1] - d[1] * S[:, 0]
        ok = np.abs(denom) > 1e-9
        AO = A - o
        denom_safe = np.where(ok, denom, 1.0)
        t = (AO[:, 0] * S[:, 1] - AO[:, 1] * S[:, 0]) / denom_safe
        u = (AO[:, 0] * d[1] - AO[:, 1] * d[0]) / denom_safe
        valid = ok & (t > 0.05) & (t <= RANGE_MAX) & (u >= 0) & (u <= 1)
        if valid.any():
            best_t[i] = t[valid].min()
    hit = np.isfinite(best_t)
    # occlusion dropout
    keep = hit & (rng.random(N_RAYS) > DROPOUT_P)
    if not keep.any():
        return np.zeros((0, 3))
    scale_err_frame = 1.0 + rng.uniform(-SCALE_ERR, SCALE_ERR)
    r_true = best_t[keep]
    r_noisy = r_true * scale_err_frame
    ang_keep = angs[keep]
    world_hit = o[None, :] + r_noisy[:, None] * np.column_stack([np.cos(ang_keep), np.sin(ang_keep)])
    world_hit += rng.normal(0, NOISE_XY_STD, world_hit.shape)
    # replicate each surviving ray at K_Z heights to build a plausible wall-band cloud
    world_hit_rep = np.repeat(world_hit, K_Z, axis=0)
    z = rng.uniform(ZBAND_LO, ZBAND_HI, len(world_hit_rep))
    R_true = sm._yaw_mat(theta_deg)
    local_xy = (world_hit_rep - o) @ R_true
    return np.column_stack([local_xy, z])


# ------------------------------------------------------------- tracking loop
records = []
prior = None
scale_errs_used = []
for i in range(n_frames):
    tx, ty, tth = traj_xy[i, 0], traj_xy[i, 1], heading_smooth[i]
    frng = np.random.default_rng(1000 + i)
    obs = raycast_frame(tx, ty, tth, frng)

    if i == 0:
        prior = dict(x=tx + frng.normal(0, 0.6), y=ty + frng.normal(0, 0.6),
                     theta_deg=tth + frng.normal(0, 8.0))

    t0 = time.time()
    res = sm.localize(obs, walls, base_z=0.0, prior=prior)
    elapsed_ms = (time.time() - t0) * 1000.0

    if res["x"] is not None:
        pos_err = float(np.hypot(res["x"] - tx, res["y"] - ty))
        th_err = float(abs(((res["theta_deg"] - tth + 180) % 360) - 180))
        prior = dict(x=res["x"], y=res["y"], theta_deg=res["theta_deg"])
        lost = False
    else:
        pos_err = float("nan")
        th_err = float("nan")
        lost = True
        # hold last prior (no ground-truth feedback) -- realistic recovery strategy

    rl_true = room_label(tx, ty)
    rl_est = room_label(res["x"], res["y"]) if res["x"] is not None else -2
    records.append(dict(
        i=i, tx=tx, ty=ty, tth=tth,
        ex=res["x"], ey=res["y"], eth=res["theta_deg"],
        pos_err=pos_err, th_err=th_err, conf=res["confidence"],
        n_obs=res["n_points"], elapsed_ms=elapsed_ms, lost=lost,
        rl_true=rl_true, rl_est=rl_est, score=res["score"],
    ))

np.save("/home/acer01/plans/_tracking_records.npy", np.array(records, dtype=object))
print(f"[track] completed {len(records)} frames")

import pickle
with open("/home/acer01/arlo-vision/_tracking_records.pkl", "wb") as f:
    pickle.dump(dict(records=records, walls=walls, traj_xy=traj_xy,
                      heading_smooth=heading_smooth, room_polys_wkt=[p.wkt for p in room_polys]), f)
print("[done] saved /home/acer01/arlo-vision/_tracking_records.pkl")
