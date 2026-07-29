from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JST = ZoneInfo("Asia/Tokyo")
NEW_YORK = ZoneInfo("America/New_York")
FRANKFURT = ZoneInfo("Europe/Berlin")
SYDNEY = ZoneInfo("Australia/Sydney")

OUTPUT = Path("central_banks.ics")

FED_URL = (
    "https://www.federalreserve.gov/"
    "monetarypolicy/fomccalendars.htm"
)

ECB_URL = (
    "https://www.ecb.europa.eu/"
    "press/calendars/mgcgc/html/index.en.html"
)

RBA_URL = (
    "https://www.rba.gov.au/"
    "schedules-events/board-meeting-schedules.html"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 central-bank-calendar-feed/1.0 "
        "https://github.com/maxliem/mxm-jgbs-cal"
    )
}


def fetch(url: str) -> str:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def escape_ics(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\n", r"\n")
    )


def fold_line(line: str, limit: int = 75) -> list[str]:
    if len(line.encode("utf-8")) <= limit:
        return [line]

    output: list[str] = []
    remaining = line

    while remaining:
        prefix = "" if not output else " "
        chunk = ""

        for char in remaining:
            candidate = prefix + chunk + char
            if len(candidate.encode("utf-8")) > limit:
                break
            chunk += char

        if not chunk:
            chunk = remaining[0]

        output.append(prefix + chunk)
        remaining = remaining[len(chunk):]

    return output


def normalise_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def stable_uid(
    bank: str,
    event_type: str,
    date: datetime,
) -> str:
    raw = (
        f"{bank}|{event_type}|"
        f"{date.astimezone(JST).isoformat()}"
    )
    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]

    return f"{digest}@mxm-central-banks"


def append_alarm(
    lines: list[str],
    minutes_before: int,
    description: str,
) -> None:
    lines.extend(
        [
            "BEGIN:VALARM",
            f"TRIGGER:-PT{minutes_before}M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{escape_ics(description)}",
            "END:VALARM",
        ]
    )


def append_event(
    calendar: list[str],
    *,
    bank: str,
    event_type: str,
    local_start: datetime,
    duration: timedelta,
    summary: str,
    source_url: str,
    alerts: tuple[int, ...],
) -> None:
    start_jst = local_start.astimezone(JST)
    end_jst = (
        local_start + duration
    ).astimezone(JST)

    event = [
        "BEGIN:VEVENT",
        (
            "UID:"
            f"{stable_uid(bank, event_type, local_start)}"
        ),
        (
            "DTSTAMP:"
            f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        ),
        (
            "DTSTART;TZID=Asia/Tokyo:"
            f"{start_jst:%Y%m%dT%H%M%S}"
        ),
        (
            "DTEND;TZID=Asia/Tokyo:"
            f"{end_jst:%Y%m%dT%H%M%S}"
        ),
        f"SUMMARY:{escape_ics(summary)}",
        (
            "DESCRIPTION:"
            f"{escape_ics('Official schedule converted to JST')}"
        ),
        f"URL:{source_url}",
    ]

    for minutes in alerts:
        append_alarm(
            event,
            minutes,
            summary,
        )

    event.append("END:VEVENT")

    for line in event:
        calendar.extend(fold_line(line))


def month_number(name: str) -> int:
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    return months[name.lower()]


def parse_fed() -> list[datetime]:
    soup = BeautifulSoup(fetch(FED_URL), "html.parser")
    meetings: list[datetime] = []

    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    headings = soup.find_all(
        ["h3", "h4"],
        string=re.compile(r"20\d{2}\s+FOMC Meetings", re.I),
    )

    for heading in headings:
        year_match = re.search(r"(20\d{2})", heading.get_text(" ", strip=True))
        if not year_match:
            continue

        year = int(year_match.group(1))

        if year < datetime.now(JST).year:
            continue

        current_month: int | None = None

        for node in heading.find_all_next():
            if node is not heading and node.name in {"h3", "h4"}:
                if re.search(
                    r"20\d{2}\s+FOMC Meetings",
                    node.get_text(" ", strip=True),
                    re.I,
                ):
                    break

            text = normalise_spaces(node.get_text(" ", strip=True))

            if text.lower() in months:
                current_month = months[text.lower()]
                continue

            if current_month is None:
                continue

            # Accept only a standalone two-day meeting range.
            match = re.fullmatch(
                r"(\d{1,2})\s*[-–]\s*(\d{1,2})\*?",
                text,
            )

            if not match:
                continue

            decision_day = int(match.group(2))

            try:
                decision = datetime(
                    year,
                    current_month,
                    decision_day,
                    14,
                    0,
                    tzinfo=NEW_YORK,
                )
            except ValueError:
                continue

            meetings.append(decision)
            current_month = None

    return sorted(set(meetings))

def parse_ecb() -> list[datetime]:
    """
    Select only ECB monetary-policy Day 2 rows that explicitly say
    'followed by press conference'.
    """
    soup = BeautifulSoup(fetch(ECB_URL), "html.parser")
    text = soup.get_text("\n", strip=True)

    lines = [
        normalise_spaces(line)
        for line in text.splitlines()
        if normalise_spaces(line)
    ]

    meetings: list[datetime] = []

    for index, line in enumerate(lines):
        if not re.fullmatch(r"\d{2}/\d{2}/20\d{2}", line):
            continue

        if index + 1 >= len(lines):
            continue

        description = lines[index + 1].lower()

        if "monetary policy meeting" not in description:
            continue

        if "followed by press conference" not in description:
            continue

        if "non-monetary" in description:
            continue

        date = datetime.strptime(line, "%d/%m/%Y")

        decision = datetime(
            date.year,
            date.month,
            date.day,
            14,
            15,
            tzinfo=FRANKFURT,
        )

        meetings.append(decision)

    return sorted(set(meetings))


