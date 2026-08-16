"""Detect objects in one Recast Lens frame with the local YOLO runtime."""

import argparse
import json
import os
import sys

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--model", default=os.environ.get("RECAST_YOLO_MODEL", "/home/acer01/arlo-vision/yolo11m.pt"))
    parser.add_argument("--conf", type=float, default=float(os.environ.get("RECAST_YOLO_CONF", "0.25")))
    parser.add_argument("--imgsz", type=int, default=int(os.environ.get("RECAST_YOLO_IMGSZ", "960")))
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(json.dumps({"error": "image not found"}))
        return 2
    if not os.path.exists(args.model):
        print(json.dumps({"error": f"model not found: {args.model}"}))
        return 2

    model = YOLO(args.model)
    result = model.predict(args.image, imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
    height, width = result.orig_shape
    objects = []
    for box in result.boxes:
        cls_id = int(box.cls[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        objects.append({
            "label": result.names.get(cls_id, str(cls_id)),
            "class_id": cls_id,
            "confidence": round(float(box.conf[0]), 4),
            "bbox_xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "center_xy": [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)],
            "relative_area": round(((x2 - x1) * (y2 - y1)) / float(width * height), 5),
        })

    summary = {}
    for obj in objects:
        summary[obj["label"]] = summary.get(obj["label"], 0) + 1

    print(json.dumps({
        "ok": True,
        "engine": "local YOLO object detector",
        "model": os.path.basename(args.model),
        "image": args.image,
        "image_size": {"width": width, "height": height},
        "count": len(objects),
        "summary": summary,
        "objects": objects,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
