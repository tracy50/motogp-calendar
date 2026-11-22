#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MotoGP Calendar 3.0

功能：
- 自動取得 MotoGP 賽季、分站及場次
- 只收錄 MotoGP Sprint (SPR) 與 Race (RAC)
- 使用官方賽程頁核對分站日期，API 提供場次時刻
- 保留 API 明確提供的時區資訊
- API 沒有時區時，使用賽道當地時間
- 轉換成 UTC ICS，Apple Calendar 自動顯示台灣時間
- 每個事件提前 30 分鐘提醒
- 官方賽程比對失敗或驗證失敗時，不覆蓋舊 ICS
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
    "catalonia", "portuguese", "portugal", "valencia",
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
    "grand prix of portugal": "portugal",
    "portuguese": "portugal",
    "portimao": "portugal",
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
    "grand prix of valencia": "valencia",
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
    """Parse only official GP rows; never infer a round from nearby prose."""
    row_pattern = re.compile(
        r"\bGP\s*(?P<round>\d{1,2})\s+"
        r"(?P<label>[A-Z][A-Z '&-]*?)\s+"
        r"(?P<d1>\d{1,2})\s+(?:(?P<month1>[A-Za-z]{3,9})\s*)?"
        r"[-–]\s*(?P<d2>\d{1,2})\s+"
        r"(?P<month2>[A-Za-z]{3,9})\s+(?P<year>\d{4})",
        re.IGNORECASE,
    )
    rounds = {}
    for match in row_pattern.finditer(page_text):
        try:
            month2 = MONTHS[match.group("month2").lower()]
            month1 = MONTHS[(match.group("month1") or match.group("month2")).lower()]
            row_year = int(match.group("year"))
            start = date(row_year, month1, int(match.group("d1")))
            end_year = row_year + (month2 < month1)
            end = date(end_year, month2, int(match.group("d2")))
            if not 1 <= (end - start).days <= 4:
                continue
            label = norm(match.group("label"))
            previous = rounds.get(label)
            current = (start, end, f"<<<DATE>>>{label}")
            if previous and previous[:2] != current[:2]:
                raise RuntimeError(f"官方賽程同一站日期衝突：{label}")
            rounds[label] = current
        except (KeyError, ValueError):
            continue

    if len(rounds) < MIN_OFFICIAL_ROUNDS:
        raise RuntimeError(
            f"官方 {year} 賽程 GP 列解析不足：{len(rounds)} 站；"
            "停止更新以保留舊 ICS。"
        )
    return sorted(rounds.values())

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

    # Circuit identity is stable when an API event name or country is
    # stale. Resolve it before sponsored/event labels.
    circuit_n = norm(circuit)
    for hint, label in ROUND_LABEL_HINTS.items():
        if norm(hint) in circuit_n:
            return [norm(label)]

    combined = norm(circuit + " " + event_text)

    for hint, label in ROUND_LABEL_HINTS.items():

        if norm(hint) in combined:
            return [norm(label)]

    event_labels = [
        label
        for label in KNOWN_OFFICIAL_LABELS
        if re.search(rf"\b{re.escape(norm(label))}\b", combined)
    ]

    if len(event_labels) == 1:
        return event_labels

    labels = [norm(country)]

    if country in (
        "United States",
        "United States of America",
        "USA"
    ):
        labels.append("usa")

    if country in ("Czech Republic", "Czechia"):
        labels.append("czechia")

    if country == "Portugal":
        labels.append("portuguese")

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

        parts = context.split("<<<DATE>>>", 1)

        if len(parts) != 2:
            continue

        before = norm(parts[0])
        after = norm(parts[1])
        distances = []

        for label in labels:

            label_n = norm(label)

            if not label_n:
                continue

            pos_before = before.rfind(label_n)

            if pos_before >= 0:
                distances.append(
                    len(before) - pos_before - len(label_n)
                )

            pos_after = after.find(label_n)

            if pos_after >= 0:
                distances.append(pos_after)

        if distances:
            matches.append((min(distances), start, end))

    if not matches:
        year = ranges[0][0].year if ranges else int(event.get("year") or 0)
        start, end = fetch_official_event_weekend(event, year)
        ranges.append(
            (start, end, f"<<<DATE>>>{event_official_labels(event)[0]}")
        )
        return start, end

    closest = min(distance for distance, _, _ in matches)
    best_dates = {
        (start, end)
        for distance, start, end in matches
        if distance == closest
    }

    if len(best_dates) != 1:
        raise RuntimeError(
            f"官方賽程日期比對不唯一：{best_dates}"
        )

    return next(iter(best_dates))


