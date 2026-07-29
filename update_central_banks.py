#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Alarm, Calendar, Event


# ============================================================
# Configuration
# ============================================================

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

OUTPUT_FILE = Path("central_banks.ics")

NEW_YORK = ZoneInfo("America/New_York")
FRANKFURT = ZoneInfo("Europe/Berlin")
SYDNEY = ZoneInfo("Australia/Sydney")

USER_AGENT = (
    "Mozilla/5.0 (compatible; CentralBankCalendarBot/1.0; "
    "+https://github.com/maxliem/mxm-jgbs-cal)"
)

REQUEST_TIMEOUT = 30

MONTHS = {
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


# ============================================================
# HTTP helpers
# ============================================================

def fetch(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.text


def normalise_spaces(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = value.replace("\u202f", " ")
    return re.sub(r"\s+", " ", value).strip()


def unique_sorted(
    meetings: Iterable[datetime],
) -> list[datetime]:
    return sorted(set(meetings))


# ============================================================
# Fed parser
# ============================================================

def parse_fed() -> list[datetime]:
    """
    Parse official FOMC meeting ranges.

    Only date ranges such as January 27-28 are accepted.
    The decision occurs on the second day of the meeting.
    """
    soup = BeautifulSoup(fetch(FED_URL), "html.parser")
    text = normalise_spaces(soup.get_text(" ", strip=True))

    meetings: list[datetime] = []

    # Split at year headings such as:
    # 2026 FOMC Meetings
    year_matches = list(
        re.finditer(
            r"\b(20\d{2})\s+FOMC\s+Meetings\b",
            text,
            flags=re.I,
        )
    )

    for index, year_match in enumerate(year_matches):
        year = int(year_match.group(1))

        section_start = year_match.end()

        if index + 1 < len(year_matches):
            section_end = year_matches[index + 1].start()
        else:
            section_end = len(text)

        section = text[section_start:section_end]

        # Examples:
        # January 27-28
        # March 17-18*
        # October 27–28
        pattern = re.compile(
            r"\b("
            + "|".join(MONTHS.keys())
            + r")\b"
            r"\s+"
            r"(\d{1,2})"
            r"\s*[\-–—]\s*"
            r"(\d{1,2})"
            r"(?:\s*\*)?",
            flags=re.I,
        )

        for match in pattern.finditer(section):
            month = MONTHS[match.group(1).lower()]
            second_day = int(match.group(3))

            try:
                decision = datetime(
                    year,
                    month,
                    second_day,
                    14,
                    0,
                    tzinfo=NEW_YORK,
                )
            except ValueError:
                continue

            meetings.append(decision)

    meetings = unique_sorted(meetings)

    print("Fed parsed dates:", flush=True)
    for meeting in meetings:
        print(f"  {meeting.date()}", flush=True)

    return meetings


# ============================================================
# ECB parser
# ============================================================

def parse_ecb() -> list[datetime]:
    """
    Parse ECB monetary-policy meetings.

    Only entries containing all of the following are accepted:
      - monetary policy meeting
      - Day 2
      - followed by press conference

    ECB decision time: 14:15 Frankfurt time.
    """
    soup = BeautifulSoup(fetch(ECB_URL), "html.parser")
    text = normalise_spaces(soup.get_text(" ", strip=True))

    meetings: list[datetime] = []

    entries = re.split(
        r"(?=(?:0[1-9]|[12]\d|3[01])/"
        r"(?:0[1-9]|1[0-2])/20\d{2})",
        text,
    )

    for entry in entries:
        date_match = re.match(
            r"(\d{2}/\d{2}/20\d{2})",
            entry,
        )

        if not date_match:
            continue

        description = entry[:700].lower()

        if "monetary policy meeting" not in description:
            continue

        if "day 2" not in description:
            continue

        if "followed by press conference" not in description:
            continue

        if "non-monetary" in description:
            continue

        parsed_date = datetime.strptime(
            date_match.group(1),
            "%d/%m/%Y",
        )

        decision = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            14,
            15,
            tzinfo=FRANKFURT,
        )

        meetings.append(decision)

    meetings = unique_sorted(meetings)

    print("ECB parsed dates:", flush=True)
    for meeting in meetings:
        print(f"  {meeting.date()}", flush=True)

    return meetings


# ============================================================
# RBA parser
# ============================================================

def parse_rba() -> list[datetime]:
    """
    Parse official RBA Monetary Policy Board meeting ranges.

    Only the Monetary Policy Board date ranges are used.
    The decision occurs on the second day at 14:30 Sydney time.
    """
    soup = BeautifulSoup(fetch(RBA_URL), "html.parser")

    meetings: list[datetime] = []

    # Preferred method: parse the official HTML tables.
    for table in soup.find_all("table"):
        table_text = normalise_spaces(
            table.get_text(" ", strip=True)
        ).lower()

        if "monetary policy board" not in table_text:
            continue

        year = find_nearest_rba_year(table)

        if year is None:
            continue

        for row in table.find_all("tr"):
            cells = [
                normalise_spaces(
                    cell.get_text(" ", strip=True)
                )
                for cell in row.find_all(["th", "td"])
            ]

            if len(cells) < 2:
                continue

            # Column 0 = month
            # Column 1 = Monetary Policy Board
            monetary_policy_cell = cells[1]

            meeting_date = parse_rba_range(
                monetary_policy_cell,
                year,
            )

            if meeting_date is not None:
                meetings.append(meeting_date)

    # Fallback for changes in the RBA HTML/table structure.
    if not meetings:
        text = normalise_spaces(
            soup.get_text(" ", strip=True)
        )
        meetings = parse_rba_from_text(text)

    meetings = unique_sorted(meetings)

    print("RBA parsed dates:", flush=True)
    for meeting in meetings:
        print(f"  {meeting.date()}", flush=True)

    return meetings


def find_nearest_rba_year(table) -> int | None:
    """
    Find a heading before the table containing:
    Board meeting schedules YYYY
    """
    heading = table.find_previous(
        ["h1", "h2", "h3", "h4", "caption"]
    )

    while heading is not None:
        heading_text = normalise_spaces(
            heading.get_text(" ", strip=True)
        )

        match = re.search(
            r"Board meeting schedules\s+(20\d{2})",
            heading_text,
            flags=re.I,
        )

        if match:
            return int(match.group(1))

        heading = heading.find_previous(
            ["h1", "h2", "h3", "h4", "caption"]
        )

    # Sometimes the year is inside a caption or nearby text.
    nearby_text = normalise_spaces(
        table.get_text(" ", strip=True)
    )

    match = re.search(r"\b(20\d{2})\b", nearby_text)

    if match:
        return int(match.group(1))

    return None


def parse_rba_range(
    value: str,
    year: int,
) -> datetime | None:
    """
    Parse values such as:
      2–3 February
      16-17 March
    """
    match = re.search(
        r"(?<!\d)"
        r"(\d{1,2})"
        r"\s*[\-–—]\s*"
        r"(\d{1,2})"
        r"\s+"
        r"("
        + "|".join(MONTHS.keys())
        + r")"
        r"\b",
        value,
        flags=re.I,
    )

    if not match:
        return None

    second_day = int(match.group(2))
    month = MONTHS[match.group(3).lower()]

    try:
        return datetime(
            year,
            month,
            second_day,
            14,
            30,
            tzinfo=SYDNEY,
        )
    except ValueError:
        return None


def parse_rba_from_text(
    text: str,
) -> list[datetime]:
    meetings: list[datetime] = []

    year_matches = list(
        re.finditer(
            r"Board meeting schedules\s+(20\d{2})",
            text,
            flags=re.I,
        )
    )

    for index, year_match in enumerate(year_matches):
        year = int(year_match.group(1))

        section_start = year_match.end()

        if index + 1 < len(year_matches):
            section_end = year_matches[index + 1].start()
        else:
            section_end = len(text)

        section = text[section_start:section_end]

        # Each row should normally contain:
        # Month | Monetary Policy Board | Payments System Board
        for match in re.finditer(
            r"(?<!\d)"
            r"(\d{1,2})"
            r"\s*[\-–—]\s*"
            r"(\d{1,2})"
            r"\s+"
            r"("
            + "|".join(MONTHS.keys())
            + r")"
            r"\b",
            section,
            flags=re.I,
        ):
            second_day = int(match.group(2))
            month = MONTHS[match.group(3).lower()]

            try:
                decision = datetime(
                    year,
                    month,
                    second_day,
                    14,
                    30,
                    tzinfo=SYDNEY,
                )
            except ValueError:
                continue

            meetings.append(decision)

    return meetings


# ============================================================
# Validation
# ============================================================

def validate(
    fed: list[datetime],
    ecb: list[datetime],
    rba: list[datetime],
) -> None:
    """
    Prevent obviously broken scrapes from overwriting the calendar.

    Important:
    - Official websites may remove past meetings.
    - Future-year schedules may initially be incomplete.
    - Therefore, partial years are allowed.
    """
    errors: list[str] = []

    calendars = {
        "Fed": fed,
        "ECB": ecb,
        "RBA": rba,
    }

    today = datetime.now(UTC).date()

    for name, meetings in calendars.items():
        if not meetings:
            errors.append(f"{name}: no meetings parsed")
            continue

        if len(meetings) != len(set(meetings)):
            errors.append(f"{name}: duplicate meetings detected")

        # Catch absurdly large results caused by matching unrelated dates.
        if len(meetings) > 40:
            errors.append(
                f"{name}: suspicious total count "
                f"{len(meetings)}"
            )

        yearly_counts = Counter(
            meeting.year
            for meeting in meetings
        )

        for year, count in sorted(yearly_counts.items()):
            # A complete year normally has fewer than 11 meetings.
            # Partial schedules with 1–5 dates are valid.
            if count > 10:
                errors.append(
                    f"Suspicious {name} count "
                    f"for {year}: {count}"
                )

        future_or_today = [
            meeting
            for meeting in meetings
            if meeting.date() >= today
        ]

        if not future_or_today:
            errors.append(
                f"{name}: no current or future meetings parsed"
            )

    # Additional RBA protection:
    # the fallback must not accidentally parse Payments System
    # Board dates as extra monetary-policy meetings.
    rba_year_counts = Counter(
        meeting.year
        for meeting in rba
    )

    for year, count in sorted(rba_year_counts.items()):
        if count > 8:
            errors.append(
                f"RBA has more than 8 meetings "
                f"for {year}: {count}"
            )

    if errors:
        raise RuntimeError("; ".join(errors))


# ============================================================
# ICS helpers
# ============================================================

def stable_uid(
    bank: str,
    event_type: str,
    start: datetime,
) -> str:
    raw = (
        f"{bank}|{event_type}|"
        f"{start.isoformat()}"
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]

    return (
        f"{bank.lower()}-"
        f"{event_type.lower().replace(' ', '-')}-"
        f"{digest}@mxm-jgbs-cal"
    )


def add_alarm(
    event: Event,
    before: timedelta,
    description: str,
) -> None:
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", description)
    alarm.add("trigger", -before)
    event.add_component(alarm)


def add_event(
    calendar: Calendar,
    *,
    bank: str,
    event_type: str,
    summary: str,
    start: datetime,
    duration: timedelta,
    source_url: str,
) -> None:
    event = Event()

    event.add(
        "uid",
        stable_uid(
            bank,
            event_type,
            start,
        ),
    )

    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", start + duration)
    event.add("dtstamp", datetime.now(UTC))

    event.add(
        "description",
        f"Official source: {source_url}",
    )

    event.add("url", source_url)
    event.add("status", "CONFIRMED")
    event.add("transp", "TRANSPARENT")

    add_alarm(
        event,
        timedelta(hours=3),
        f"{summary} in 3 hours",
    )

    add_alarm(
        event,
        timedelta(minutes=5),
        f"{summary} in 5 minutes",
    )

    calendar.add_component(event)


# ============================================================
# Calendar generation
# ============================================================

def build_calendar() -> None:
    fed = parse_fed()
    ecb = parse_ecb()
    rba = parse_rba()

    validate(fed, ecb, rba)

    calendar = Calendar()

    calendar.add("prodid", "-//MXM Central Banks Calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")

    calendar.add(
        "x-wr-calname",
        "Central Bank Meetings",
    )

    calendar.add(
        "x-wr-timezone",
        "Asia/Tokyo",
    )

    calendar.add(
        "refresh-interval",
        timedelta(hours=12),
    )

    calendar.add(
        "x-published-ttl",
        timedelta(hours=12),
    )

    # Fed:
    # Decision 14:00 New York
    # Press conference 14:30 New York
    for decision in fed:
        add_event(
            calendar,
            bank="Fed",
            event_type="decision",
            summary="Fed decision",
            start=decision,
            duration=timedelta(minutes=20),
            source_url=FED_URL,
        )

        add_event(
            calendar,
            bank="Fed",
            event_type="press conference",
            summary="Fed press conference",
            start=decision + timedelta(minutes=30),
            duration=timedelta(hours=1),
            source_url=FED_URL,
        )

    # ECB:
    # Decision 14:15 Frankfurt
    # Press conference 14:45 Frankfurt
    for decision in ecb:
        add_event(
            calendar,
            bank="ECB",
            event_type="decision",
            summary="ECB decision",
            start=decision,
            duration=timedelta(minutes=20),
            source_url=ECB_URL,
        )

        add_event(
            calendar,
            bank="ECB",
            event_type="press conference",
            summary="ECB press conference",
            start=decision + timedelta(minutes=30),
            duration=timedelta(hours=1),
            source_url=ECB_URL,
        )

    # RBA:
    # Decision 14:30 Sydney
    # Media conference 15:30 Sydney
    for decision in rba:
        add_event(
            calendar,
            bank="RBA",
            event_type="decision",
            summary="RBA decision",
            start=decision,
            duration=timedelta(minutes=20),
            source_url=RBA_URL,
        )

        add_event(
            calendar,
            bank="RBA",
            event_type="media conference",
            summary="RBA media conference",
            start=decision + timedelta(hours=1),
            duration=timedelta(hours=1),
            source_url=RBA_URL,
        )

    OUTPUT_FILE.write_bytes(calendar.to_ical())

    print(
        f"Wrote {OUTPUT_FILE} with "
        f"{len(fed) * 2 + len(ecb) * 2 + len(rba) * 2} events.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        build_calendar()
    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise
