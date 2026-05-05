import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

BASE = "https://www.mof.go.jp/english/policy/jgbs/auction/calendar/"
MONTHS = [f"26{m:02d}" for m in range(1, 13)]

TENOR_PATTERNS = [
    ("2Y", "2-year"),
    ("5Y", "5-year"),
    ("10Y", "10-year"),
    ("20Y", "20-year"),
    ("30Y", "30-year"),
    ("40Y", "40-year"),
]

events = []


def fetch(url):
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def esc(text):
    return (
        text.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def extract_series(text):
    # Official Japanese style: （第173回） or (第173回)
    m = re.search(r"[（(]\s*第\s*(\d+)\s*回\s*[)）]", text)
    if m:
        return m.group(1)

    # Fallback: plain parenthesis number, e.g. (173)
    m = re.search(r"\((\d{2,4})\)", text)
    if m:
        return m.group(1)

    return None


def classify(text, tenor_short):
    t = text.lower()
    series = extract_series(text)

    if "liquidity enhancement" in t:
        base = f"{tenor_short} Liquidity Enhancement"
    elif "climate" in t or "transition" in t or "green" in t:
        base = f"{tenor_short} Green JGB auction"
    else:
        base = f"{tenor_short} JGB auction"

    if series:
        return f"{base} (#{series})"
    return base


def parse_page(code, suffix):
    url = f"{BASE}{code}{suffix}.htm"
    html = fetch(url)
    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")
    month = int(code[2:4])

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        text = " ".join(c.get_text(" ", strip=True) for c in cells)
        text_l = text.lower()

        if not text.strip():
            continue

        day_match = re.search(r"\b(\d{1,2})\b", text)
        if not day_match:
            continue

        day = int(day_match.group(1))

        for tenor_short, tenor_long in TENOR_PATTERNS:
            if tenor_long in text_l:
                dt = datetime(2026, month, day, 12, 35, tzinfo=JST)
                summary = classify(text, tenor_short)
                events.append((dt, summary, url, text))


def build_ics():
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JGB Auction Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:MOF JGB Auctions",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]

    seen = set()

    for dt, summary, url, raw_text in sorted(events, key=lambda x: (x[0], x[1])):
        key = (dt.strftime("%Y%m%d"), summary)
        if key in seen:
            continue
        seen.add(key)

        end = dt + timedelta(minutes=30)
        uid = f"{dt.strftime('%Y%m%d')}-{re.sub(r'[^A-Za-z0-9]', '', summary)}@jgb-feed"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID=Asia/Tokyo:{dt.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Tokyo:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{esc(summary)}",
            f"DESCRIPTION:{esc(raw_text)}",
            f"URL:{url}",
            "BEGIN:VALARM",
            "TRIGGER:-PT3H",
            "ACTION:DISPLAY",
            "DESCRIPTION:JGB auction in 3h",
            "END:VALARM",
            "BEGIN:VALARM",
            "TRIGGER:-PT5M",
            "ACTION:DISPLAY",
            "DESCRIPTION:JGB auction in 5m",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")

    with open("calendar.ics", "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")


for code in MONTHS:
    parse_page(code, "e")
    parse_page(code, "ae")

build_ics()
