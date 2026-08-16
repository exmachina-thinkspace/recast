"""Save and reload a scan session, so a walkthrough survives the app closing.

Everything the twin learns — accumulated surfaces, the object scenegraph,
measured ceiling heights, the operator's anchor — lives in memory today and
dies with the process. That makes each run start from an empty building, which
is wrong for a digital twin: the point is that it accumulates.

A session is a directory under ~/plans/scans/<name>/ holding:

  meta.json        when, which anchor, which levels, counts
  scenegraph.json  the object tree exactly as the app serialises it
  <level>.npz      accumulated voxel centres + colours, compressed

Clouds are stored as voxel centres rather than raw points, which is what the
accumulator holds anyway — a lossless round-trip of the thing that is actually
rendered, at a fraction of the size. Saves are atomic (write to .tmp, rename)
so an interrupted save cannot corrupt an existing session.
"""
import json, os, shutil, time
import numpy as np

SCANS = os.path.expanduser("~/plans/scans")


def _safe(name):
    keep = "-_. abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    s = "".join(c for c in str(name) if c in keep).strip().replace(" ", "_")
    return s or "scan"


def save(name, levels, scenegraph=None, anchor=None, heights=None, note=""):
    """Persist a session. `levels` is {lv: {"acc": Accumulator, "base_z": z}}."""
    name = _safe(name)
    root = os.path.join(SCANS, name)
    tmp = root + ".tmp"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)

    counts = {}
    for lv, L in (levels or {}).items():
        acc = L.get("acc")
        if acc is None:
            continue
        P, C = acc.get()
        if P is None or not len(P):
            continue
        np.savez_compressed(os.path.join(tmp, "%s.npz" % lv),
                            points=P.astype(np.float32), colors=C.astype(np.uint8),
                            base_z=np.float32(L.get("base_z", 0.0)),
                            voxel=np.float32(getattr(acc, "voxel", 0.06)))
        counts[lv] = int(len(P))

    if scenegraph is not None:
        json.dump(scenegraph, open(os.path.join(tmp, "scenegraph.json"), "w"), indent=1)
    if heights is not None:
        json.dump(heights, open(os.path.join(tmp, "ceiling_heights.json"), "w"), indent=1)

    meta = dict(name=name, saved=time.strftime("%Y-%m-%dT%H:%M:%S"),
                points=counts, total_points=int(sum(counts.values())),
                anchor=anchor, note=note,
                objects=sum(len(r.get("objects", []))
                            for lvl in (scenegraph or {}).get("levels", [])
                            for r in lvl.get("rooms", [])) if scenegraph else 0)
    json.dump(meta, open(os.path.join(tmp, "meta.json"), "w"), indent=1)

    # atomic swap: a half-written session must never replace a good one
    if os.path.isdir(root):
        old = root + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.rename(root, old)
        os.rename(tmp, root)
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.makedirs(SCANS, exist_ok=True)
        os.rename(tmp, root)
    return root, meta


def list_scans():
    """Every saved session, newest first."""
    out = []
    if not os.path.isdir(SCANS):
        return out
    for n in os.listdir(SCANS):
        m = os.path.join(SCANS, n, "meta.json")
        if os.path.exists(m):
            try:
                out.append(json.load(open(m)))
            except Exception:
                pass
    return sorted(out, key=lambda d: d.get("saved", ""), reverse=True)


def load(name):
    """Read a session back. Returns {meta, clouds:{lv:(P,C)}, scenegraph, heights}."""
    root = os.path.join(SCANS, _safe(name))
    if not os.path.isdir(root):
        return None
    meta = {}
    mp = os.path.join(root, "meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp))
    clouds = {}
    for f in os.listdir(root):
        if not f.endswith(".npz"):
            continue
        lv = f[:-4]
        try:
            z = np.load(os.path.join(root, f))
            clouds[lv] = (z["points"], z["colors"])
        except Exception:
            pass
    sgp = os.path.join(root, "scenegraph.json")
    hp = os.path.join(root, "ceiling_heights.json")
    return dict(meta=meta, clouds=clouds,
                scenegraph=json.load(open(sgp)) if os.path.exists(sgp) else None,
                heights=json.load(open(hp)) if os.path.exists(hp) else None)


def restore_into(levels, loaded):
    """Fold a loaded session's clouds back into live accumulators.

    Points are nudged to the centre of their cell first. The accumulator hands
    out cell *corners*, and in float32 `key * voxel / voxel` can land a hair
    below the integer, so flooring drops the point into the neighbouring cell
    and a few voxels merge on every save/load cycle. Half a voxel of headroom
    makes the round-trip exact.
    """
    n = 0
    for lv, (P, C) in (loaded or {}).get("clouds", {}).items():
        L = (levels or {}).get(lv)
        if L is None or L.get("acc") is None:
            continue
        half = float(getattr(L["acc"], "voxel", 0.06)) * 0.5
        L["acc"].add(np.asarray(P, np.float32) + half, C)
        n += len(P)
    return n


def export_ply(points, colors, path):
    """Coloured point cloud as binary PLY — openable in any 3D tool."""
    P = np.asarray(points, np.float32)
    C = np.asarray(colors, np.uint8)
    n = len(P)
    hdr = ("ply\nformat binary_little_endian 1.0\nelement vertex %d\n"
           "property float x\nproperty float y\nproperty float z\n"
           "property uchar red\nproperty uchar green\nproperty uchar blue\n"
           "end_header\n" % n).encode()
    rec = np.zeros(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                             ("r", "u1"), ("g", "u1"), ("b", "u1")])
    rec["x"], rec["y"], rec["z"] = P[:, 0], P[:, 1], P[:, 2]
    rec["r"], rec["g"], rec["b"] = C[:, 0], C[:, 1], C[:, 2]
    with open(path, "wb") as f:
        f.write(hdr)
        f.write(rec.tobytes())
    return path, n


