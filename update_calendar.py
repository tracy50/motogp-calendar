#!/usr/bin/env python3
import json, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE="https://api.motogp.pulselive.com/motogp/v1"
YEAR=2026
OUT=Path("motogp-calendar.ics")

TZ={
"Thailand":"Asia/Bangkok",
"Brazil":"America/Sao_Paulo",
"United States":"America/Chicago",
"USA":"America/Chicago",
"Spain":"Europe/Madrid",
"France":"Europe/Paris",
"Italy":"Europe/Rome",
"Hungary":"Europe/Budapest",
"Czechia":"Europe/Prague",
"Czech Republic":"Europe/Prague",
"Netherlands":"Europe/Amsterdam",
"Germany":"Europe/Berlin",
"Great Britain":"Europe/London",
"Austria":"Europe/Vienna",
"Japan":"Asia/Tokyo",
"Indonesia":"Asia/Makassar",
"Australia":"Australia/Melbourne",
"Malaysia":"Asia/Kuala_Lumpur",
"Qatar":"Asia/Qatar",
"Portugal":"Europe/Lisbon",
"San Marino":"Europe/Rome"
}
ZH={"Thailand":"泰國站","Brazil":"巴西站","United States":"美國站","Spain":"西班牙站",
"France":"法國站","Italy":"義大利站","Hungary":"匈牙利站","Czechia":"捷克站",
"Czech Republic":"捷克站","Netherlands":"荷蘭站","Germany":"德國站","Great Britain":"英國站",
"Austria":"奧地利站","Japan":"日本站","Indonesia":"印尼站","Australia":"澳洲站",
"Malaysia":"馬來西亞站","Qatar":"卡達站","Portugal":"葡萄牙站"}

def get(path):
    req=urllib.request.Request(BASE+path,headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)

def esc(s):
    return str(s).replace("\\","\\\\").replace("\n","\\n").replace(",","\\,").replace(";","\\;")

def dt(s, event):
    if not s:
        return None

    raw = str(s).strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    country = (event.get("country") or {}).get("name", "")
    tzname = TZ.get(country)

    if not tzname:
        raise RuntimeError(
            f"缺少賽道時區設定: {country} / {event.get('name')}"
        )

    # MotoGP API 的 session date 視為賽道當地鐘面時間
    local_time = parsed.replace(tzinfo=None).replace(
        tzinfo=ZoneInfo(tzname)
    )

    return local_time.astimezone(timezone.utc)

def round_name(e):
    c=(e.get("country") or {}).get("name","")
    if c in ZH:return ZH[c]
    text=" ".join(str(e.get(k,"")) for k in ("name","short_name","sponsored_name"))
    extras={"Catalunya":"加泰隆尼亞站","Aragon":"亞拉岡站","San Marino":"聖馬利諾站","Valencia":"瓦倫西亞站"}
    for k,v in {**ZH,**extras}.items():
        if k.lower() in text.lower():return v
    return c+"站" if c else "MotoGP"

seasons=get("/results/seasons")
season=next(x for x in seasons if x.get("year")==YEAR)
events=get(f"/results/events?seasonUuid={season['id']}")
if isinstance(events,dict):events=events.get("events") or events.get("data") or []

items=[]
for e in events:
    eid=e.get("id")
    if not eid:continue
    try:
        cats=get(f"/results/categories?eventUuid={eid}")
        if isinstance(cats,dict):cats=cats.get("categories") or cats.get("data") or []
        gp=next((c for c in cats if c.get("legacy_id")==3 or "motogp" in str(c.get("name","")).lower()),None)
        if not gp:continue
        sessions=get(f"/results/sessions?eventUuid={eid}&categoryUuid={gp['id']}")
        if isinstance(sessions,dict):sessions=sessions.get("sessions") or sessions.get("data") or []
    except Exception as ex:
        print("skip",e.get("name"),ex);continue
    for s in sessions:
        typ=str(s.get("type","")).upper()
        if typ not in ("SPR","RAC"):continue
        start=dt(s.get("date"), e)
        if not start:continue
        if start.tzinfo is None:start=start.replace(tzinfo=timezone.utc)
        start=start.astimezone(timezone.utc)
        sprint=typ=="SPR"
        items.append((start,s.get("id") or eid+"-"+typ,
                      f"MotoGP｜{round_name(e)}｜{'衝刺賽' if sprint else '正賽'}",
                      (e.get("circuit") or {}).get("name",""),
                      start+(timedelta(hours=1) if sprint else timedelta(hours=2))))

if not items:raise RuntimeError("沒有抓到 Sprint/正賽，為避免清空訂閱行事曆，本次停止更新。")
items.sort()
stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
L=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//tracy50//MotoGP Calendar//ZH-TW",
"CALSCALE:GREGORIAN","METHOD:PUBLISH","X-WR-CALNAME:MotoGP","X-WR-TIMEZONE:Asia/Taipei",
"X-PUBLISHED-TTL:PT1H","REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
for start,uid,title,loc,end in items:
    L += ["BEGIN:VEVENT",f"UID:{esc(uid)}@motogp-calendar",f"DTSTAMP:{stamp}",
          f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
          f"SUMMARY:{esc(title)}",f"LOCATION:{esc(loc)}",
          "DESCRIPTION:資料來源：MotoGP 官方公開賽程 API",
          "BEGIN:VALARM","TRIGGER:-PT30M","ACTION:DISPLAY",
          "DESCRIPTION:MotoGP 將於 30 分鐘後開始","END:VALARM","END:VEVENT"]
L.append("END:VCALENDAR")
OUT.write_text("\r\n".join(L)+"\r\n",encoding="utf-8")
print("完成",len(items),"個事件")