def parse_rba() -> list[datetime]:
    """
    Parse only the Monetary Policy Board column and use the final
    day of each two-day meeting.
    """
    soup = BeautifulSoup(fetch(RBA_URL), "html.parser")
    meetings: list[datetime] = []

    for heading in soup.find_all(
        ["h2", "h3"],
        string=re.compile(r"Board meeting schedules 20\d{2}", re.I),
    ):
        year_match = re.search(r"(20\d{2})", heading.get_text(" ", strip=True))
        if not year_match:
            continue

        year = int(year_match.group(1))

        if year < datetime.now(JST).year:
            continue

        table = heading.find_next("table")
        if table is None:
            continue

        for row in table.select("tbody tr"):
            cells = [
                normalise_spaces(cell.get_text(" ", strip=True))
                for cell in row.find_all(["th", "td"])
            ]

            if len(cells) < 2:
                continue

            monetary_policy_cell = cells[1]

            match = re.search(
                r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+"
                r"(January|February|March|April|May|June|"
                r"July|August|September|October|November|December)",
                monetary_policy_cell,
                re.I,
            )

            if not match:
                continue

            decision_day = int(match.group(2))
            month = month_number(match.group(3))

            decision = datetime(
                year,
                month,
                decision_day,
                14,
                30,
                tzinfo=SYDNEY,
            )

            meetings.append(decision)

    return sorted(set(meetings))


def validate(
    fed: list[datetime],
    ecb: list[datetime],
    rba: list[datetime],
) -> None:
    current_year = datetime.now(JST).year

    fed_current = [x for x in fed if x.year == current_year]
    ecb_current = [x for x in ecb if x.year == current_year]
    rba_current = [x for x in rba if x.year == current_year]

    errors: list[str] = []

    if not 6 <= len(fed_current) <= 10:
        errors.append(
            f"Suspicious Fed count for {current_year}: "
            f"{len(fed_current)}"
        )

    if not 4 <= len(ecb_current) <= 10:
        errors.append(
            f"Suspicious ECB count for {current_year}: "
            f"{len(ecb_current)}"
        )

    if not 6 <= len(rba_current) <= 10:
        errors.append(
            f"Suspicious RBA count for {current_year}: "
            f"{len(rba_current)}"
        )

    for name, dates in {
        "Fed": fed,
        "ECB": ecb,
        "RBA": rba,
    }.items():
        if len(dates) != len(set(dates)):
            errors.append(f"Duplicate {name} dates detected")

    if errors:
        raise RuntimeError("; ".join(errors))


def build_calendar() -> None:
    fed = parse_fed()
    ecb = parse_ecb()
    rba = parse_rba()

    validate(fed, ecb, rba)

    calendar = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        (
            "PRODID:"
            "-//maxliem//Central Bank Feed//EN"
        ),
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Fed ECB RBA",
        "X-WR-TIMEZONE:Asia/Tokyo",
        "X-PUBLISHED-TTL:PT6H",
        (
            "REFRESH-INTERVAL;"
            "VALUE=DURATION:PT6H"
        ),
    ]

    for decision in fed:
        append_event(
            calendar,
            bank="Fed",
            event_type="decision",
            local_start=decision,
            duration=timedelta(minutes=30),
            summary="Fed decision",
            source_url=FED_URL,
            alerts=(30, 5),
        )

        append_event(
            calendar,
            bank="Fed",
            event_type="press-conference",
            local_start=(
                decision + timedelta(minutes=30)
            ),
            duration=timedelta(hours=1),
            summary="Fed press conference",
            source_url=FED_URL,
            alerts=(15,),
        )

    for decision in ecb:
        append_event(
            calendar,
            bank="ECB",
            event_type="decision",
            local_start=decision,
            duration=timedelta(minutes=30),
            summary="ECB decision",
            source_url=ECB_URL,
            alerts=(30, 5),
        )

        append_event(
            calendar,
            bank="ECB",
            event_type="press-conference",
            local_start=(
                decision + timedelta(minutes=30)
            ),
            duration=timedelta(hours=1),
            summary="ECB press conference",
            source_url=ECB_URL,
            alerts=(15,),
        )

    for decision in rba:
        append_event(
            calendar,
            bank="RBA",
            event_type="decision",
            local_start=decision,
            duration=timedelta(minutes=30),
            summary="RBA decision",
            source_url=RBA_URL,
            alerts=(30, 5),
        )

        append_event(
            calendar,
            bank="RBA",
            event_type="media-conference",
            local_start=(
                decision + timedelta(hours=1)
            ),
            duration=timedelta(hours=1),
            summary="RBA media conference",
            source_url=RBA_URL,
            alerts=(15,),
        )

    calendar.append("END:VCALENDAR")

    OUTPUT.write_text(
        "\r\n".join(calendar) + "\r\n",
        encoding="utf-8",
    )

    print(
        f"Fed meetings: {len(fed)}"
    )
    print(
        f"ECB meetings: {len(ecb)}"
    )
    print(
        f"RBA meetings: {len(rba)}"
    )
    print(
        f"Created: {OUTPUT}"
    )


if __name__ == "__main__":
    build_calendar()
