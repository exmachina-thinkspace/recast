import asyncio, json, urllib.request, websockets

WAIT_NAV, WAIT_PANEL = 7, 8


async def ev(ws, expr, i):
    await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True,
                                         "awaitPromise": True}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == i:
            return m.get("result", {}).get("result", {}).get("value")


async def main():
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/list"))
    page = [t for t in tabs if t.get("type") == "page"][0]
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=40_000_000) as ws:
        n = [0]

        def nid():
            n[0] += 1
            return n[0]

        await ev(ws, 'location.hash="#/device"', nid())
        await asyncio.sleep(WAIT_NAV)
        cams = await ev(ws, """
          Array.from(document.querySelectorAll("a"))
            .map(a=>({n:a.textContent.trim(),h:a.getAttribute("href")||""}))
            .filter(x=>/^#\\/device\\/\\d+$/.test(x.h) && x.n && !/plugin/i.test(x.n))
        """, nid())

        seen, out = set(), {}
        for c in cams or []:
            if c["n"] in seen:
                continue
            seen.add(c["n"])
            await ev(ws, "location.hash=%s" % json.dumps(c["h"]), nid())
            await asyncio.sleep(WAIT_NAV)
            clicked = await ev(ws, """(()=>{const e=Array.from(
                document.querySelectorAll("button.v-expansion-panel-title"))
                .find(x=>/^\\s*streams\\s*$/i.test(x.textContent));
                if(e){e.click();return true;} return false;})()""", nid())
            await asyncio.sleep(WAIT_PANEL)
            pairs = await ev(ws, """(()=>{
                const h=document.body.innerHTML.replace(/&quot;/g,String.fromCharCode(34));
                const re=/"subgroup":"([^"]*)"[^}]*?"value":"(rtsp:\\/\\/[^"]+)"/g;
                const o=[]; let m; while((m=re.exec(h))!==null) o.push([m[1],m[2]]);
                return o;})()""", nid())
            uniq = []
            for sg, u in (pairs or []):
                if u not in [x[1] for x in uniq]:
                    uniq.append([sg, u])
            out[c["n"]] = uniq
            print("%-16s panel=%s  streams=%d" % (c["n"], clicked, len(uniq)), flush=True)
            for sg, u in uniq:
                print("     %-22s %s" % (sg.replace("Stream: ", ""), u), flush=True)

        open("/home/acer01/arlo-frames/streams_all.json", "w").write(json.dumps(out, indent=1))
        print("saved", len(out), "cameras", flush=True)


asyncio.run(main())
