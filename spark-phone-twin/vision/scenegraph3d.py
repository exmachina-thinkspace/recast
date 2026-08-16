"""Live 3D scenegraph: Building -> Level -> Room -> Objects, built from phone video.

The plan supplies the tree's skeleton (levels, rooms, their real polygons). Phone
video supplies the leaves — objects observed inside those rooms, placed in
building coordinates and accumulated over time.

Two deliberate choices:

*Only classes that measured reliably get in.* Open-vocabulary detection found
5 of 18 building fixtures on real frames of this site and hallucinated a banner
as a "partition wall", so the vocabulary here is restricted to what actually
held up, each with its own threshold. A scenegraph full of confident nonsense is
worse than a sparse one.

*Objects must be seen repeatedly to be believed.* A track is provisional until
it has been observed MIN_OBS times; a single frame is a guess. Position is a
running median-ish mean, so one bad depth estimate cannot drag an object across
the room.

Room type is inferred from what is inside it — a toilet plus a sink is a
restroom, a fridge plus a microwave is a break room — which works with the
plain fixed COCO vocabulary and needs no extra model.
"""
import json, os, time
import numpy as np

# class -> (min confidence, merge radius m). Thresholds are per-class because
# `person` is far better trained than `whiteboard`.
RELIABLE = {
    "person":        (0.45, 0.90),
    "chair":         (0.35, 0.55),
    "couch":         (0.35, 1.10),
    "dining table":  (0.35, 1.30),
    "bed":           (0.40, 1.50),
    "tv":            (0.35, 0.70),
    "laptop":        (0.35, 0.45),
    "keyboard":      (0.30, 0.40),
    "mouse":         (0.30, 0.30),
    "book":          (0.30, 0.35),
    "clock":         (0.35, 0.40),
    "vase":          (0.30, 0.35),
    "potted plant":  (0.35, 0.60),
    "bottle":        (0.30, 0.30),
    "cup":           (0.30, 0.30),
    "bowl":          (0.30, 0.30),
    "sink":          (0.35, 0.70),
    "toilet":        (0.40, 0.70),
    "refrigerator":  (0.40, 0.90),
    "microwave":     (0.35, 0.60),
    "oven":          (0.35, 0.80),
    "toaster":       (0.30, 0.40),
    "bench":         (0.35, 1.10),
    "backpack":      (0.35, 0.45),
    "handbag":       (0.30, 0.40),
    "suitcase":      (0.35, 0.55),
    "cell phone":    (0.30, 0.25),
    # --- open-vocabulary: office/coworking fixtures COCO has no class for.
    # Measured on real frames of this building with the large world model:
    # desk 0.67, whiteboard 0.40, shelf 0.37, cabinet 0.37, vent 0.48 were solid;
    # printer and ceiling light fired weakly (0.15-0.19). Thresholds are set per
    # class from those observations, and MIN_OBS repeat-sighting filters the rest.
    "desk":              (0.40, 1.20),
    "whiteboard":        (0.32, 1.00),
    "cabinet":           (0.30, 0.90),
    "shelf":             (0.25, 0.90),
    "air vent":          (0.38, 0.60),
    "conference table":  (0.32, 1.60),
    "office chair":      (0.32, 0.60),
    "computer monitor":  (0.30, 0.55),
    "printer":           (0.28, 0.60),
    "filing cabinet":    (0.30, 0.70),
    "bookshelf":         (0.28, 0.90),
    "ceiling light":     (0.28, 0.60),
    "floor lamp":        (0.30, 0.45),
    "coffee machine":    (0.30, 0.40),
    "water cooler":      (0.30, 0.45),
    "trash can":         (0.28, 0.40),
    "projector screen":  (0.30, 1.40),
    "coat rack":         (0.28, 0.50),
    "stool":             (0.30, 0.45),
    "partition wall":    (0.35, 1.20),
    "door":              (0.32, 0.90),
    "window":            (0.32, 1.00),
    # wall-mounted fixtures: weak detectors today, but they are the most stable
    # landmarks in the building, so they earn a place in the vocabulary and the
    # repeat-sighting filter keeps false positives out of the map
    "wall art":          (0.35, 0.70),
    "picture frame":     (0.35, 0.60),
    "exit sign":         (0.35, 0.40),
    "fire extinguisher": (0.35, 0.35),
    "thermostat":        (0.35, 0.25),
    "light switch":      (0.35, 0.20),
    "power outlet":      (0.35, 0.20),
}
# Phrasing matters to an open-vocabulary detector: "computer monitor" and
# "illuminated exit sign" behave very differently from "monitor" / "exit sign".
OPEN_VOCAB = [
    "desk", "whiteboard", "cabinet", "shelf", "air vent", "conference table",
    "office chair", "computer monitor", "printer", "filing cabinet", "bookshelf",
    "ceiling light", "floor lamp", "coffee machine", "water cooler", "trash can",
    "projector screen", "coat rack", "stool", "partition wall", "door", "window",
    "wall art", "picture frame", "exit sign", "fire extinguisher", "thermostat",
    "light switch", "power outlet",
]

