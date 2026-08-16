"""Solid building model -> USD / glTF / OBJ / IFC.

Uses the verified-aarch64 stack: shapely (geometry), mapbox_earcut (triangulation
with holes), trimesh + manifold3d (solids + booleans), pxr via usd-exchange (USD),
ifcopenshell (IFC).
"""
import os, json
import numpy as np
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

PLANS = os.path.expanduser("~/plans")
CEIL = {"level1": 15 * 0.3048 + 4 * 0.0254, "level2": 9 * 0.3048 + 7 * 0.0254}
BASE = {"level1": 0.0, "level2": 15 * 0.3048 + 4 * 0.0254 + 0.60}
SLAB = 0.25
WALL_T = 0.13

scene = trimesh.Scene()
stats = {}

for lv in ("level1", "level2"):
    rj = "%s/%s_rooms.json" % (PLANS, lv)
    if not os.path.exists(rj):
        continue
    rooms = json.load(open(rj))
    z0, ch = BASE[lv], CEIL[lv]
    polys, made = [], 0

    for i, r in enumerate(rooms):
        try:
            P = Polygon(r["poly"]).buffer(0)
        except Exception:
            continue
        if P.is_empty or P.area < 3.0:
            continue
        polys.append(P)

        # room volume as a solid (floor slab -> ceiling)
        try:
            vol = trimesh.creation.extrude_polygon(P, ch)
        except Exception:
            continue
        vol.apply_translation([0, 0, z0])
        vol.visual.face_colors = [120, 190, 235, 90] if lv == "level1" else [245, 190, 120, 90]
        scene.add_geometry(vol, node_name="%s_room_%02d" % (lv, i))
        made += 1

    if not polys:
        continue

    # floor slab = union of rooms, thickened downward
    floor_poly = unary_union(polys).buffer(0.15).buffer(-0.10)
    try:
        for gp in (floor_poly.geoms if hasattr(floor_poly, "geoms") else [floor_poly]):
            slab = trimesh.creation.extrude_polygon(gp, SLAB)
            slab.apply_translation([0, 0, z0 - SLAB])
            slab.visual.face_colors = [110, 110, 120, 255]
            scene.add_geometry(slab, node_name="%s_slab" % lv)
    except Exception as e:
        print("slab failed for %s: %s" % (lv, e))

    # walls: buffer the plan wall centrelines
    w = np.load("%s/%s_walls_m.npy" % (PLANS, lv))
    segs = []
    from shapely.geometry import LineString
    for x0, y0, x1, y1 in w:
        if np.hypot(x1 - x0, y1 - y0) > 0.35:
            segs.append(LineString([(x0, y0), (x1, y1)]))
    wall_poly = unary_union([s.buffer(WALL_T / 2, cap_style=2, join_style=2) for s in segs])
    nwall = 0
    for gp in (wall_poly.geoms if hasattr(wall_poly, "geoms") else [wall_poly]):
        if gp.area < 0.05:
            continue
        try:
            wm = trimesh.creation.extrude_polygon(gp, ch)
        except Exception:
            continue
        wm.apply_translation([0, 0, z0])
        wm.visual.face_colors = [210, 210, 215, 255]
        scene.add_geometry(wm, node_name="%s_walls_%03d" % (lv, nwall))
        nwall += 1

    stats[lv] = dict(rooms=made, wall_solids=nwall,
                     area_sqft=round(sum(p.area for p in polys) / 0.09290304, 1),
                     ceiling_m=round(ch, 3))
    print("%s: %d room volumes, %d wall solids, %.0f sqft, ceiling %.2fm"
          % (lv, made, nwall, stats[lv]["area_sqft"], ch))

print("scene: %d geometries" % len(scene.geometry))

# ---- exports ----
for ext in ("glb", "obj"):
    p = "%s/building.%s" % (PLANS, ext)
    try:
        scene.export(p)
        print("wrote %s (%.1f MB)" % (p, os.path.getsize(p) / 1e6))
    except Exception as e:
        print("%s export failed: %s" % (ext, e))

# ---- USD ----
try:
    from pxr import Usd, UsdGeom, Gf
    usd_path = "%s/building.usda" % PLANS
    stage = Usd.Stage.CreateNew(usd_path) if not os.path.exists(usd_path) \
        else Usd.Stage.Open(usd_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Building")
    stage.SetDefaultPrim(root.GetPrim())
    n = 0
    for name, geom in scene.geometry.items():
        safe = "".join(c if c.isalnum() else "_" for c in name)
        lvl = "Level1" if name.startswith("level1") else "Level2"
        UsdGeom.Xform.Define(stage, "/Building/%s" % lvl)
        mesh = UsdGeom.Mesh.Define(stage, "/Building/%s/%s" % (lvl, safe))
        mesh.CreatePointsAttr([Gf.Vec3f(*map(float, v)) for v in geom.vertices])
        mesh.CreateFaceVertexCountsAttr([3] * len(geom.faces))
        mesh.CreateFaceVertexIndicesAttr([int(i) for i in geom.faces.reshape(-1)])
        n += 1
    stage.GetRootLayer().Save()
    print("wrote %s  (%d meshes, Z-up, metres)" % (usd_path, n))
except Exception as e:
    print("USD export failed: %s" % e)

# ---- IFC ----
try:
    import ifcopenshell
    from ifcopenshell.api import run
    f = ifcopenshell.file(schema="IFC4")
    run("root.create_entity", f, ifc_class="IfcProject", name="Lake Union Building")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    ctx = run("context.add_context", f, context_type="Model")
    site = run("root.create_entity", f, ifc_class="IfcSite", name="1700 Westlake Ave N")
    bld = run("root.create_entity", f, ifc_class="IfcBuilding", name="Lake Union Building")
    run("aggregate.assign_object", f, products=[site], relating_object=f.by_type("IfcProject")[0])
    run("aggregate.assign_object", f, products=[bld], relating_object=site)
    nsp = 0
    for lv in stats:
        st = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name=lv)
        run("aggregate.assign_object", f, products=[st], relating_object=bld)
        for i, r in enumerate(json.load(open("%s/%s_rooms.json" % (PLANS, lv)))):
            sp = run("root.create_entity", f, ifc_class="IfcSpace",
                     name="%s_room_%02d" % (lv, i))
            run("aggregate.assign_object", f, products=[sp], relating_object=st)
            nsp += 1
    f.write("%s/building.ifc" % PLANS)
    print("wrote %s/building.ifc  (%d IfcSpace, %d storeys)" % (PLANS, nsp, len(stats)))
except Exception as e:
    print("IFC export failed: %s" % e)

json.dump(stats, open("%s/building_stats.json" % PLANS, "w"), indent=1)
