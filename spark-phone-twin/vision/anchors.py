"""Known-position QR anchors: absolute fixes for phone localization.

Geometry-only tracking was measured diverging after ~50 frames of a walk and
never recovering, with confidence still reading 0.99 while the heading was 180°
wrong. The building's repeated 6.4 m structural bay and long parallel corridors
make that failure mode intrinsic, not a tuning problem.

An anchor removes the ambiguity instead of fighting it. A phone already scans a
QR to join, so if that QR is at a surveyed spot the join itself is an absolute
fix — no extra user action. Two properties matter:

  position  the anchor's (x, y, level), known to whatever accuracy it was placed
  heading   a wall-mounted QR is read while facing the wall, so the reader's
            bearing is the direction from the anchor into that wall

Anchors also re-anchor mid-walk: scanning any anchor resets accumulated drift to
zero, which is the difference between a demo that holds up over a long walk and
one that quietly points backwards.

  python anchors.py --list
  python anchors.py --add --room level2_room_09 --label "2F Lobby"
  python anchors.py --sheet          # printable QR sheet for every anchor
"""
import argparse, json, os
import numpy as np

PLANS = os.path.expanduser("~/plans")
ANCHORS = os.path.join(PLANS, "anchors.json")
F2F = 15 * 0.3048 + 4 * 0.0254 + 0.60


def load():
    if os.path.exists(ANCHORS):
        try:
            return json.load(open(ANCHORS))
        except Exception:
            pass
    return {"anchors": []}


def save(d):
    tmp = ANCHORS + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1)
    os.replace(tmp, ANCHORS)


def _rooms(level):
    for name in ("%s_rooms_v2_aligned.json", "%s_rooms_v2.json", "%s_rooms.json"):
        p = os.path.join(PLANS, name % level)
        if os.path.exists(p):
            return json.load(open(p)), p
    return [], None


def _walls(level):
    for name in ("%s_walls_m_aligned.npy", "%s_walls_m.npy"):
        p = os.path.join(PLANS, name % level)
        if os.path.exists(p):
            return np.load(p)
    return np.zeros((0, 4))


def facing_nearest_wall(x, y, walls):
    """Bearing from a point toward the nearest wall — where a reader would look.

    A QR is mounted on a wall and read face-on, so the reader's heading is the
    direction from the anchor into that wall.
    """
    if len(walls) == 0:
        return 0.0
    a, b = walls[:, 0:2], walls[:, 2:4]
    ab = b - a
    L2 = np.maximum((ab * ab).sum(1), 1e-9)
    ap = np.array([x, y], np.float64)[None, :] - a
    t = np.clip((ap * ab).sum(1) / L2, 0.0, 1.0)
    proj = a + t[:, None] * ab
    d = np.linalg.norm(np.array([x, y])[None, :] - proj, axis=1)
    i = int(np.argmin(d))
    v = proj[i] - np.array([x, y])
    return float(np.degrees(np.arctan2(v[1], v[0])))


def interior_point(poly, walls):
    """Most-open spot in a room, respecting interior walls (see render_room_depth)."""
    import cv2
    P = np.asarray(poly, np.float64)
    x0, y0 = P[:, 0].min(), P[:, 1].min()
    x1, y1 = P[:, 0].max(), P[:, 1].max()
    res = max(x1 - x0, y1 - y0) / 200.0
    w = max(8, int((x1 - x0) / res) + 4)
    h = max(8, int((y1 - y0) / res) + 4)
    m = np.zeros((h, w), np.uint8)
    q = np.stack([(P[:, 0] - x0) / res + 2, (P[:, 1] - y0) / res + 2], -1).astype(np.int32)
    cv2.fillPoly(m, [q], 1)
    if len(walls):
        for sx, sy, ex, ey in walls:
            if not (x0 - 1 <= sx <= x1 + 1 or x0 - 1 <= ex <= x1 + 1):
                continue
            cv2.line(m, (int((sx - x0) / res + 2), int((sy - y0) / res + 2)),
                     (int((ex - x0) / res + 2), int((ey - y0) / res + 2)), 0, 2)
    dt = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    iy, ix = np.unravel_index(int(np.argmax(dt)), dt.shape)
    return float((ix - 2) * res + x0), float((iy - 2) * res + y0)


