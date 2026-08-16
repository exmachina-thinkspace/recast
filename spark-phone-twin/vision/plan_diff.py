"""Compare observed geometry against the drawings — as-built vs as-drawn.

The plan is survey-accurate about what the architect drew. It is silent about
what was actually built, what got renovated, and what the extraction missed
(L1 currently yields 557 m2 of rooms inside a 2356 m2 plate, so a lot is
missing). Observed geometry from cameras can supply that, but only as evidence,
never as authority — monocular depth is noisy and scale drifts.

Three questions, each answered with a number rather than a verdict:

  wall_coverage   observed vertical surfaces WITHOUT a nearby plan wall
                  -> partitions added, or drawings out of date
  see_through     plan walls with NO observed surface where they should be
                  -> doors/openings, or demolished walls (also just occlusion,
                     so this is the weakest signal of the three)
  floor_coverage  observed floor area OUTSIDE every room polygon
                  -> rooms the extraction missed

Everything is reported with the observed-point count backing it, so a claim
resting on 40 points is visibly weaker than one resting on 4000. Nothing here
edits the plan; it produces candidates for a human to accept.
"""
import numpy as np
import cv2

CEIL = 9 * 0.3048 + 7 * 0.0254


def seg_distance(pts_xy, walls):
    """Min distance from each 2D point to any wall segment. (M,) in metres."""
    if len(walls) == 0 or len(pts_xy) == 0:
        return np.full(len(pts_xy), np.inf, np.float32)
    a = walls[:, 0:2].astype(np.float32)          # (N,2)
    b = walls[:, 2:4].astype(np.float32)
    ab = b - a
    L2 = np.maximum((ab * ab).sum(1), 1e-9)       # (N,)
    out = np.empty(len(pts_xy), np.float32)
    # chunk to bound memory: full (M,N) at 200k x 2000 would be 1.6 GB
    step = max(1, int(4_000_000 // max(len(walls), 1)))
    for i in range(0, len(pts_xy), step):
        p = pts_xy[i:i + step].astype(np.float32)             # (m,2)
        ap = p[:, None, :] - a[None, :, :]                    # (m,N,2)
        t = np.clip((ap * ab[None]).sum(2) / L2[None], 0.0, 1.0)
        proj = a[None] + t[..., None] * ab[None]              # (m,N,2)
        d = np.linalg.norm(p[:, None, :] - proj, axis=2)      # (m,N)
        out[i:i + step] = d.min(1)
    return out


def _poly_mask(rooms, x0, y0, res, w, h):
    m = np.zeros((h, w), np.uint8)
    for r in rooms:
        P = np.asarray(r["poly"], np.float32)
        q = np.stack([(P[:, 0] - x0) / res, (P[:, 1] - y0) / res], -1).astype(np.int32)
        cv2.fillPoly(m, [q], 1)
    return m


def compare(points, walls, rooms, base_z=0.0, res=0.25, wall_tol=0.45,
            min_pts=25):
    """Observed cloud vs plan. Returns a dict of findings with evidence counts."""
    out = dict(observed_points=int(len(points)))
    if points is None or len(points) < 200:
        out["error"] = "too few observed points (%d)" % len(points)
        return out

    z = points[:, 2] - base_z
    wall_band = points[(z > 0.9) & (z < CEIL - 0.3)]       # above furniture
    floor_band = points[(z > -0.3) & (z < 0.35)]
    out["wall_band_points"] = int(len(wall_band))
    out["floor_band_points"] = int(len(floor_band))

    # --- 1. observed structure with no drawn wall
    if len(wall_band):
        d = seg_distance(wall_band[:, :2], walls)
        unexplained = wall_band[d > wall_tol]
        out["wall_match_rate"] = float((d <= wall_tol).mean())
        out["unexplained_wall_points"] = int(len(unexplained))
        if len(unexplained) >= min_pts:
            out["unexplained_wall_area_m2"] = round(
                float(len(unexplained)) * res * res * 0.5, 1)   # rough facing area
            c = unexplained[:, :2]
            out["unexplained_extent"] = [round(float(c[:, 0].min()), 1),
                                         round(float(c[:, 1].min()), 1),
                                         round(float(c[:, 0].max()), 1),
                                         round(float(c[:, 1].max()), 1)]
    # --- 2. floor seen outside every drawn room
    if len(floor_band) and rooms:
        xs, ys = floor_band[:, 0], floor_band[:, 1]
        x0, x1 = float(xs.min()) - 1, float(xs.max()) + 1
        y0, y1 = float(ys.min()) - 1, float(ys.max()) + 1
        w = max(8, int((x1 - x0) / res)); h = max(8, int((y1 - y0) / res))
        rmask = _poly_mask(rooms, x0, y0, res, w, h)
        gx = np.clip(((xs - x0) / res).astype(np.int32), 0, w - 1)
        gy = np.clip(((ys - y0) / res).astype(np.int32), 0, h - 1)
        seen = np.zeros((h, w), np.uint8)
        seen[gy, gx] = 1
        inside = rmask[gy, gx].astype(bool)
        out["floor_inside_rooms_rate"] = float(inside.mean())
        gap = ((seen == 1) & (rmask == 0)).astype(np.uint8)
        gap = cv2.morphologyEx(gap, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(gap, 8)
        cand = []
        for i in range(1, n):
            a_m2 = float(stats[i, cv2.CC_STAT_AREA]) * res * res
            if a_m2 < 2.0:                      # ignore specks
                continue
            cand.append(dict(area_m2=round(a_m2, 1),
                             centroid=[round(float(cent[i][0] * res + x0), 1),
                                       round(float(cent[i][1] * res + y0), 1)],
                             cells=int(stats[i, cv2.CC_STAT_AREA])))
        cand.sort(key=lambda c: -c["area_m2"])
        out["uncovered_floor_m2"] = round(sum(c["area_m2"] for c in cand), 1)
        out["missing_room_candidates"] = cand[:12]

    # --- 3. drawn walls with nothing observed near them (weak: occlusion)
    if len(wall_band) and len(walls):
        mid = ((walls[:, 0:2] + walls[:, 2:4]) / 2.0).astype(np.float32)
        dd = seg_distance(mid, np.concatenate(
            [wall_band[:, :2], wall_band[:, :2]], 1))
        out["drawn_walls_unseen"] = int((dd > 1.0).sum())
        out["drawn_walls_total"] = int(len(walls))
        out["see_through_note"] = ("unseen walls are weak evidence — a wall is "
                                   "also 'unseen' when simply never looked at")
    return out


def summarise(rep):
    L = []
    L.append("observed points      : %s" % "{:,}".format(rep.get("observed_points", 0)))
    if "error" in rep:
        L.append("ERROR: %s" % rep["error"]); return "\n".join(L)
    if "wall_match_rate" in rep:
        L.append("observed walls matching plan : %.1f%% (%s pts in band)"
                 % (100 * rep["wall_match_rate"],
                    "{:,}".format(rep["wall_band_points"])))
    if rep.get("unexplained_wall_points", 0):
        L.append("structure NOT in drawings    : %s pts%s"
                 % ("{:,}".format(rep["unexplained_wall_points"]),
                    ", ~%.1f m2" % rep["unexplained_wall_area_m2"]
                    if "unexplained_wall_area_m2" in rep else ""))
    if "floor_inside_rooms_rate" in rep:
        L.append("observed floor inside rooms  : %.1f%%"
                 % (100 * rep["floor_inside_rooms_rate"]))
        L.append("floor seen outside any room  : %.1f m2" % rep.get("uncovered_floor_m2", 0))
        for c in rep.get("missing_room_candidates", [])[:5]:
            L.append("   candidate room %6.1f m2 at (%.1f, %.1f)"
                     % (c["area_m2"], c["centroid"][0], c["centroid"][1]))
    if "drawn_walls_unseen" in rep:
        L.append("drawn walls never observed   : %d / %d  (weak signal)"
                 % (rep["drawn_walls_unseen"], rep["drawn_walls_total"]))
    return "\n".join(L)
