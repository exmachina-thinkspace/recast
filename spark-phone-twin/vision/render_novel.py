"""Render a PLY point set from rotated viewpoints with a numpy z-buffer.

Proves the reconstruction is real geometry: a flat billboard would show no
parallax when the virtual camera swings around the scene centroid.
"""
import sys
import numpy as np

FRAMES = "/home/acer01/arlo-frames"
OUT_W, OUT_H = 1100, 620
HFOV_DEG = 120.0


def load_ply(path):
    with open(path, "rb") as f:
        head = b""
        while b"end_header\n" not in head:
            head += f.read(1)
        txt = head.decode()
        nv = int([l for l in txt.splitlines() if l.startswith("element vertex")][0].split()[-1])
        nf = int([l for l in txt.splitlines() if l.startswith("element face")][0].split()[-1])
        v = np.frombuffer(f.read(nv * 15), dtype=[
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("r", "u1"), ("g", "u1"), ("b", "u1"),
        ], count=nv)
    pts = np.stack([v["x"], v["y"], v["z"]], -1).astype(np.float32)
    col = np.stack([v["r"], v["g"], v["b"]], -1).astype(np.uint8)
    return pts, col, nf


def render(pts, col, yaw_deg, pitch_deg=0.0):
    c = pts.mean(0)
    p = pts - c

    ry = np.radians(yaw_deg)
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)],
                   [0, 1, 0],
                   [-np.sin(ry), 0, np.cos(ry)]], dtype=np.float32)
    rx = np.radians(pitch_deg)
    Rx = np.array([[1, 0, 0],
                   [0, np.cos(rx), -np.sin(rx)],
                   [0, np.sin(rx), np.cos(rx)]], dtype=np.float32)
    p = p @ (Ry @ Rx).T + c

    Z = p[:, 2]
    keep = Z < -0.2                     # in front of the camera
    p, cc, Z = p[keep], col[keep], Z[keep]

    fx = OUT_W / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))
    u = (fx * p[:, 0] / -Z + OUT_W / 2.0).astype(np.int32)
    v = (OUT_H / 2.0 - fx * p[:, 1] / -Z).astype(np.int32)

    ok = (u >= 0) & (u < OUT_W) & (v >= 0) & (v < OUT_H)
    u, v, cc, Z = u[ok], v[ok], cc[ok], Z[ok]

    # painter's algorithm: draw far to near so nearest wins
    order = np.argsort(-(-Z))
    u, v, cc = u[order], v[order], cc[order]

    img = np.zeros((OUT_H, OUT_W, 3), np.uint8)
    img[v, u] = cc
    return img


if __name__ == "__main__":
    import cv2
    name = sys.argv[1]
    pts, col, nf = load_ply("%s/%s.ply" % (FRAMES, name))
    print("%s: %d verts %d faces" % (name, len(pts), nf), flush=True)

    tiles = []
    for yaw, pitch, label in [(-22, 0, "yaw -22"), (0, 0, "original"),
                              (22, 0, "yaw +22"), (0, -25, "pitch -25 (above)")]:
        im = render(pts, col, yaw, pitch)
        cv2.putText(im, label, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        tiles.append(im)

    grid = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])])
    out = "%s/%s_views.jpg" % (FRAMES, name)
    cv2.imwrite(out, cv2.cvtColor(grid, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print("wrote", out, flush=True)