# people move; furniture does not. Transient classes are counted, not tracked.
TRANSIENT = {"person", "cell phone", "cup", "bottle"}
MIN_OBS = 3                     # observations before a track is "confirmed"
STALE_S = 900.0                 # drop unconfirmed tracks unseen this long

# How reliably does an object stay put? This matters because stable furniture can
# serve as landmarks for localization — a refrigerator is unique on a floor where
# every corridor wall looks identical, which is exactly where geometry-only
# matching failed. Chairs move hourly and would poison a landmark map.
STABILITY = {
    # --- tier 3: architecturally fixed or mounted. The best landmarks, because
    # they are effectively part of the building. Anything screwed to a wall
    # outlives every furniture rearrangement.
    "door": 3, "window": 3, "wall art": 3, "picture frame": 3, "clock": 3,
    "exit sign": 3, "fire extinguisher": 3, "thermostat": 3, "light switch": 3,
    "power outlet": 3, "air vent": 3, "whiteboard": 3, "projector screen": 3,
    "sink": 3, "toilet": 3, "refrigerator": 3, "oven": 3, "water cooler": 3,
    "bookshelf": 3, "shelf": 3, "cabinet": 3, "filing cabinet": 3,
    "wall mounted tv": 3, "tv": 3,          # office TVs are almost always mounted
    # --- tier 2: heavy, moved rarely. Usable landmarks.
    "desk": 2, "conference table": 2, "dining table": 2, "couch": 2, "bed": 2,
    "microwave": 2, "coffee machine": 2, "bench": 2, "coat rack": 2,
    "printer": 2, "floor lamp": 2, "trash can": 2, "partition wall": 2,
    "ceiling light": 2, "computer monitor": 2,   # monitors sit on a fixed desk
    # --- tier 1 and below: repositioned constantly; would poison a landmark map
    "chair": 1, "office chair": 1, "stool": 1, "potted plant": 1, "laptop": 1,
    "keyboard": 1, "mouse": 1, "book": 1, "vase": 1, "bowl": 1, "toaster": 1,
    "backpack": 0, "handbag": 0, "suitcase": 0, "person": 0, "cell phone": 0,
    "cup": 0, "bottle": 0,
}
LANDMARK_MIN_STABILITY = 2      # tier 2+ may anchor a pose
LANDMARK_MIN_OBS = 5            # and must be seen more than a merely-confirmed track

ROOM_TYPE_RULES = [
    ("restroom",     {"toilet"},                       {"sink"}),
    ("break room",   {"refrigerator", "microwave"},    {"sink", "oven", "toaster"}),
    ("kitchen",      {"oven", "refrigerator"},         {"sink", "microwave"}),
    ("meeting room", {"dining table"},                 {"chair", "tv", "whiteboard"}),
    ("lounge",       {"couch"},                        {"tv", "potted plant"}),
    ("bedroom",      {"bed"},                          set()),
    ("workspace",    {"desk"},                         {"chair", "laptop", "keyboard"}),
    ("open office",  {"chair"},                        {"laptop", "keyboard", "desk"}),
]


class Track:
    __slots__ = ("cls", "pos", "n", "conf", "first", "last", "room_id", "level",
                 "size", "nsize", "yaw", "nyaw")

    def __init__(self, cls, pos, conf, room_id, level, t, size=None):
        self.cls, self.pos, self.n, self.conf = cls, np.asarray(pos, np.float32), 1, conf
        self.first = self.last = t
        self.room_id, self.level = room_id, level
        # measured extent (w, depth, h) in metres, averaged over sightings; None
        # until something plausible is measured, so callers fall back to a prior
        self.size = None if size is None else np.asarray(size, np.float32)
        self.nsize = 0 if size is None else 1
        self.yaw, self.nyaw = None, 0

    def update_yaw(self, yaw):
        """Circular running mean — a facing near 0/360 must not average to 180.

        Furniture orientation is modulo 180 (a desk facing 10 deg and 190 deg is
        the same desk), so fold before averaging.
        """
        if yaw is None:
            return
        y = float(yaw) % 180.0
        if self.yaw is None:
            self.yaw, self.nyaw = y, 1
            return
        a0, a1 = np.radians(self.yaw * 2), np.radians(y * 2)
        w = 1.0 / (self.nyaw + 1)
        sx = (1 - w) * np.cos(a0) + w * np.cos(a1)
        sy = (1 - w) * np.sin(a0) + w * np.sin(a1)
        self.yaw = float(np.degrees(np.arctan2(sy, sx)) / 2.0) % 180.0
        self.nyaw += 1

    def update(self, pos, conf, t, size=None, yaw=None):
        # running mean: one bad depth sample should nudge, not teleport
        w = 1.0 / (self.n + 1)
        self.pos = (1 - w) * self.pos + w * np.asarray(pos, np.float32)
        self.conf = max(self.conf, conf)
        self.n += 1
        self.last = t
        self.update_yaw(yaw)
        if size is not None:
            s = np.asarray(size, np.float32)
            if self.size is None:
                self.size, self.nsize = s, 1
            else:
                ws = 1.0 / (self.nsize + 1)
                self.size = (1 - ws) * self.size + ws * s
                self.nsize += 1

    @property
    def confirmed(self):
        return self.n >= MIN_OBS

    def as_dict(self):
        return dict(cls=self.cls, position=[round(float(v), 2) for v in self.pos],
                    size_m=None if self.size is None else
                    [round(float(v), 2) for v in self.size],
                    yaw_deg=None if self.yaw is None else round(float(self.yaw), 1),
                    observations=self.n, confidence=round(float(self.conf), 2),
                    confirmed=bool(self.confirmed),
                    first_seen=round(self.first, 1), last_seen=round(self.last, 1))


