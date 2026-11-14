
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MotoGP Calendar 3.0

功能：
- 自動取得 MotoGP 賽季、分站及場次
- 只收錄 MotoGP Sprint (SPR) 與 Race (RAC)
- 使用官方賽程頁交叉驗證週末日期
- 保留 API 明確提供的時區資訊
- API 沒有時區時，使用賽道當地時間
- 轉換成 UTC ICS，Apple Calendar 自動顯示台灣時間
- 每個事件提前 30 分鐘提醒
- 下載失敗、日期比對失敗或驗證失敗時，不覆蓋舊 ICS
- 原子寫入，避免寫到一半造成檔案損壞
"""

import html
import json
import re
import tempfile
import time
import urllib.request

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# 基本設定
# ============================================================

BASE = "https://api.motogp.pulselive.com/motogp/v1"
OFFICIAL_CALENDAR = "https://www.motogp.com/en/calendar/{year}"

OUT = Path("motogp-calendar.ics")

USER_AGENT = "Mozilla/5.0 (compatible; MotoGPCalendar/3.0)"
REQUEST_TIMEOUT = 30
RETRIES = 3

MIN_OFFICIAL_ROUNDS = 10


# ============================================================
# 國家與時區
# ============================================================

TZ = {
    "Thailand": "Asia/Bangkok",
    "Brazil": "America/Sao_Paulo",
    "Argentina": "America/Argentina/Buenos_Aires",

    "United States": "America/Chicago",
    "United States of America": "America/Chicago",
    "USA": "America/Chicago",

    "Spain": "Europe/Madrid",
    "France": "Europe/Paris",
    "Italy": "Europe/Rome",
    "Hungary": "Europe/Budapest",

    "Czechia": "Europe/Prague",
    "Czech Republic": "Europe/Prague",

    "Netherlands": "Europe/Amsterdam",
    "Germany": "Europe/Berlin",

    "Great Britain": "Europe/London",
    "United Kingdom": "Europe/London",
    "United Kingdom of Great Britain and Northern Ireland":
        "Europe/London",

    "Austria": "Europe/Vienna",
    "Japan": "Asia/Tokyo",
    "Indonesia": "Asia/Makassar",

    "Australia": "Australia/Melbourne",

    "Malaysia": "Asia/Kuala_Lumpur",
    "Qatar": "Asia/Qatar",
    "Portugal": "Europe/Lisbon",
    "San Marino": "Europe/Rome",
    "Kazakhstan": "Asia/Almaty",
    "Finland": "Europe/Helsinki",
}


ZH = {
    "Thailand": "泰國站",
    "Brazil": "巴西站",
    "Argentina": "阿根廷站",

    "United States": "美國站",
    "United States of America": "美國站",
    "USA": "美國站",

    "Spain": "西班牙站",
    "France": "法國站",
    "Italy": "義大利站",
    "Hungary": "匈牙利站",

    "Czechia": "捷克站",
    "Czech Republic": "捷克站",

    "Netherlands": "荷蘭站",
    "Germany": "德國站",

    "Great Britain": "英國站",
    "United Kingdom": "英國站",
    "United Kingdom of Great Britain and Northern Ireland":
        "英國站",

    "Austria": "奧地利站",
    "Japan": "日本站",
    "Indonesia": "印尼站",
    "Australia": "澳洲站",

    "Malaysia": "馬來西亞站",
    "Qatar": "卡達站",
    "Portugal": "葡萄牙站",
    "San Marino": "聖馬利諾站",
    "Kazakhstan": "哈薩克站",
    "Finland": "芬蘭站",
}


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


KNOWN_OFFICIAL_LABELS = [
    "great britain", "san marino", "czechia",
    "netherlands", "thailand", "argentina",
    "catalonia", "portugal", "valencia",
    "australia", "adelaide", "indonesia",
    "malaysia", "germany", "france",
    "italy", "hungary", "aragon",
    "austria", "japan", "brazil",
    "qatar", "spain", "usa",
    "finland", "kazakhstan",
]


ROUND_LABEL_HINTS = {
    "circuit ricardo tormo": "valencia",
    "motorland": "aragon",
    "circuit de barcelona-catalunya": "catalonia",
    "barcelona-catalunya": "catalonia",
    "misano": "san marino",
    "autodromo internazionale del mugello": "italy",
    "autodromo internazionale do algarve": "portugal",
    "mobility resort motegi": "japan",
    "pertamina mandalika": "indonesia",
    "phillip island": "australia",
    "adelaide street circuit": "adelaide",
    "sachsenring": "germany",
    "silverstone": "great britain",
    "tt circuit assen": "netherlands",
    "balaton": "hungary",
    "brno": "czechia",
    "red bull ring": "austria",
    "lusail": "qatar",
    "sepang": "malaysia",
    "buriram": "thailand",
    "goiania": "brazil",
    "goiânia": "brazil",
    "circuit of the americas": "usa",
    "austin": "usa",
    "jerez": "spain",
    "le mans": "france",
    "buenos aires": "argentina",
}


# ============================================================
# HTTP / API
# ============================================================

def request_bytes(url, headers=None):
    merged = {"User-Agent": USER_AGENT}

    if headers:
        merged.update(headers)

    last_error = None

    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers=merged
            )

            with urllib.request.urlopen(
                req,
                timeout=REQUEST_TIMEOUT
            ) as response:
                return response.read()

        except Exception as exc:
            last_error = exc

            if attempt + 1 < RETRIES:
                time.sleep(2 ** attempt)

    raise RuntimeError(
        f"下載失敗：{url}: {last_error}"
    )


def get_json(path):
    raw = request_bytes(
        BASE + path,
        {
            "Accept": "application/json",
            "Origin": "https://www.motogp.com",
        }
    )

    try:
        return json.loads(raw.decode("utf-8"))

    except Exception as exc:
        raise RuntimeError(
            f"MotoGP API JSON 解析失敗：{path}: {exc}"
        )


def as_list(value, *keys):
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return value[key]

    return []


def norm(value):
    value = str(value or "").lower()
    value = value.replace("ё", "е")

    value = re.sub(
        r"[^a-z0-9áéíóúàèìòùüñç]+",
        " ",
        value
    )

    return re.sub(r"\s+", " ", value).strip()


# ============================================================
# HTML 官方賽程解析
# ============================================================

class TextExtractor(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        value = html.unescape(data).strip()

        if value:
            self.parts.append(value)

    def text(self):
        return re.sub(
            r"\s+",
            " ",
            " ".join(self.parts)
        ).strip()


def parse_calendar_ranges(page_text, year):

    found = []
    occupied = []

    patterns = [
        re.compile(
            r"(?P<d1>\d{1,2})\s*[-–]\s*"
            r"(?P<d2>\d{1,2})\s+"
            r"(?P<m2>[A-Za-z]+)\s+(?P<y>\d{4})"
        ),

        re.compile(
            r"(?P<d1>\d{1,2})\s+"
            r"(?P<m1>[A-Za-z]+)\s*[-–]\s*"
            r"(?P<d2>\d{1,2})\s+"
            r"(?P<m2>[A-Za-z]+)\s+"
            r"(?P<y>\d{4})"
        ),

        re.compile(
            r"(?P<d1>\d{1,2})\s*[-–]\s*"
            r"(?P<d2>\d{1,2})\s+"
            r"(?P<m2>[A-Za-z]+)"
            r"(?!\s+\d{4})"
        ),
    ]

    for pattern in patterns:

        for match in pattern.finditer(page_text):

            if any(
                match.start() < b and match.end() > a
                for a, b in occupied
            ):
                continue

            try:
                month2 = MONTHS[
                    match.group("m2").lower()
                ]

                d1 = int(match.group("d1"))
                d2 = int(match.group("d2"))

                y = int(
                    match.groupdict().get("y") or year
                )

                if match.groupdict().get("m1"):
                    month1 = MONTHS[
                        match.group("m1").lower()
                    ]
                else:
                    month1 = month2

                start = date(y, month1, d1)

                end_year = y + (
                    1 if month2 < month1 else 0
                )

                end = date(end_year, month2, d2)

                if not 1 <= (end - start).days <= 4:
                    continue

                context = page_text[
                    max(0, match.start() - 220):
                    match.start()
                ]

                found.append((start, end, context))

                occupied.append(
                    (match.start(), match.end())
                )

            except (KeyError, ValueError):
                continue

    unique = {}

    for start, end, context in found:
        unique[(start, end)] = context

    return [
        (start, end, context)
        for (start, end), context in sorted(unique.items())
    ]


def fetch_official_weekends(year):

    url = OFFICIAL_CALENDAR.format(year=year)

    raw = request_bytes(
        url,
        {"Accept": "text/html"}
    )

    parser = TextExtractor()

    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    page_text = parser.text()

    ranges = parse_calendar_ranges(
        page_text,
        year
    )

    if len(ranges) < MIN_OFFICIAL_ROUNDS:
        raise RuntimeError(
            f"官方 {year} 賽程資料不足："
            f"只解析到 {len(ranges)} 個週末。"
            "為保護舊行事曆，停止更新。"
        )

    return ranges


# ============================================================
# 分站比對
# ============================================================

def event_official_labels(event):

    country = (
        (event.get("country") or {}).get("name") or ""
    ).strip()

    circuit = (
        (event.get("circuit") or {}).get("name") or ""
    ).strip()

    event_text = " ".join(
        str(event.get(k) or "")
        for k in (
            "name",
            "short_name",
            "sponsored_name"
        )
    )

    combined = norm(circuit + " " + event_text)

    for hint, label in ROUND_LABEL_HINTS.items():

        if norm(hint) in combined:
            return [norm(label)]

    labels = [norm(country)]

    if country in (
        "United States",
        "United States of America",
        "USA"
    ):
        labels.append("usa")

    if country in ("Czech Republic", "Czechia"):
        labels.append("czechia")

    if country in (
        "United Kingdom",
        "Great Britain",
        "United Kingdom of Great Britain and Northern Ireland"
    ):
        labels.append("great britain")

    return [x for x in labels if x]


def match_official_weekend(event, ranges):

    labels = set(event_official_labels(event))
    matches = []

    for start, end, context in ranges:

        context_n = norm(context[-220:])

        occurrences = []

        for label in KNOWN_OFFICIAL_LABELS:

            pos = context_n.rfind(label)

            if pos >= 0:
                occurrences.append((pos, label))

        if not occurrences:
            continue

        latest_pos = max(
            pos for pos, _ in occurrences
        )

        latest_labels = {
            label
            for pos, label in occurrences
            if pos == latest_pos
        }

        if (
            len(latest_labels) == 1
            and latest_labels.intersection(labels)
        ):
            matches.append(
                (
                    len(context_n) - latest_pos,
                    start,
                    end
                )
            )

    if not matches:

        name = (
            event.get("name")
            or (event.get("country") or {}).get("name")
        )

        raise RuntimeError(
            f"官方賽程找不到分站日期：{name}"
        )

    nearest = min(x[0] for x in matches)

    best = {
        (x[1], x[2])
        for x in matches
        if x[0] == nearest
    }

    if len(best) != 1:
        raise RuntimeError(
            f"官方賽程日期比對不唯一：{best}"
        )

    return next(iter(best))


# ============================================================
# 時區處理
# ============================================================

def event_timezone(event):

    country = (
        (event.get("country") or {}).get("name") or ""
    ).strip()

    circuit = norm(
        (event.get("circuit") or {}).get("name") or ""
    )

    # 澳洲 Adelaide 使用南澳時區，不是 Melbourne 時區
    if country == "Australia" and "adelaide" in circuit:
        return ZoneInfo("Australia/Adelaide")

    tz_name = TZ.get(country)

    if not tz_name:
        raise RuntimeError(
            f"缺少賽道時區設定：{country} / "
            f"{event.get('name') or event.get('short_name')}"
        )

    return ZoneInfo(tz_name)


def session_local_datetime(session_date, event):

    if not session_date:
        raise RuntimeError("MotoGP 場次缺少日期。")

    raw = str(session_date).strip()

    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(raw)

    except ValueError:
        raise RuntimeError(
            f"無法解析 MotoGP 日期：{session_date}"
        )

    track_tz = event_timezone(event)

    # 有明確 offset：轉換到賽道當地時區
    # 沒有 offset：視為賽道當地時間
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(track_tz)

    return parsed, track_tz


# ============================================================
# 賽事名稱
# ============================================================

def event_title(event):

    country = (
        (event.get("country") or {}).get("name") or ""
    ).strip()

    circuit = (
        (event.get("circuit") or {}).get("name") or ""
    ).strip()

    text = norm(
        circuit + " " + " ".join(
            str(event.get(k) or "")
            for k in (
                "name",
                "short_name",
                "sponsored_name"
            )
        )
    )

    extras = {
        "catalunya": "加泰隆尼亞站",
        "aragon": "亞拉岡站",
        "valencia": "瓦倫西亞站",
        "san marino": "聖馬利諾站",
        "adelaide": "澳洲站",
        "buenos aires": "阿根廷站",
    }

    for key, value in extras.items():

        if key in text:
            return value

    return ZH.get(
        country,
        country + "站" if country else "MotoGP"
    )


# ============================================================
# 賽季與場次 API
# ============================================================

def fetch_season():

    seasons = as_list(
        get_json("/results/seasons"),
        "seasons",
        "data"
    )

    if not seasons:
        raise RuntimeError(
            "MotoGP API 沒有回傳賽季資料。"
        )

    current = [
        s for s in seasons
        if s.get("current") is True
    ]

    if current:

        season = max(
            current,
            key=lambda s: int(s.get("year") or 0)
        )

    else:

        today_year = datetime.now(
            timezone.utc
        ).year

        candidates = [
            s for s in seasons
            if str(s.get("year", "")).isdigit()
            and int(s["year"]) >= today_year
        ]

        if not candidates:
            raise RuntimeError(
                "MotoGP API 找不到當前或未來賽季。"
            )

        season = max(
            candidates,
            key=lambda s: int(s["year"])
        )

    if not season.get("id") or not season.get("year"):
        raise RuntimeError(
            f"賽季資料缺少必要欄位：{season}"
        )

    return season


def fetch_sessions_for_event(event):

    event_id = event.get("id")

    if not event_id:
        raise RuntimeError(
            f"賽事缺少 ID：{event}"
        )

    categories = as_list(
        get_json(
            f"/results/categories?eventUuid={event_id}"
        ),
        "categories",
        "data"
    )

    gp = next(
        (
            c for c in categories
            if c.get("legacy_id") == 3
            or "motogp" in norm(c.get("name", ""))
        ),
        None
    )

    if not gp:
        return []

    sessions = as_list(
        get_json(
            f"/results/sessions?"
            f"eventUuid={event_id}&categoryUuid={gp['id']}"
        ),
        "sessions",
        "data"
    )

    return sessions


# ============================================================
# 建立行事曆事件
# ============================================================

def build_events(season):

    year = int(season["year"])

    official_ranges = fetch_official_weekends(year)

    api_events = as_list(
        get_json(
            f"/results/events?seasonUuid={season['id']}"
        ),
        "events",
        "data"
    )

    if not api_events:
        raise RuntimeError(
            f"{year} 賽季沒有賽事資料。"
        )

    output = []
    seen_uids = set()
    matched_event_count = 0

    for event in api_events:

        sessions = fetch_sessions_for_event(event)

        relevant = [
            s for s in sessions
            if str(s.get("type", "")).upper()
            in ("SPR", "RAC")
        ]

        if not relevant:
            continue

        weekend_start, weekend_end = (
            match_official_weekend(
                event,
                official_ranges
            )
        )

        matched_event_count += 1

        track_tz = event_timezone(event)

        for session in relevant:

            session_type = str(
                session.get("type", "")
            ).upper()

            parsed_local, track_tz = (
                session_local_datetime(
                    session.get("date"),
                    event
                )
            )

            # 官方週末最後一天為正賽日；
            # Sprint 為前一天。
            target_day = weekend_end - timedelta(
                days=1 if session_type == "SPR" else 0
            )

            if not (
                weekend_start <= target_day <= weekend_end
            ):
                raise RuntimeError(
                    f"官方週末日期不合理："
                    f"{event.get('name')} "
                    f"{weekend_start}–{weekend_end}"
                )

            local_clock = parsed_local.time().replace(
                tzinfo=None
            )

            local_start = datetime.combine(
                target_day,
                local_clock,
                tzinfo=track_tz
            )

            start_utc = local_start.astimezone(
                timezone.utc
            )

            sprint = session_type == "SPR"

            duration = timedelta(
                hours=1 if sprint else 2
            )

            end_utc = start_utc + duration

            session_id = session.get("id")

            if not session_id:
                raise RuntimeError(
                    f"場次缺少 ID：{event.get('name')}"
                )

            uid = f"{session_id}@motogp-calendar"

            if uid in seen_uids:
                raise RuntimeError(
                    f"重複場次 UID：{uid}"
                )

            seen_uids.add(uid)

            title = (
                f"MotoGP｜{event_title(event)}｜"
                f"{'衝刺賽' if sprint else '正賽'}"
            )

            circuit = (
                (event.get("circuit") or {}).get("name") or ""
            )

            output.append(
                (
                    start_utc,
                    uid,
                    title,
                    circuit,
                    end_utc
                )
            )

            if parsed_local.date() != target_day:
                print(
                    f"官方日期校正：{event_title(event)} "
                    f"{session_type}: "
                    f"{parsed_local.date()} -> {target_day}"
                )

    if matched_event_count == 0 or len(output) < 2:
        raise RuntimeError(
            "官方賽程驗證後沒有足夠的 MotoGP 場次。"
        )

    output.sort(key=lambda row: row[0])

    return output


# ============================================================
# ICS 輸出
# ============================================================

def esc(value):

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def render_ics(items):

    stamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//tracy50//MotoGP Calendar//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:MotoGP",
        "X-WR-TIMEZONE:Asia/Taipei",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]

    for start, uid, title, location, end in items:

        lines.extend([
            "BEGIN:VEVENT",

            f"UID:{esc(uid)}",

            f"DTSTAMP:{stamp}",

            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",

            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",

            f"SUMMARY:{esc(title)}",

            f"LOCATION:{esc(location)}",

            "DESCRIPTION:MotoGP Sprint／正賽",

            "BEGIN:VALARM",
            "TRIGGER:-PT30M",
            "ACTION:DISPLAY",
            "DESCRIPTION:MotoGP 將於 30 分鐘後開始",
            "END:VALARM",

            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")

    return "\r\n".join(lines) + "\r\n"


# ============================================================
# ICS 安全驗證
# ============================================================

def validate_ics(content, expected_count):

    if not content.startswith("BEGIN:VCALENDAR"):
        raise RuntimeError("ICS 標頭錯誤。")

    if not content.rstrip().endswith("END:VCALENDAR"):
        raise RuntimeError("ICS 結尾錯誤。")

    if content.count("BEGIN:VEVENT") != expected_count:
        raise RuntimeError("ICS 事件數量不一致。")

    if content.count("BEGIN:VALARM") != expected_count:
        raise RuntimeError("部分賽事缺少提醒。")

    if content.count("TRIGGER:-PT30M") != expected_count:
        raise RuntimeError("提醒時間驗證失敗。")

    if "MotoGP｜" not in content:
        raise RuntimeError("賽事標題驗證失敗。")

    if "DTSTART:" not in content:
        raise RuntimeError("沒有賽事開始時間。")


def atomic_write(path, content):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=str(path.parent),
        delete=False
    ) as temp:

        temp.write(content)
        temp_path = Path(temp.name)

    temp_path.replace(path)


# ============================================================
# 主程式
# ============================================================

def main():

    print("開始更新 MotoGP 行事曆...")

    season = fetch_season()

    print(
        f"目前使用賽季：{season['year']}"
    )

    items = build_events(season)

    content = render_ics(items)

    validate_ics(
        content,
        len(items)
    )

    # 所有下載及驗證完成後才寫入
    atomic_write(
        OUT,
        content
    )

    print(
        f"成功更新：{len(items)} 個 Sprint／正賽事件"
    )

    print(
        f"輸出檔案：{OUT}"
    )


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(f"ERROR: {exc}")

        raise