def fetch_official_event_weekend(event, year):
    """Fallback to the official event page when the season index omits a GP."""
    labels = event_official_labels(event)
    event_id = event.get("id")
    if len(labels) != 1 or not event_id or not year:
        raise RuntimeError(
            f"無法建立官方分站頁面查詢：{event.get('name')}"
        )

    slug = labels[0].replace(" ", "-")
    url = f"https://www.motogp.com/en/calendar/{year}/event/{slug}/{event_id}"
    raw = request_bytes(url, {"Accept": "text/html"})
    parser = TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    page_text = parser.text()

    date_pattern = re.compile(
        r"(?P<d1>\d{1,2})\s+(?:(?P<m1>[A-Za-z]{3,9})\s*)?"
        r"[-–]\s*(?P<d2>\d{1,2})\s+"
        r"(?P<m2>[A-Za-z]{3,9})\s+(?P<year>\d{4})"
    )
    label_pattern = re.compile(rf"\b{re.escape(labels[0])}\b", re.IGNORECASE)
    label_positions = [m.start() for m in label_pattern.finditer(page_text)]
    candidates = []

    for match in date_pattern.finditer(page_text):
        try:
            row_year = int(match.group("year"))
            if row_year != year:
                continue
            month2 = MONTHS[match.group("m2").lower()]
            month1 = MONTHS[(match.group("m1") or match.group("m2")).lower()]
            start = date(row_year, month1, int(match.group("d1")))
            end = date(row_year + (month2 < month1), month2, int(match.group("d2")))
            if not 1 <= (end - start).days <= 4:
                continue
            distance = min((abs(pos - match.start()) for pos in label_positions), default=10**9)
            if distance <= 250:
                candidates.append((distance, start, end))
        except (KeyError, ValueError):
            continue

    if not candidates:
        raise RuntimeError(
            f"官方賽程索引未列出 {labels[0]}，且官方分站頁找不到日期："
            f"{event.get('name')}；停止更新以保留舊 ICS。"
        )

    nearest = min(distance for distance, _, _ in candidates)
    dates = {(start, end) for distance, start, end in candidates if distance == nearest}
    if len(dates) != 1:
        raise RuntimeError(
            f"官方分站頁日期比對不唯一：{labels[0]} {dates}"
        )
    return next(iter(dates))


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

    labels = event_official_labels(event)
    label_to_timezone = {
        "qatar": "Asia/Qatar", "malaysia": "Asia/Kuala_Lumpur",
        "portugal": "Europe/Lisbon", "valencia": "Europe/Madrid",
        "thailand": "Asia/Bangkok", "brazil": "America/Sao_Paulo",
        "usa": "America/Chicago", "spain": "Europe/Madrid",
        "france": "Europe/Paris", "italy": "Europe/Rome",
        "hungary": "Europe/Budapest", "czechia": "Europe/Prague",
        "netherlands": "Europe/Amsterdam", "germany": "Europe/Berlin",
        "great britain": "Europe/London", "austria": "Europe/Vienna",
        "japan": "Asia/Tokyo", "indonesia": "Asia/Makassar",
        "australia": "Australia/Melbourne", "san marino": "Europe/Rome",
        "argentina": "America/Argentina/Buenos_Aires",
    }
    tz_name = next((label_to_timezone[x] for x in labels if x in label_to_timezone), None) or TZ.get(country)

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

    # MotoGP schedule API's `date` is a venue-local wall-clock value;
    # its serialized Z/offset must not be treated as the session's UTC
    # instant. Keep the displayed date and clock, then attach the circuit
    # timezone exactly once when creating the calendar event.
    return parsed.replace(tzinfo=None), track_tz


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

    labels = event_official_labels(event)
    label_to_title = {
        "thailand": "泰國站", "brazil": "巴西站", "argentina": "阿根廷站",
        "usa": "美國站", "spain": "西班牙站", "france": "法國站",
        "italy": "義大利站", "hungary": "匈牙利站", "czechia": "捷克站",
        "netherlands": "荷蘭站", "germany": "德國站",
        "great britain": "英國站", "austria": "奧地利站",
        "japan": "日本站", "indonesia": "印尼站", "australia": "澳洲站",
        "malaysia": "馬來西亞站", "qatar": "卡達站",
        "portugal": "葡萄牙站", "valencia": "瓦倫西亞站",
        "catalonia": "加泰隆尼亞站", "aragon": "亞拉岡站",
        "san marino": "聖馬利諾站",
    }
    for label in labels:
        if label in label_to_title:
            return label_to_title[label]

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
    seen_round_sessions = set()
    seen_rounds = set()
    seen_weekends = set()
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

        weekend_start, weekend_end = match_official_weekend(
            event,
            official_ranges
        )
        seen_weekends.add((weekend_start, weekend_end))

        matched_event_count += 1
        round_labels = event_official_labels(event)
        if len(round_labels) != 1:
            raise RuntimeError(
                f"賽事無法唯一識別：{event.get('name')}；停止更新。"
            )
        round_key = round_labels[0]
        seen_rounds.add(round_key)

        for session in relevant:

            session_type = str(
                session.get("type", "")
            ).upper()

            round_session_key = (round_key, session_type)
            if round_session_key in seen_round_sessions:
                print(f"忽略 API 重複場次：{round_key} / {session_type}")
                continue

            parsed_local, track_tz = (
                session_local_datetime(
                    session.get("date"),
                    event
                )
            )

            local_start = parsed_local.replace(tzinfo=track_tz)

            # Keep the API's actual session date (including a one-day delay).
            # The official GP weekend is a guardrail, never a replacement
            # date. Larger shifts must also appear on the official calendar;
            # otherwise fail closed and retain the previous ICS.
            if not (
                weekend_start - timedelta(days=1)
                <= local_start.date()
                <= weekend_end + timedelta(days=1)
            ):
                raise RuntimeError(
                    f"場次日期 {local_start.date()} 與官網週末 "
                    f"{weekend_start}–{weekend_end} 相差超過一天："
                    f"{event.get('name')}；"
                    "停止更新以保留舊 ICS。"
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
            seen_round_sessions.add(round_session_key)

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

    if (
        matched_event_count == 0
        or len(output) < 2
        or len(seen_rounds) < MIN_OFFICIAL_ROUNDS
        or len(seen_weekends) != len(official_ranges)
    ):
        raise RuntimeError(
            f"官方 API 場次不足或分站比對不全（識別站數：{len(seen_rounds)}；"
            f"已配對週末：{len(seen_weekends)}/{len(official_ranges)}），"
            "停止更新以保護舊 ICS。"
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