def add(room_id, label, base_url):
    level = room_id.rsplit("_room_", 1)[0]
    idx = int(room_id.rsplit("_", 1)[1])
    rooms, _ = _rooms(level)
    if idx >= len(rooms):
        raise SystemExit("no such room: %s" % room_id)
    walls = _walls(level)
    x, y = interior_point(rooms[idx]["poly"], walls)
    hd = facing_nearest_wall(x, y, walls)
    d = load()
    aid = "a%02d" % (len(d["anchors"]) + 1)
    d["anchors"].append(dict(
        id=aid, label=label or room_id, room_id=room_id, level=level,
        x=round(x, 2), y=round(y, 2), z=round(0.0 if level == "level1" else F2F, 2),
        heading_deg=round(hd, 1),
        url="%s?a=%s" % (base_url.rstrip("/") + "/", aid),
        placement="mount at eye height on the nearest wall, facing into the room"))
    save(d)
    print("added %s  %s  at (%.2f, %.2f) %s  heading %.1f deg"
          % (aid, label or room_id, x, y, level, hd))
    return aid


def sheet(out=None):
    """Printable sheet: one QR per anchor with its label and coordinates."""
    import cv2
    try:
        import qrcode
    except ImportError:
        raise SystemExit("qrcode not installed: pip install qrcode[pil]")
    d = load()
    if not d["anchors"]:
        raise SystemExit("no anchors yet — add some first")
    out = out or os.path.join(PLANS, "anchor_sheet.png")
    cols = 2
    cell = 460
    rows = int(np.ceil(len(d["anchors"]) / cols))
    img = np.full((rows * cell, cols * cell, 3), 255, np.uint8)
    for i, a in enumerate(d["anchors"]):
        q = qrcode.make(a["url"]).convert("RGB")
        q = cv2.cvtColor(np.array(q), cv2.COLOR_RGB2BGR)
        q = cv2.resize(q, (300, 300), interpolation=cv2.INTER_NEAREST)
        r_, c_ = divmod(i, cols)
        oy, ox = r_ * cell, c_ * cell
        img[oy + 20:oy + 320, ox + 80:ox + 380] = q
        cv2.putText(img, "%s  %s" % (a["id"], a["label"][:22]), (ox + 40, oy + 350),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 2)
        cv2.putText(img, "%s  (%.1f, %.1f)  facing %.0f deg"
                    % (a["level"], a["x"], a["y"], a["heading_deg"]),
                    (ox + 40, oy + 375), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1)
        cv2.putText(img, a["placement"][:46], (ox + 40, oy + 398),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (140, 140, 140), 1)
        cv2.rectangle(img, (ox + 10, oy + 10), (ox + cell - 10, oy + cell - 20),
                      (200, 200, 200), 1)
    cv2.imwrite(out, img)
    print("wrote %s (%d anchors)" % (out, len(d["anchors"])))
    return out


def lookup(aid):
    for a in load()["anchors"]:
        if a["id"] == aid:
            return a
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--room", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--base-url", default="https://172.16.94.151:8099")
    a = ap.parse_args()

    if a.add:
        if not a.room:
            raise SystemExit("--add needs --room level2_room_09")
        add(a.room, a.label, a.base_url)
    elif a.sheet:
        sheet()
    else:
        d = load()
        if not d["anchors"]:
            print("no anchors defined")
        for x in d["anchors"]:
            print("%-4s %-22s %-8s (%7.2f, %7.2f)  facing %6.1f deg  %s"
                  % (x["id"], x["label"], x["level"], x["x"], x["y"],
                     x["heading_deg"], x["url"]))
