"""Refresh RTSP stream URLs from a Scrypted instance into ~/plans/streams.json.

Scrypted regenerates its rebroadcast path tokens whenever the service restarts
or a camera's stream config changes. Anything holding an old URL gets a bare
400 Bad Request with no hint of why — which is exactly how the Arlo feeds went
dark. Re-extract instead of hard-coding.

Extraction runs through the Chrome DevTools Protocol against a browser that is
already logged into Scrypted, so no credentials are handled here.

  python refresh_streams.py [--host 127.0.0.1:10443] [--cdp 9222]
"""
import argparse, asyncio, json, os, sys, time, urllib.request

PLANS = os.path.expanduser("~/plans")
OUT = os.path.join(PLANS, "streams.json")

# Scrypted device name -> the app's camera key
NAME_MAP = {
    "2F LOBBY": "2F LOBBY",
    "1F COMMON AREA": "1F COMMON AREA",
    "2F SW HALLWAY": "2F SW HALLWAY",
}


async def ev(ws, expr, i):
    await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True,
                                         "awaitPromise": True}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == i:
            return m.get("result", {}).get("result", {}).get("value")


async def extract(cdp_port, host):
    import websockets
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % cdp_port))
    page = [t for t in tabs if t.get("type") == "page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=40_000_000) as ws:
        n = [0]

        def nid():
            n[0] += 1
            return n[0]

        await ev(ws, "location.href=%s" % json.dumps(
            "https://%s/endpoint/@scrypted/core/public/#/device" % host), nid())
        await asyncio.sleep(8)

        if await ev(ws, 'document.body.innerText.includes("User Name")', nid()):
            print("NOT LOGGED IN to %s — log in first, then re-run" % host)
            return None

        cams = await ev(ws, """
          Array.from(document.querySelectorAll("a"))
            .map(a=>({n:a.textContent.trim(),h:a.getAttribute("href")||""}))
            .filter(x=>/^#\\/device\\/\\d+$/.test(x.h) && x.n && !/plugin/i.test(x.n))
        """, nid())

        out, seen = {}, set()
        for c in cams or []:
            if c["n"] in seen:
                continue
            seen.add(c["n"])
            await ev(ws, "location.hash=%s" % json.dumps(c["h"]), nid())
            await asyncio.sleep(6)
            await ev(ws, """(()=>{const e=Array.from(
                document.querySelectorAll("button.v-expansion-panel-title"))
                .find(x=>/^\\s*streams\\s*$/i.test(x.textContent)); if(e) e.click();})()""", nid())
            await asyncio.sleep(6)
            urls = await ev(ws, """(()=>{
                const h=document.body.innerHTML.replace(/&quot;/g,String.fromCharCode(34));
                const re=/"subgroup":"Stream: Cloud RTSP"[^}]*?"value":"(rtsp:\\/\\/[^"]+)"/g;
                const o=[]; let m; while((m=re.exec(h))!==null) o.push(m[1]); return o;})()""", nid())
            if urls:
                # Scrypted renders these as localhost from its own perspective
                out[c["n"]] = urls[0].replace("localhost", host.split(":")[0])
            print("  %-16s %s" % (c["n"], out.get(c["n"], "no Cloud RTSP")))
        return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1:10443",
                    help="Scrypted host:port (default: the Spark's own instance)")
    ap.add_argument("--cdp", type=int, default=9222)
    a = ap.parse_args()

    got = asyncio.run(extract(a.cdp, a.host))
    if got is None:
        sys.exit(1)
    mapped = {NAME_MAP[k]: v for k, v in got.items() if k in NAME_MAP}
    if not mapped:
        print("no usable Cloud RTSP streams found — is Rebroadcast enabled on the cameras?")
        sys.exit(2)
    json.dump({"scrypted_host": a.host, "refreshed": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "streams": mapped}, open(OUT, "w"), indent=1)
    print("\nwrote %s with %d stream(s)" % (OUT, len(mapped)))
    print("restart spark_app.py to pick them up")
