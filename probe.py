import json, requests, time
S = requests.Session()
S.headers.update({"User-Agent": "ur-vacancy-watcher/1.0 (probe)", "Referer": "https://www.ur-net.go.jp/chintai/"})
URL = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/result/bukken_result/"
def q(skcs, page=0):
    r = S.post(URL, data=dict(mode="area", skcs=str(skcs), block="kanto", tdfk="13", rireki_tdfk="13",
        orderByField="0", pageSize="50", pageIndex=str(page), pageIndexRoom="0", sp=""), timeout=20)
    return r.status_code, r.text
st, t = q(101)
print("status", st, "len", len(t)); print(t[:300])
d = json.loads(t)
first = d[0] if isinstance(d, list) else d
print("TOP TYPE", type(d).__name__, "LEN", len(d) if isinstance(d, list) else "")
print(json.dumps({k: (v if k != "room" else (v[:1] if isinstance(v, list) else v)) for k, v in first.items()}, ensure_ascii=False, indent=1)[:4000])
tot = 0
for s in range(101, 124):
    time.sleep(1.2)
    st, t = q(s)
    d = json.loads(t) if t.strip() not in ("", "null") else []
    rc = [(b.get("danchiNm"), b.get("roomCount")) for b in d] if isinstance(d, list) else d
    n = d[0].get("bukkenCount") if isinstance(d, list) and d else 0
    tot += len(d) if isinstance(d, list) else 0
    print(s, st, "bukkenCount", n, "returned", len(d) if isinstance(d, list) else "-", "with_rooms", sum(1 for _, c in rc if str(c) not in ("0", "None")) if isinstance(rc, list) else rc)
print("TOTAL", tot)
