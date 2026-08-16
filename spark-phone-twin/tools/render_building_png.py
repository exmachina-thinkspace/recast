"""Render the BUILDING view straight to a PNG, no window required.

The Spark's compositor redirects windows offscreen, so screen captures of the
live app come back black. This draws the identical geometry through the same
plan_render code into an image file, so the massing can actually be reviewed.

  python render_building_png.py [--out ~/plans/building_view.png] [--yaw 35]
"""
import argparse, json, os, sys
import numpy as np
import cv2

sys.path.insert(0, os.path.expanduser("~/arlo-vision"))
import plan_render

PLANS = os.path.expanduser("~/plans")
CEIL = 9 * 0.3048 + 7 * 0.0254
MAIN_CEIL = 15 * 0.3048 + 4 * 0.0254
F2F = MAIN_CEIL + 0.60


def rot(yaw, pitch):
    a, b = np.radians(yaw), np.radians(pitch)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]], np.float32)
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]], np.float32)
    return Rx @ Rz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="%s/building_view.png" % PLANS)
    ap.add_argument("--yaw", type=float, default=35.0)
    ap.add_argument("--pitch", type=float, default=62.0)
    ap.add_argument("--w", type=int, default=1600)
    ap.add_argument("--h", type=int, default=900)
    ap.add_argument("--explode", type=float, default=1.0,
                    help="multiply storey separation to show floors apart")
    a = ap.parse_args()

    LEV = {}
    for lv, bz in (("level1", 0.0), ("level2", F2F * a.explode)):
        p = "%s/%s_walls_m.npy" % (PLANS, lv)
        if not os.path.exists(p):
            continue
        w = np.load(p)
        q = plan_render.wall_quads(w, bz, CEIL)
        LEV[lv] = dict(walls=w, quads=q, base_z=bz,
                       cols=plan_render.quad_shade(
                           q, (150, 205, 240) if lv == "level1" else (235, 190, 130)))
        print("%s: %d segs -> %d quads" % (lv, len(w), len(q)))
    if not LEV:
        print("no plan data"); return 1

    allw = [d["walls"] for d in LEV.values()]
    bx0 = min(w[:, [0, 2]].min() for w in allw); bx1 = max(w[:, [0, 2]].max() for w in allw)
    by0 = min(w[:, [1, 3]].min() for w in allw); by1 = max(w[:, [1, 3]].max() for w in allw)
    SPAN = max(bx1 - bx0, by1 - by0)
    cen = np.array([(bx0 + bx1) / 2, (by0 + by1) / 2,
                    F2F * a.explode / 2], np.float32)
    print("extent %.1f x %.1f m, span %.1f m" % (bx1 - bx0, by1 - by0, SPAN))

    pane = np.full((a.h, a.w, 3), 16, np.uint8)
    R = rot(a.yaw, a.pitch)
    cam = np.array([0, 0, SPAN * 1.05], np.float32)
    f = a.w / 1.5

    drawn = 0
    for lv, L in LEV.items():
        drawn += plan_render.draw_walls(pane, L["quads"], L["cols"], R, cam, f,
                                        cen, a.w, a.h)
    print("drew %d wall quads" % drawn)

    cv2.rectangle(pane, (0, 0), (a.w, 44), (0, 0, 0), -1)
    cv2.putText(pane, "1700 Westlake Ave N  -  extruded plan geometry  (L1 blue / L2 orange)",
                (12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (240, 240, 240), 2)
    cv2.putText(pane, "%d wall surfaces, %.0f x %.0f m footprint, 2 storeys"
                % (drawn, bx1 - bx0, by1 - by0), (12, 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, (150, 150, 150), 1)
    cv2.imwrite(a.out, pane)
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
