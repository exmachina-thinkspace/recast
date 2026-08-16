"""Superimpose camera 3D reconstructions onto the building shell.

Known from data      : metric scale, floor plane (-> Z), dominant wall dir (-> yaw)
Placeholder (needs 4 correspondences per camera) : X-Y position on the floor
"""
import os, json
import numpy as np
import cv2

PLANS = os.path.expanduser("~/plans")
FRAMES = os.path.expanduser("~/arlo-frames")
CEIL = 9 * 0.3048 + 7 * 0.0254
F2F = CEIL + 0.60

# room -> (level, placeholder X,Y in building metres, extra yaw deg)
PLACE = {
    "common": ("level1", 0.35, 0.45, 0.0),
    "lobby":  ("level2", 0.40, 0.55, 0.0),
    "swhall": ("level2", 0.68, 0.30, 90.0),
}
OVR = os.path.join(PLANS, "camera_poses.json")
if os.path.exists(OVR):
    for k, v in json.load(open(OVR)).items():
        PLACE[k] = tuple(v)
    print("loaded pose overrides")


def load_ply(p):
    with open(p, "rb") as f:
        head = b""
        while b"end_header\n" not in head:
            head += f.read(1)
        nv = int([l for l in head.decode().splitlines()
                  if l.startswith("element vertex")][0].split()[-1])
        v = np.frombuffer(f.read(nv * 15), dtype=[
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("r", "u1"), ("g", "u1"), ("b", "u1")], count=nv)
    xyz = np.stack([v["x"], v["y"], v["z"]], -1).astype(np.float32)
    rgb = np.stack([v["r"], v["g"], v["b"]], -1).astype(np.uint8)
    return xyz, rgb


walls = {n: np.load("%s/%s_walls_m.npy" % (PLANS, n)) for n in ("level1", "level2")
         if os.path.exists("%s/%s_walls_m.npy" % (PLANS, n))}
bx0 = min(w[:, [0, 2]].min() for w in walls.values())
bx1 = max(w[:, [0, 2]].max() for w in walls.values())
by0 = min(w[:, [1, 3]].min() for w in walls.values())
by1 = max(w[:, [1, 3]].max() for w in walls.values())
print("building extent %.1f x %.1f m" % (bx1 - bx0, by1 - by0))

clouds = []
for room, (level, fx, fy, yaw_extra) in PLACE.items():
    p = "%s/%s.ply" % (FRAMES, room)
    if not os.path.exists(p):
        continue
    xyz, rgb = load_ply(p)
    k = max(1, len(xyz) // 90000)
    xyz, rgb = xyz[::k], rgb[::k]

    # camera frame: X right, Y up, Z = -depth  ->  building: X, Y ground, Z up
    P = np.stack([xyz[:, 0], -xyz[:, 2], xyz[:, 1]], -1)

    # floor = 2nd percentile of Z, drop to the level's slab
    floor_z = np.percentile(P[:, 2], 2)
    P[:, 2] -= floor_z
    # camera sits at the origin of its own reconstruction, so its height above
    # the floor is simply -floor_z
    print("   %-7s camera height above floor: %.2f m (%.1f ft)"
          % (room, -floor_z, -floor_z / 0.3048))
    keep = P[:, 2] < CEIL * 1.25                    # trim the fisheye fan above ceiling
    P, rgb = P[keep], rgb[keep]

    th = np.radians(yaw_extra)
    R = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]], np.float32)
    P = P @ R.T

    P[:, 0] += bx0 + fx * (bx1 - bx0) - P[:, 0].mean()
    P[:, 1] += by0 + fy * (by1 - by0) - P[:, 1].mean()
    P[:, 2] += 0.0 if level == "level1" else F2F
    clouds.append((room, level, P.astype(np.float32), rgb))
    print("%-7s %s  %d pts  z %.2f..%.2f" % (room, level, len(P), P[:, 2].min(), P[:, 2].max()))

# ---------- render ----------
OW, OH = 1600, 950
allp = np.vstack([c[2] for c in clouds]) if clouds else np.zeros((1, 3), np.float32)
C = np.array([(bx0 + bx1) / 2, (by0 + by1) / 2, F2F / 2], np.float32)
span = max(bx1 - bx0, by1 - by0) * 1.15


def render(yaw, pitch, path):
    ry, rx = np.radians(yaw), np.radians(pitch)
    Rz = np.array([[np.cos(ry), -np.sin(ry), 0], [np.sin(ry), np.cos(ry), 0], [0, 0, 1]], np.float32)
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]], np.float32)
    R = Rx @ Rz
    cam = np.array([0, 0, span], np.float32)
    f = OW / 1.45
    img = np.full((OH, OW, 3), 16, np.uint8)
    zbuf = np.full((OH, OW), 1e9, np.float32)

    def T(P):
        return (P - C) @ R.T + cam

    # camera clouds first, z-buffered
    for room, level, P, rgb in clouds:
        q = T(P)
        m = q[:, 2] > 0.2
        q, c = q[m], rgb[m]
        u = (f * q[:, 0] / q[:, 2] + OW / 2).astype(np.int32)
        v = (OH / 2 - f * q[:, 1] / q[:, 2]).astype(np.int32)
        ok = (u >= 0) & (u < OW) & (v >= 0) & (v < OH)
        u, v, c, z = u[ok], v[ok], c[ok], q[ok, 2]
        o = np.argsort(-z)
        img[v[o], u[o]] = c[o][:, ::-1]
        zbuf[v[o], u[o]] = z[o]

    # wall wireframe over the top
    for name, w in walls.items():
        z0 = 0.0 if name == "level1" else F2F
        col = (255, 210, 140) if name == "level2" else (150, 220, 255)
        for x0, y0, x1, y1 in w[::2]:
            for zz in (z0, z0 + CEIL):
                a = T(np.array([[x0, y0, zz]], np.float32))[0]
                b = T(np.array([[x1, y1, zz]], np.float32))[0]
                if a[2] > 0.2 and b[2] > 0.2:
                    pa = (int(f * a[0] / a[2] + OW / 2), int(OH / 2 - f * a[1] / a[2]))
                    pb = (int(f * b[0] / b[2] + OW / 2), int(OH / 2 - f * b[1] / b[2]))
                    cv2.line(img, pa, pb, col, 1, cv2.LINE_AA)

    cv2.putText(img, "Lake Union Building - camera reconstructions in building frame",
                (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (240, 240, 240), 2)
    cv2.putText(img, "X-Y placement is PLACEHOLDER pending 4 correspondences/camera",
                (16, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 170, 255), 1)
    cv2.imwrite(path, img)
    return path


ts = [render(y, p, "%s/sup_%d.png" % (PLANS, i))
      for i, (y, p) in enumerate([(30, 55), (0, 88), (95, 40), (215, 50)])]
g = np.vstack([np.hstack([cv2.imread(ts[0]), cv2.imread(ts[1])]),
               np.hstack([cv2.imread(ts[2]), cv2.imread(ts[3])])])
cv2.imwrite("%s/superimposed.jpg" % PLANS, cv2.resize(g, (1900, 1128)),
            [cv2.IMWRITE_JPEG_QUALITY, 87])
print("wrote superimposed.jpg")
