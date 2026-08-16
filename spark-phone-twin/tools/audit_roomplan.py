"""Audit our twin against Apple RoomPlan's output schema.

RoomPlan's CapturedRoom is the bar: parametric walls, doors, windows and
openings with dimensions; floors; a ceiling height; and objects carrying a
category, 3D dimensions, an orientation and a confidence. This checks each of
those against what we actually produce, so "no gaps" is a measurement rather
than an opinion.

Each check reports PASS/FAIL with the evidence behind it. A check that cannot
find its data FAILS — absent proof is not proof.

  python audit_roomplan.py [--json ~/plans/roomplan_audit.json]
"""
import argparse, json, os, sys
import numpy as np

PLANS = os.path.expanduser("~/plans")
VISION = os.path.expanduser("~/arlo-vision")
sys.path.insert(0, VISION)

R = {}


def check(n, name, ok, detail):
    R[n] = dict(name=name, pass_=bool(ok), detail=str(detail)[:150])
    print("%-2s %-30s %s  %s" % (n, name, "PASS" if ok else "FAIL", detail[:90]))


def _load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="%s/roomplan_audit.json" % PLANS)
    a = ap.parse_args()

    sg = _load("%s/scenegraph.json" % PLANS) or {}
    objs = []
    for lvl in (sg.get("levels") or []):
        for room in lvl.get("rooms", []):
            for o in room.get("objects", []):
                o["_room"] = room.get("room_id")
                objs.append(o)

    # 1 walls as parametric surfaces with real dimensions
    nw = 0
    for lv in ("level1", "level2"):
        for cand in ("%s_walls_m_aligned.npy", "%s_walls_m_clean.npy", "%s_walls_m.npy"):
            p = "%s/%s" % (PLANS, cand % lv)
            if os.path.exists(p):
                nw += len(np.load(p)); break
    check(1, "walls (parametric)", nw > 500, "%d wall segments" % nw)

    # 2 walls separated from drafting annotation (grid/dimension lines)
    clean = [f for f in os.listdir(PLANS) if f.endswith("_walls_m_clean.npy")]
    meta = _load("%s/wall_filter.json" % PLANS)
    check(2, "annotation filtered", bool(clean) and bool(meta),
          "clean sets: %s%s" % (len(clean),
                                "" if not meta else ", removed %s" % meta.get("removed")))

    # 3 doors with dimensions
    op = _load("%s/openings.json" % PLANS) or {}
    doors = [o for o in op.get("openings", []) if o.get("kind") == "door"]
    check(3, "doors detected", len(doors) > 0 and all("width_m" in d for d in doors),
          "%d doors" % len(doors))

    # 4 windows with dimensions
    wins = [o for o in op.get("openings", []) if o.get("kind") == "window"]
    check(4, "windows detected", len(wins) > 0 and all("width_m" in w for w in wins),
          "%d windows" % len(wins))

    # 5 cased openings (no door leaf)
    opens = [o for o in op.get("openings", []) if o.get("kind") == "opening"]
    good_op = [o for o in opens if float(o.get("confidence") or 0) >= 0.6]
    check(5, "openings detected", len(good_op) >= 3,
          "%d openings, %d at conf>=0.6" % (len(opens), len(good_op)))

    # 6 floors
    nfloor = 0
    for lv in ("level1", "level2"):
        if os.path.exists("%s/%s_rooms_v2_aligned.json" % (PLANS, lv)) or \
           os.path.exists("%s/%s_rooms_v2.json" % (PLANS, lv)):
            nfloor += 1
    check(6, "floors / storeys", nfloor >= 2, "%d storeys with room polygons" % nfloor)

    # 7 per-room ceiling height, measured rather than assumed
    ch = _load("%s/ceiling_heights.json" % PLANS) or {}
    rooms_h = ch.get("rooms") or {}
    meas_h = [v for v in rooms_h.values() if v.get("source") != "spec"]
    check(7, "ceiling height per room", len(rooms_h) >= 1 and len(meas_h) >= 1,
          "%d rooms (%d measured, %d spec)"
          % (len(rooms_h), len(meas_h), len(rooms_h) - len(meas_h)))

    # 8 objects carry a category
    check(8, "object categories", len(objs) > 0 and all(o.get("cls") for o in objs),
          "%d objects" % len(objs))

    # 9 objects carry measured 3D dimensions
    sized = [o for o in objs if o.get("size_m")]
    frac_s = (len(sized) / len(objs)) if objs else 0.0
    check(9, "object dimensions", objs and frac_s >= 0.30,
          "%d/%d measured (%.0f%%, need 30%%)" % (len(sized), len(objs), 100 * frac_s))

    # 10 objects carry an orientation (RoomPlan boxes are oriented, not axis-aligned)
    orient = [o for o in objs if o.get("yaw_deg") is not None]
    frac_o = (len(orient) / len(objs)) if objs else 0.0
    check(10, "object orientation", objs and frac_o >= 0.30,
          "%d/%d with yaw (%.0f%%, need 30%%)" % (len(orient), len(objs), 100 * frac_o))

    # 11 objects carry a confidence
    conf = [o for o in objs if o.get("confidence") is not None]
    check(11, "object confidence", len(conf) > 0 and len(objs) > 0,
          "%d/%d with confidence" % (len(conf), len(objs)))

    # 12 room type labels — RoomPlan does not do this; we should not regress it
    typed = 0
    for lvl in (sg.get("levels") or []):
        typed += sum(1 for r in lvl.get("rooms", []) if r.get("inferred_type"))
    check(12, "room type inference", typed > 0, "%d rooms typed" % typed)

    # 13 USD export, the format RoomPlan emits
    exports = [f for f in ("building.usda", "building.usdc", "building.glb",
                           "building.ifc") if os.path.exists("%s/%s" % (PLANS, f))]
    check(13, "USD/3D export", "building.usda" in exports, ",".join(exports) or "none")

    npass = sum(1 for v in R.values() if v["pass_"])
    print("\n%d / %d checks pass" % (npass, len(R)))
    out = dict(generated=__import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
               passed=npass, total=len(R), gaps=[v["name"] for v in R.values()
                                                 if not v["pass_"]],
               checks={str(k): v for k, v in R.items()})
    json.dump(out, open(a.json, "w"), indent=1)
    print("wrote %s" % a.json)
    if out["gaps"]:
        print("GAPS: %s" % ", ".join(out["gaps"]))
    return 0 if npass == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
