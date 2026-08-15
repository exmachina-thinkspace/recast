#!/usr/bin/env python3
"""Embed a JSON file into one of the data constants at the top of seattle-office-vitals-3d.html.

The page is a single self-contained HTML file with no build step and no runtime fetches (it must work when
double-clicked from disk, where fetch() of local files is blocked). Data therefore lives inline as
`const B = [...]`, `const F = {...}` and `const BV = {...}`. This tool swaps one of them for the contents of a
JSON file, so nobody has to hand-edit a 100 KB line.

  python3 tools/embed_json.py --var BV data/build_vitals_all.json     # Build Vitals records keyed by building #
  python3 tools/embed_json.py --var B  data/buildings.json            # replace the building list
  python3 tools/embed_json.py --var BV --show                          # print the current value

--merge (objects only) shallow-merges the file into the existing value instead of replacing it, e.g. to add BHI
records for a few buildings at a time.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HTML = os.path.join(os.path.dirname(HERE), "seattle-office-vitals-3d.html")

def find(src, var):
    m = re.search(r"const %s = (\[.*?\]|\{.*?\});\n" % re.escape(var), src, re.S)
    if not m: sys.exit(f"could not find `const {var} = ...;` in the HTML")
    return m

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="JSON file to embed")
    ap.add_argument("--var", required=True, choices=["B", "F", "BV"], help="which constant to replace")
    ap.add_argument("--html", default=DEFAULT_HTML)
    ap.add_argument("--merge", action="store_true", help="shallow-merge into the existing object instead of replacing")
    ap.add_argument("--show", action="store_true", help="print the current value and exit")
    a = ap.parse_args()

    src = open(a.html, encoding="utf-8").read()
    m = find(src, a.var)
    current = json.loads(m.group(1))
    if a.show:
        print(json.dumps(current, indent=1)[:4000]); return
    if not a.file: sys.exit("give a JSON file to embed (or --show)")
    new = json.load(open(a.file))
    if a.var == "B" and not isinstance(new, list): sys.exit("B must be a JSON array of building objects")
    if a.var in ("F", "BV") and not isinstance(new, dict): sys.exit(f"{a.var} must be a JSON object keyed by building #")
    if isinstance(new, dict): new = {k: v for k, v in new.items() if not str(k).startswith("_")}   # drop _comment-style keys
    if a.merge:
        if not isinstance(current, dict): sys.exit("--merge only works for object constants")
        merged = dict(current); merged.update(new); new = merged
    src = src[:m.start(1)] + json.dumps(new, separators=(",", ":")) + src[m.end(1):]
    open(a.html, "w", encoding="utf-8").write(src)
    n = len(new)
    print(f"embedded {a.var}: {n} {'buildings' if a.var == 'B' else 'records'} -> {a.html}")

if __name__ == "__main__":
    main()
