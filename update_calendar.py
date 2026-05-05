import re, json, requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
BASE = "https://www.mof.go.jp/english/policy/jgbs/auction/calendar/"
MONTHS = [f"26{m:02d}" for m in range(1, 13)]
TENORS = ["2-year", "5-year", "10-year", "20-year", "30-year", "40-year"]

events = []

def fetch(url):
    r = requests.get(url, timeout=20)
    return r.text if r.status_code == 200 else None

def parse_page(code, suffix):
    url = f"{BASE}{code}{suffix}.htm"
    html = fetch(url)
    if not html:
        return

    soup = BeautifulSoup(html, "html.parser")

    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")

        for row in rows:
            cols = row.find_all(["td","th"])
            text = " ".join([c.get_text(" ", strip=True) for c in cols])

            for tenor in TENORS:
                if tenor in text.lower():
                    m = re.search(r"(\d{1,2})", text)
                    if not m:
                        continue

                    day = int(m.group(1))
                    month = int(code[2:4])

                    dt = datetime(2026, month, day, 12, 35, tzinfo=JST)
                    events.append((dt, tenor, url))

def build_ics():
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JGB Feed//EN",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]

    seen = set()

    for i,(dt,tenor,url) in enumerate(sorted(events)):
        key = (dt.strftime("%Y%m%d"), tenor)
        if key in seen:
            continue
        seen.add(key)

        end = dt + timedelta(minutes=30)

        lines += [
            "BEGIN:VEVENT",
            f"UID:{i}@jgb",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID=Asia/Tokyo:{dt.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Asia/Tokyo:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{tenor.upper()} JGB auction",
            f"URL:{url}",
            "BEGIN:VALARM",
            "TRIGGER:-PT3H",
            "ACTION:DISPLAY",
            "DESCRIPTION:Auction in 3h",
            "END:VALARM",
            "BEGIN:VALARM",
            "TRIGGER:-PT5M",
            "ACTION:DISPLAY",
            "DESCRIPTION:Auction in 5m",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    open("calendar.ics","w").write("\r\n".join(lines))

for m in MONTHS:
    parse_page(m,"e")
    parse_page(m,"ae")

build_ics()
