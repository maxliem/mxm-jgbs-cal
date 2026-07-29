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
    html = fetch(FED_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = normalise_spaces(
        soup.get_text(" ", strip=True)
    )

    meetings: list[datetime] = []

    # Searches for patterns such as:
    # January 27-28 2026
    # March 17-18*
    pattern = re.compile(
        r"""
        \b
        (January|February|March|April|May|June|
         July|August|September|October|November|
         December)
        \s+
        (\d{1,2})
        \s*[-–]\s*
        (\d{1,2})
        \*?
        (?:\s*,?\s*(2026|2027|2028))?
        \b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    current_year = 2026

    for match in pattern.finditer(text):
        month_name = match.group(1)
        end_day = int(match.group(3))
        explicit_year = match.group(4)

        if explicit_year:
            current_year = int(explicit_year)

        if current_year < 2026:
            continue

        decision = datetime(
            current_year,
            month_number(month_name),
            end_day,
            14,
            0,
            tzinfo=NEW_YORK,
        )

        if decision not in meetings:
            meetings.append(decision)

    return sorted(meetings)


def parse_ecb() -> list[datetime]:
    html = fetch(ECB_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = normalise_spaces(
        soup.get_text(" ", strip=True)
    )

    meetings: list[datetime] = []

    # The official ECB page places a date near:
    # "monetary policy meeting ... Day 2,
    # followed by press conference"
    pattern = re.compile(
        r"""
        \b
        (\d{1,2})[./]
        (\d{1,2})[./]
        (20\d{2})
        \b
        (?:
            .{0,300}?
        )
        monetary\ policy\ meeting
        (?:
            .{0,200}?
        )
        followed\ by\ press\ conference
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    for match in pattern.finditer(text):
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        decision = datetime(
            year,
            month,
            day,
            14,
            15,
            tzinfo=FRANKFURT,
        )

        if decision not in meetings:
            meetings.append(decision)

    return sorted(meetings)


def parse_rba() -> list[datetime]:
    html = fetch(RBA_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = normalise_spaces(
        soup.get_text(" ", strip=True)
    )

    meetings: list[datetime] = []

    # Examples:
    # February 2–3 February
    # March 16–17 March
    pattern = re.compile(
        r"""
        \b
        (January|February|March|April|May|June|
         July|August|September|October|November|
         December)
        \s+
        (\d{1,2})
        \s*[-–]\s*
        (\d{1,2})
        (?:\s+
            (January|February|March|April|May|June|
             July|August|September|October|November|
             December)
        )?
        (?:\s+(20\d{2}))?
        \b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    for match in pattern.finditer(text):
        first_month = match.group(1)
        end_day = int(match.group(3))
        repeated_month = match.group(4)
        year = int(match.group(5) or 2026)

        actual_month = (
            repeated_month or first_month
        )

        decision = datetime(
            year,
            month_number(actual_month),
            end_day,
            14,
            30,
            tzinfo=SYDNEY,
        )

        if decision not in meetings:
            meetings.append(decision)

    return sorted(meetings)


def validate(
    fed: list[datetime],
    ecb: list[datetime],
    rba: list[datetime],
) -> None:
    errors: list[str] = []

    if not fed:
        errors.append(
            "No Fed meetings were found."
        )

    if not ecb:
        errors.append(
            "No ECB meetings were found."
        )

    if not rba:
        errors.append(
            "No RBA meetings were found."
        )

    if errors:
        raise RuntimeError(" ".join(errors))


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