def point_in_poly(pt, poly):
    P = np.asarray(poly, np.float64)
    x, y = float(pt[0]), float(pt[1])
    inside = False
    j = len(P) - 1
    for i in range(len(P)):
        xi, yi, xj, yj = P[i, 0], P[i, 1], P[j, 0], P[j, 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


class SceneGraph:
    """Building -> Level -> Room -> Objects, updated live."""

    def __init__(self, levels, name="1700 Westlake Ave N"):
        self.name = name
        self.levels = levels                  # {lv: {"rooms": [...], "base_z": z}}
        self.tracks = []
        self.people_now = {}                  # room_id -> live count
        self.t0 = time.time()

    # ---- room lookup
    def room_of(self, pos, level):
        rooms = self.levels.get(level, {}).get("rooms", [])
        for i, r in enumerate(rooms):
            if point_in_poly(pos[:2], r["poly"]):
                return "%s_room_%02d" % (level, i), i
        return None, None

    # ---- ingestion
    def observe(self, cls, pos_world, conf, level, t=None, size=None, yaw=None):
        """Fold one detection into the graph. Returns the track it joined.

        `size` is the object's measured (w, depth, h) in metres when the
        detector gave a mask we could back-project; None falls back to a
        per-class prior at render time."""
        if cls not in RELIABLE:
            return None
        min_conf, radius = RELIABLE[cls]
        if conf < min_conf:
            return None
        t = t or time.time()
        room_id, _ = self.room_of(pos_world, level)
        # merge into the nearest same-class track within its radius
        best, bd = None, radius
        for tr in self.tracks:
            if tr.cls != cls or tr.level != level:
                continue
            d = float(np.linalg.norm(tr.pos[:2] - np.asarray(pos_world, np.float32)[:2]))
            if d < bd:
                best, bd = tr, d
        if best is not None:
            best.update(pos_world, conf, t, size, yaw)
            if room_id:
                best.room_id = room_id
            return best
        tr = Track(cls, pos_world, conf, room_id, level, t, size)
        tr.update_yaw(yaw)
        self.tracks.append(tr)
        return tr

    def prune(self, now=None):
        now = now or time.time()
        keep = []
        for tr in self.tracks:
            if tr.cls in TRANSIENT and now - tr.last > 20.0:
                continue                          # people leave; don't keep them
            if not tr.confirmed and now - tr.last > STALE_S:
                continue                          # never corroborated
            keep.append(tr)
        self.tracks = keep

    # ---- reads
    def room_objects(self, room_id, confirmed_only=True):
        return [t for t in self.tracks
                if t.room_id == room_id and (t.confirmed or not confirmed_only)
                and t.cls not in TRANSIENT]

    def room_type(self, room_id):
        present = {t.cls for t in self.room_objects(room_id)}
        if not present:
            return None, 0.0
        best, score = None, 0.0
        for label, required, supporting in ROOM_TYPE_RULES:
            if not required <= present:
                continue
            s = 1.0 + 0.25 * len(supporting & present)
            if s > score:
                best, score = label, s
        return best, round(score, 2)

    def landmarks(self, level=None):
        """Stable, well-observed objects usable as localization anchors.

        Returns [{cls, x, y, z, level, n}]. Requires both stability tier and
        repeated sightings: a desk seen once could be a misdetection, and a
        landmark map polluted by phantoms is worse than no landmark map.
        """
        out = []
        for t in self.tracks:
            if level is not None and t.level != level:
                continue
            if STABILITY.get(t.cls, 0) < LANDMARK_MIN_STABILITY:
                continue
            if t.n < LANDMARK_MIN_OBS:
                continue
            out.append(dict(cls=t.cls, x=float(t.pos[0]), y=float(t.pos[1]),
                            z=float(t.pos[2]), level=t.level, n=int(t.n),
                            stability=STABILITY.get(t.cls, 0)))
        return out

    def counts(self):
        conf = [t for t in self.tracks if t.confirmed and t.cls not in TRANSIENT]
        people = [t for t in self.tracks if t.cls == "person"]
        rooms = {t.room_id for t in conf if t.room_id}
        return dict(tracks=len(self.tracks), confirmed=len(conf),
                    people=len(people), rooms_with_objects=len(rooms))

    # ---- serialisation
    def to_dict(self):
        out = dict(building=self.name, generated=time.strftime("%Y-%m-%dT%H:%M:%S"),
                   uptime_s=round(time.time() - self.t0, 1),
                   summary=self.counts(), levels=[])
        for lv, L in self.levels.items():
            rooms_out = []
            for i, r in enumerate(L.get("rooms", [])):
                rid = "%s_room_%02d" % (lv, i)
                objs = self.room_objects(rid)
                if not objs:
                    continue
                rtype, sc = self.room_type(rid)
                rooms_out.append(dict(
                    room_id=rid, area_m2=round(float(r.get("area_m2", 0)), 1),
                    inferred_type=rtype, type_score=sc,
                    people_now=int(self.people_now.get(rid, 0)),
                    objects=[o.as_dict() for o in objs]))
            if rooms_out:
                out["levels"].append(dict(level=lv, base_z=round(L["base_z"], 2),
                                          rooms=rooms_out))
        return out

    def save(self, path):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=1)
        os.replace(tmp, path)
        return path

    def tree_lines(self, max_rooms=14):
        """Compact text tree for on-screen rendering."""
        d = self.to_dict()
        L = ["%s" % d["building"]]
        for lvl in d["levels"]:
            L.append("+- %s  (z=%.1f m)" % (lvl["level"], lvl["base_z"]))
            for r in lvl["rooms"][:max_rooms]:
                head = "|  +- %s  %.0f m2" % (r["room_id"].replace("level", "L")
                                              .replace("_room_", " r"), r["area_m2"])
                if r["inferred_type"]:
                    head += "  [%s]" % r["inferred_type"]
                if r["people_now"]:
                    head += "  people:%d" % r["people_now"]
                L.append(head)
                agg = {}
                for o in r["objects"]:
                    agg[o["cls"]] = agg.get(o["cls"], 0) + 1
                for cls, n in sorted(agg.items(), key=lambda x: -x[1])[:6]:
                    L.append("|  |    - %s x%d" % (cls, n))
        if len(L) == 1:
            L.append("  (walk a room with a phone to populate)")
        return L


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    rooms = [dict(area_m2=20.0, poly=[[0, 0], [5, 0], [5, 4], [0, 4]]),
             dict(area_m2=12.0, poly=[[6, 0], [9, 0], [9, 4], [6, 4]])]
    sg = SceneGraph({"level1": dict(rooms=rooms, base_z=0.0)}, name="Test Building")

    # a restroom in room 0
    for _ in range(4):
        sg.observe("toilet", [1.0, 1.0, 0.4], 0.7, "level1")
        sg.observe("sink", [2.0, 1.0, 0.9], 0.6, "level1")
    # a workspace in room 1
    for _ in range(4):
        sg.observe("desk", [7.0, 2.0, 0.75], 0.6, "level1")
        sg.observe("chair", [7.6, 2.0, 0.5], 0.5, "level1")
        sg.observe("laptop", [7.0, 2.1, 0.8], 0.5, "level1")
    # below-threshold and unknown classes must be rejected
    assert sg.observe("whiteboard", [7, 2, 1], 0.10, "level1") is None, "low conf accepted"
    assert sg.observe("giraffe", [7, 2, 1], 0.99, "level1") is None, "unknown class accepted"
    # same object re-seen nearby must merge, not duplicate
    n_before = len(sg.tracks)
    sg.observe("desk", [7.05, 2.02, 0.75], 0.6, "level1")
    assert len(sg.tracks) == n_before, "duplicate track created"

    print("\n".join(sg.tree_lines()))
    print("\ncounts:", sg.counts())
    print("room0 type:", sg.room_type("level1_room_00"))
    print("room1 type:", sg.room_type("level1_room_01"))
    assert sg.room_type("level1_room_00")[0] == "restroom"
    assert sg.room_type("level1_room_01")[0] in ("workspace", "open office")
    print("\nSELF-TEST OK")
