"""Single-view depth -> coloured PLY mesh. numpy only; Open3D has no aarch64 wheel."""
import sys
import numpy as np
import cv2
from PIL import Image
from transformers import pipeline

HFOV_DEG = 120.0   # Arlo VMC4070PA is ~160 deg diagonal; this is an estimate, not calibration
STRIDE = 3         # grid downsample factor
EDGE_TOL = 0.35    # drop faces spanning more than this relative depth jump

FRAMES = "/home/acer01/arlo-frames"

pipe = pipeline(
    "depth-estimation",
    model="depth-anything/Depth-Anything-V2-Metric-Indoor-Base-hf",
    device=0,
)


def build(name):
    jpg = "%s/%s.jpg" % (FRAMES, name)
    img = Image.open(jpg)
    W, H = img.size

    d = np.array(pipe(img)["predicted_depth"], dtype=np.float32)
    if d.ndim == 3:
        d = d[0]
    if d.shape != (H, W):
        d = cv2.resize(d, (W, H), interpolation=cv2.INTER_LINEAR)

    rgb = cv2.cvtColor(cv2.imread(jpg), cv2.COLOR_BGR2RGB)

    d = d[::STRIDE, ::STRIDE]
    rgb = rgb[::STRIDE, ::STRIDE]
    h, w = d.shape

    # pinhole back-projection; fx from assumed horizontal FOV
    fx = w / (2.0 * np.tan(np.radians(HFOV_DEG) / 2.0))
    fy = fx
    cx = w / 2.0
    cy = h / 2.0

    u, v = np.meshgrid(np.arange(w), np.arange(h))
    X = (u - cx) * d / fx
    Y = -(v - cy) * d / fy
    Z = -d
    verts = np.stack([X, Y, Z], -1).reshape(-1, 3).astype(np.float32)
    cols = rgb.reshape(-1, 3).astype(np.uint8)

    idx = np.arange(h * w).reshape(h, w)
    tl = idx[:-1, :-1]
    tr = idx[:-1, 1:]
    bl = idx[1:, :-1]
    br = idx[1:, 1:]
    faces = np.concatenate([
        np.stack([tl, bl, tr], -1).reshape(-1, 3),
        np.stack([tr, bl, br], -1).reshape(-1, 3),
    ]).astype(np.int32)

    # drop triangles that straddle a depth discontinuity (otherwise walls smear
    # into foreground objects and the mesh looks like melted wax)
    fd = d.reshape(-1)[faces]
    spread = fd.max(1) - fd.min(1)
    faces = faces[spread < EDGE_TOL * fd.mean(1)]

    out = "%s/%s.ply" % (FRAMES, name)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "element vertex %d\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "element face %d\n"
        "property list uchar int vertex_indices\n"
        "end_header\n" % (len(verts), len(faces))
    )
    with open(out, "wb") as f:
        f.write(header.encode())
        vrec = np.empty(len(verts), dtype=[
            ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
            ("r", "u1"), ("g", "u1"), ("b", "u1"),
        ])
        vrec["x"], vrec["y"], vrec["z"] = verts.T
        vrec["r"], vrec["g"], vrec["b"] = cols.T
        f.write(vrec.tobytes())

        frec = np.empty(len(faces), dtype=[
            ("n", "u1"), ("a", "<i4"), ("b", "<i4"), ("c", "<i4"),
        ])
        frec["n"] = 3
        frec["a"], frec["b"], frec["c"] = faces.T
        f.write(frec.tobytes())

    print("%-8s %7d verts %8d faces  depth %.2f-%.2f m  -> %s"
          % (name, len(verts), len(faces), d.min(), d.max(), out), flush=True)


for n in sys.argv[1:]:
    build(n)