def export_mesh(points, path, voxel=0.06, pad=2):
    """Watertight-ish surface from the voxel occupancy via marching cubes.

    The accumulator already holds an occupancy grid, so a surface follows
    directly — no Poisson reconstruction, and no open3d (unavailable on this
    aarch64 box). Returns (path, n_vertices, n_faces) or None if the scan is too
    sparse to surface.
    """
    try:
        from skimage import measure
        import trimesh
    except Exception as e:
        return None
    P = np.asarray(points, np.float32)
    if len(P) < 500:
        return None
    lo = P.min(0) - voxel * pad
    idx = np.floor((P - lo) / voxel).astype(np.int32)
    dims = idx.max(0) + 1 + pad
    if np.prod(dims.astype(np.int64)) > 60_000_000:      # keep memory sane
        return None
    vol = np.zeros(tuple(int(d) for d in dims), np.float32)
    vol[idx[:, 0], idx[:, 1], idx[:, 2]] = 1.0
    # a light blur turns isolated voxels into a surface marching cubes can walk
    try:
        from scipy.ndimage import gaussian_filter
        vol = gaussian_filter(vol, 0.8)
    except Exception:
        pass
    try:
        verts, faces, _n, _v = measure.marching_cubes(vol, level=0.35)
    except Exception:
        return None
    verts = verts * voxel + lo
    m = trimesh.Trimesh(vertices=verts, faces=faces, process=True)
    m.export(path)
    return path, len(m.vertices), len(m.faces)


def export_all(levels, out_dir=None, voxel=0.06):
    """Write PLY (and a mesh where possible) for every level with geometry."""
    out_dir = out_dir or os.path.expanduser("~/plans/exports")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for lv, L in (levels or {}).items():
        acc = L.get("acc")
        if acc is None:
            continue
        P, C = acc.get()
        if P is None or len(P) < 100:
            continue
        pp = os.path.join(out_dir, "scan_%s.ply" % lv)
        _p, n = export_ply(P, C, pp)
        made.append(dict(kind="ply", level=lv, path=pp, points=n))
        mp = os.path.join(out_dir, "scan_%s.obj" % lv)
        got = export_mesh(P, mp, voxel=voxel)
        if got:
            made.append(dict(kind="mesh", level=lv, path=got[0],
                             vertices=got[1], faces=got[2]))
    return made


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.expanduser("~/arlo-vision"))
    import phone_slam

    rng = np.random.default_rng(0)
    lv = {"level1": dict(acc=phone_slam.Accumulator(0.06), base_z=0.0)}
    P = rng.uniform(0, 8, (5000, 3)).astype(np.float32)
    C = rng.integers(0, 255, (5000, 3)).astype(np.uint8)
    lv["level1"]["acc"].add(P, C)
    before = len(lv["level1"]["acc"])

    sg = dict(building="Test", levels=[dict(level="level1", rooms=[
        dict(room_id="level1_room_00", objects=[dict(cls="desk"), dict(cls="chair")])])])
    root, meta = save("selftest", lv, scenegraph=sg,
                      anchor=dict(x=1.0, y=2.0, level="level1"), note="unit test")
    print("saved %s: %d points, %d objects" % (meta["name"], meta["total_points"],
                                               meta["objects"]))

    got = load("selftest")
    assert got is not None, "load returned nothing"
    assert got["meta"]["total_points"] == before, "point count changed"
    assert got["scenegraph"]["building"] == "Test", "scenegraph did not round-trip"
    assert got["meta"]["anchor"]["x"] == 1.0, "anchor did not round-trip"

    lv2 = {"level1": dict(acc=phone_slam.Accumulator(0.06), base_z=0.0)}
    n = restore_into(lv2, got)
    after = len(lv2["level1"]["acc"])
    print("restored %d points -> %d voxels (was %d)" % (n, after, before))
    assert after == before, "restore changed the voxel count: %d vs %d" % (after, before)

    pp, npts = export_ply(P, C, "/tmp/_selftest.ply")
    print("PLY: %d points -> %s (%d bytes)" % (npts, pp, os.path.getsize(pp)))
    assert os.path.getsize(pp) > npts * 15, "PLY looks truncated"
    got = export_mesh(P, "/tmp/_selftest.obj", voxel=0.06)
    print("mesh: %s" % ("none (too sparse)" if not got else
                        "%d verts, %d faces -> %s" % (got[1], got[2], got[0])))

    names = [d["name"] for d in list_scans()]
    assert "selftest" in names, "not listed"
    print("sessions on disk: %s" % ", ".join(names[:5]))
    shutil.rmtree(root, ignore_errors=True)
    print("\nSELF-TEST OK")
