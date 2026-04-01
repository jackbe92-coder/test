"""
form_parser.py — Parse race form input into a structured runner list.

Supports two primary input modes:
  1. PDF file path   e.g. "Hobart Harness 15-03-2026.pdf"  → parse real race form
  2. Free-text block e.g. pasted form text                  → regex line parser

PDF format expected (Tasmanian harness race guide):
  Header:  "Hobart Harness — Sun 15 Mar, 2026"
  Race:    "R1 — Race Name HH:MM | NR 60 to 64 | 2090m"
  Runner:  "1  Horse Name  D: Driver T: Trainer  91249  3-1-1-0  01:57:00  NR61"

The --race flag selects which race from the PDF (1-indexed).
If omitted, the program lists available races and asks you to specify one.

Returns a RaceInfo object containing a list of Runner dataclasses.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import pandas as pd

from sim_config import VENUE_NORMALISE

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Runner:
    horse: str
    slug: str
    barrier: int
    driver: str = ""
    trainer: str = ""
    nr: float = 0.0         # Operative NR (adjusted if A-value present, else raw)
    nr_raw: float = 0.0     # Original NR before adjustment
    nr_adjusted: float = 0.0  # Adjusted NR (A-value) if present, else 0.0
    tab_no: int = 0
    sp: float = 0.0         # Market SP (if known)
    form_string: str = ""   # Recent form digits e.g. "78768"
    barrier_row: str = ""   # "FR" or "SR"
    handicap_m: int = 0     # Standing start handicap in metres (0, 10, or 20)
    is_front_tape: bool = False  # True if HCP == "FT"
    gear_changes: List[str] = field(default_factory=list)  # ["Blinkers Hood ON", ...]
    odm_mobile: bool = False    # Out of draw in mobile starts
    odm_standing: bool = False  # Out of draw in standing starts
    is_conditional: bool = False  # Driver has (C) or (C,5) suffix
    scratched: bool = False     # Horse is withdrawn


@dataclass
class RaceInfo:
    track: str = ""
    date: str = ""          # ISO format YYYY-MM-DD
    race_no: int = 0
    distance_m: int = 0
    start_type: str = "MS"  # "MS" mobile, "SS" standing
    nr_conditions: str = ""
    runners: List[Runner] = field(default_factory=list)       # Active (non-scratched) runners
    all_runners: List[Runner] = field(default_factory=list)   # All runners including scratched


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Convert horse name to URL-style slug for matching."""
    name = name.lower().strip()
    name = re.sub(r"['\u2018\u2019]", "", name)   # drop apostrophes
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = name.strip("-")
    return name


def parse_pct(val) -> float:
    """Convert '11.1%' or 0.111 or '0.111' to a float fraction."""
    if pd.isna(val):
        return 0.0
    s = str(val).strip()
    if s.endswith('%'):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return 0.0
    try:
        v = float(s)
        return v / 100.0 if v > 1.0 else v
    except ValueError:
        return 0.0


def parse_nr_from_class(class_str: str) -> float:
    """Extract a representative NR number from a class condition string.

    Examples:
      "NR 50 to 52."  → 51.0
      "NR 60 to 69."  → 64.5
      "NR up to 49."  → 49.0
      "MAIDEN"        → 0.0
      "NMT 2 LTW's"  → 0.0
    """
    if not class_str or pd.isna(class_str):
        return 0.0
    s = str(class_str)
    # Range: NR X to Y
    m = re.search(r'NR\s+(\d+)\s+to\s+(\d+)', s, re.IGNORECASE)
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2.0
    # Up to: NR up to X
    m = re.search(r'NR\s+up\s+to\s+(\d+)', s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Single: NR X
    m = re.search(r'NR\s+(\d+)', s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return 0.0


MONTH_ABBR = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10,
    'november': 11, 'december': 12,
}


def parse_date(s: str) -> Optional[str]:
    """Try to extract an ISO date string from a free-form string.

    Handles:
      "22 Mar 2026", "2026-03-22", "March 22 2026", "22/03/2026"
    Returns "YYYY-MM-DD" or None.
    """
    s = s.strip()
    # ISO
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # DD Mon YYYY or DD Month YYYY (with optional comma, optional weekday prefix)
    m = re.search(r'(\d{1,2})\s+([A-Za-z]+)[,\s]+(\d{4})', s)
    if m:
        mon = MONTH_ABBR.get(m.group(2).lower())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}"
    # Mon DD YYYY
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2})[,\s]+(\d{4})', s)
    if m:
        mon = MONTH_ABBR.get(m.group(1).lower())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    # DD/MM/YYYY
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def normalise_track(raw: str) -> str:
    """Normalise a track name to title case using VENUE_NORMALISE map."""
    key = raw.lower().strip()
    return VENUE_NORMALISE.get(key, raw.title())


def extract_track(s: str) -> Optional[str]:
    """Try to identify a known track name in a string.

    Uses word-start boundary but allows trailing digits (e.g. 'Launceston22032026').
    """
    for key, normalised in VENUE_NORMALISE.items():
        if re.search(r'\b' + re.escape(key) + r'(?:\b|\d)', s.lower()):
            return normalised
    return None


# ---------------------------------------------------------------------------
# Fields document helpers
# ---------------------------------------------------------------------------

def _is_fields_doc(text: str) -> bool:
    """Detect harness.au fields document format."""
    return bool(
        re.search(r'^R\d+\s+\d{2}:\d{2}\s+(am|pm)', text, re.MULTILINE | re.IGNORECASE)
        or 'NO. FORM NAME TRAINER DRIVER' in text
    )


def _split_trainer_driver(tokens: list[str]) -> tuple[str, str, bool]:
    """
    Split a token list into (trainer, driver, is_conditional).

    Rule: trainer = leading single-letter initials + one surname token.
          driver  = remaining tokens (first multi-letter first name onward).

    Example: ['W', 'J', 'Yole', 'Dylan', 'Ford'] → ('W J Yole', 'Dylan Ford', False)
    Example: ['C', 'V', 'Castles', 'Charlie', 'Castles'] → ('C V Castles', 'Charlie Castles', False)
    Example: ['A', 'C', 'Duggan', 'Jacob', 'Duggan', '(C)'] → ('A C Duggan', 'Jacob Duggan', True)
    """
    is_conditional = False

    # Strip (C) / (C,5) tokens and set flag
    clean = []
    for t in tokens:
        if re.match(r'^\(C(?:,\d+)?\)$', t, re.IGNORECASE):
            is_conditional = True
        else:
            clean.append(t)

    # Find split point: scan until we hit a multi-letter token that is NOT the
    # trainer surname (i.e. it follows at least one single-letter initial).
    # The trainer block is: [initials...] [Surname]
    # After the surname, the next token begins the driver name.
    split_idx = len(clean)  # default: all tokens = trainer, no driver
    seen_initial = False
    seen_surname = False
    for i, tok in enumerate(clean):
        is_single = len(tok) == 1 and tok.isupper()
        if is_single:
            seen_initial = True
        elif seen_initial and not seen_surname:
            # First multi-letter token after initials = trainer surname
            seen_surname = True
        elif seen_surname:
            # First token after trainer surname = start of driver name
            split_idx = i
            break

    trainer = ' '.join(clean[:split_idx])
    driver  = ' '.join(clean[split_idx:])
    return trainer, driver, is_conditional


def derive_barrier(hcp_str: str, tab_no: int) -> int:
    """Derive numeric barrier from HCP column value.

    FR1 → 1, FR7 → 7, SR1 → 10, SR2 → 11, FT/10/20 → tab_no, SCR → 0.
    """
    hcp = hcp_str.strip().upper()
    if hcp == 'SCR':
        return 0
    m = re.match(r'FR(\d+)', hcp)
    if m:
        return int(m.group(1))
    m = re.match(r'SR(\d+)', hcp)
    if m:
        return 9 + int(m.group(1))
    if hcp in ('FT', '10', '20'):
        return tab_no
    return tab_no


def parse_nr_fields_doc(class_str: str) -> tuple[float, float, float]:
    """Parse CLASS column from fields document.

    Returns (operative_nr, raw_nr, adjusted_nr).
    Examples:
      "NR61"       → (61.0, 61.0, 0.0)
      "NR61 (A70)" → (70.0, 61.0, 70.0)
    """
    class_str = str(class_str).strip()
    raw_nr = 0.0
    adj_nr = 0.0
    m = re.search(r'NR(\d+)', class_str, re.IGNORECASE)
    if m:
        raw_nr = float(m.group(1))
    m = re.search(r'\(A(\d+)\)', class_str, re.IGNORECASE)
    if m:
        adj_nr = float(m.group(1))
    operative = adj_nr if adj_nr > 0 else raw_nr
    return operative, raw_nr, adj_nr


def parse_fields_race_header(line: str) -> Optional[dict]:
    """Parse a race header line from the fields document.

    Input:  "R1 05:07 pm NR 70 to 79. PBD/NR. Pacers, Total Prizemoney: $9,700.00, Mobile Start The Bottle O Hadspen Pace 2200m"
    Output: dict with race_no, distance_m, start_type, nr_conditions, prizemoney.
    """
    if not re.match(r'^R\d+\s', line):
        return None
    result = {}
    m = re.match(r'^R(\d+)', line)
    if m:
        result['race_no'] = int(m.group(1))
    m = re.search(r'(\d{3,5})m\s*$', line)
    if m:
        result['distance_m'] = int(m.group(1))
    if re.search(r'Mobile Start', line, re.IGNORECASE):
        result['start_type'] = 'MS'
    elif re.search(r'Standing Start', line, re.IGNORECASE):
        result['start_type'] = 'SS'
    else:
        result['start_type'] = 'MS'
    m = re.search(r'(NR[^.]+|MAIDEN|\d+YO[^.]*)', line)
    if m:
        result['nr_conditions'] = m.group(1).strip()
    m = re.search(r'\$([\d,]+\.?\d*)', line)
    if m:
        result['prizemoney'] = float(m.group(1).replace(',', ''))
    return result


def _find_trainer_start(tokens: list[str]) -> int:
    """Find the index where trainer initials begin in a token list.

    Primary rule: 2+ consecutive single uppercase letter tokens (e.g. 'T E Rattray').
    Secondary rule: single uppercase initial followed by a title-case surname
      (e.g. 'T Bain') — catches trainers with only one initial.  Title-case is
      defined as first char uppercase, remaining chars lowercase, length >= 3,
      so country suffixes (NZ, GB) and all-caps horse-name words are excluded.
    Returns index of the first initial token, or len(tokens) if not found.
    """
    for i in range(len(tokens) - 1):
        if len(tokens[i]) == 1 and tokens[i].isupper():
            # Primary: next token is also a single uppercase letter
            if len(tokens[i + 1]) == 1 and tokens[i + 1].isupper():
                return i
            # Secondary: next token is title-case surname (>= 3 chars)
            nxt = tokens[i + 1]
            if len(nxt) >= 3 and nxt[0].isupper() and nxt[1:].islower():
                return i
    return len(tokens)


def parse_fields_runner_line(line: str) -> Optional[dict]:
    """Parse a single runner line from the fields document.

    Works right-to-left: odds, barrier code, NR class, then splits
    the left side into form string, horse name, trainer, and driver.
    """
    line = line.strip()
    if not line:
        return None

    # Must start with a 1-2 digit tab number
    tab_m = re.match(r'^(\d{1,2})\s+', line)
    if not tab_m:
        return None
    tab_no = int(tab_m.group(1))
    if tab_no < 1 or tab_no > 20:
        return None
    rest = line[tab_m.end():]

    # --- Scratched runner ---
    scr_m = re.search(r'\bSCRATCHED\b', rest)
    if scr_m:
        after_scr = rest[scr_m.end():].strip()
        before_scr = rest[:scr_m.start()].strip()

        operative, raw_nr, adj_nr = parse_nr_fields_doc(after_scr)

        tokens = before_scr.split()
        form_string = tokens[0] if tokens else ""
        remaining = tokens[1:] if len(tokens) > 1 else []

        # Strip ODM/ODS/RODS flags
        odm_mobile = odm_standing = False
        clean = []
        for t in remaining:
            if t.upper() == 'ODM':
                odm_mobile = True
            elif t.upper() in ('ODS', 'RODS'):
                odm_standing = True
            else:
                clean.append(t)

        trainer_idx = _find_trainer_start(clean)
        horse = ' '.join(clean[:trainer_idx])
        trainer = ' '.join(clean[trainer_idx:])

        return {
            'tab_no': tab_no, 'form_string': form_string,
            'horse': horse, 'trainer': trainer, 'driver': '',
            'nr': operative, 'nr_raw': raw_nr, 'nr_adjusted': adj_nr,
            'barrier': 0, 'barrier_row': '', 'handicap_m': 0,
            'is_front_tape': False, 'odm_mobile': odm_mobile,
            'odm_standing': odm_standing, 'is_conditional': False,
            'sp': 0.0, 'scratched': True,
        }

    # --- Normal runner: match right-side fields ---
    # Pattern: NR, optional (A-value), optional standing handicap, barrier code, odds
    right_m = re.search(
        r'(NR\d+)(?:\s+\(A(\d+)\))?\s+'
        r'((?:FT|10|20)\s+)?'
        r'(FR\d+|SR\d+)\s+'
        r'(\d+\.\d+|-)\s*$',
        rest,
    )
    if not right_m:
        return None

    nr_raw_str = right_m.group(1)
    adj_val = right_m.group(2)
    standing_hcp = (right_m.group(3) or '').strip()
    barrier_code = right_m.group(4)
    odds_str = right_m.group(5)
    odds = 0.0 if odds_str == '-' else float(odds_str)

    # NR values
    class_str = nr_raw_str
    if adj_val:
        class_str += f" (A{adj_val})"
    operative, raw_nr, adj_nr = parse_nr_fields_doc(class_str)

    # Barrier
    barrier = derive_barrier(barrier_code, tab_no)
    barrier_row = "FR" if barrier_code.upper().startswith('FR') else (
        "SR" if barrier_code.upper().startswith('SR') else ""
    )

    # Standing start handicap
    is_front_tape = standing_hcp == 'FT'
    handicap_m = int(standing_hcp) if standing_hcp and standing_hcp.isdigit() else 0

    # Left side: form string, horse name, ODM flags, trainer, driver
    left = rest[:right_m.start()].strip()
    tokens = left.split()
    if not tokens:
        return None

    form_string = tokens[0]
    remaining = tokens[1:]

    # Strip ODM/ODS/RODS flags
    odm_mobile = odm_standing = False
    clean = []
    for t in remaining:
        if t.upper() == 'ODM':
            odm_mobile = True
        elif t.upper() in ('ODS', 'RODS'):
            odm_standing = True
        else:
            clean.append(t)

    # Split horse name from trainer+driver
    trainer_idx = _find_trainer_start(clean)
    horse_tokens = clean[:trainer_idx]
    td_tokens = clean[trainer_idx:]

    horse = ' '.join(horse_tokens)
    trainer, driver, is_conditional = _split_trainer_driver(td_tokens)

    return {
        'tab_no': tab_no, 'form_string': form_string,
        'horse': horse, 'trainer': trainer, 'driver': driver,
        'nr': operative, 'nr_raw': raw_nr, 'nr_adjusted': adj_nr,
        'barrier': barrier, 'barrier_row': barrier_row,
        'handicap_m': handicap_m, 'is_front_tape': is_front_tape,
        'odm_mobile': odm_mobile, 'odm_standing': odm_standing,
        'is_conditional': is_conditional, 'sp': odds, 'scratched': False,
    }


def _flush_fields_race(
    races: list,
    header: dict,
    runners_data: list[dict],
    track: str,
    date_str: str,
) -> None:
    """Build a RaceInfo from parsed header and runner dicts, append to races."""
    all_runners = []
    active_runners = []

    for rd in runners_data:
        runner = Runner(
            horse=rd['horse'],
            slug=slugify(rd['horse']),
            barrier=rd['barrier'],
            driver=rd['driver'],
            trainer=rd['trainer'],
            nr=rd['nr'],
            nr_raw=rd['nr_raw'],
            nr_adjusted=rd['nr_adjusted'],
            tab_no=rd['tab_no'],
            sp=rd['sp'],
            form_string=rd['form_string'],
            barrier_row=rd['barrier_row'],
            handicap_m=rd['handicap_m'],
            is_front_tape=rd['is_front_tape'],
            odm_mobile=rd['odm_mobile'],
            odm_standing=rd['odm_standing'],
            is_conditional=rd['is_conditional'],
            scratched=rd['scratched'],
        )
        all_runners.append(runner)
        if not rd['scratched']:
            active_runners.append(runner)

    races.append(RaceInfo(
        track=track,
        date=date_str,
        race_no=header.get('race_no', 0),
        distance_m=header.get('distance_m', 0),
        start_type=header.get('start_type', 'MS'),
        nr_conditions=header.get('nr_conditions', ''),
        runners=active_runners,
        all_runners=all_runners,
    ))


def parse_fields_doc(
    text: str,
    race_no: int = 0,
    data_dir: str = '',
    filename: str = '',
) -> list[RaceInfo]:
    """Parse a complete harness.au fields document.

    Returns a list of RaceInfo objects for all races found.
    Handles page break artifacts, column headers, and second-row separators.
    """
    lines = text.splitlines()

    clean_lines: list[str] = []
    date_str = ""
    track = ""

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        # Extract date from "As at:" lines, then skip
        if stripped.startswith('As at:'):
            if not date_str:
                d = parse_date(stripped)
                if d:
                    date_str = d
            continue

        # Skip column header
        if 'NO. FORM NAME TRAINER DRIVER' in stripped:
            continue

        # Skip second-row separator
        if re.match(r'^-+SECOND ROW-+$', stripped):
            continue

        # Handle page-break lines ("*Odds... Page X of Y<runner data>")
        if 'Page' in stripped and re.search(r'Page\s+\d+\s+of\s+\d', stripped):
            m = re.search(r'Page\s+\d+\s+of\s+\d', stripped)
            if m:
                after = stripped[m.end():].strip()
                # Strip repeated page refs like "2 of 3"
                after = re.sub(r'^\d+\s+of\s+\d\s*', '', after).strip()
                if after and re.match(r'\d{1,2}\s+\S', after):
                    clean_lines.append(after)
            continue

        # Skip standalone "*Odds" lines
        if stripped.startswith('*Odds'):
            continue

        clean_lines.append(stripped)

    # Detect track from early lines
    if not track:
        for cl in clean_lines[:20]:
            t = extract_track(cl)
            if t:
                track = t
                break

    # Fallback: extract track from filename (e.g. "Launceston22032026.txt")
    if not track and filename:
        t = extract_track(os.path.basename(filename))
        if t:
            track = t

    # Parse race headers and runner lines
    races: list[RaceInfo] = []
    current_header: Optional[dict] = None
    current_runners: list[dict] = []

    for cl in clean_lines:
        header = parse_fields_race_header(cl)
        if header:
            if current_header and current_runners:
                _flush_fields_race(races, current_header, current_runners, track, date_str)
            current_header = header
            current_runners = []
            continue

        if current_header:
            runner_data = parse_fields_runner_line(cl)
            if runner_data:
                current_runners.append(runner_data)

    # Flush last race
    if current_header and current_runners:
        _flush_fields_race(races, current_header, current_runners, track, date_str)

    return races


# ---------------------------------------------------------------------------
# Mode 1: lookup from stride_results.csv
# ---------------------------------------------------------------------------

def lookup_race_from_data(
    track: str,
    date: str,
    race_no: int,
    data_dir: str,
) -> RaceInfo:
    """Build RaceInfo by querying stride_results.csv for a specific race."""
    results_path = os.path.join(data_dir, 'stride_results.csv')
    divs_path = os.path.join(data_dir, 'dividends.csv')

    df = pd.read_csv(results_path, low_memory=False)

    # Filter by track and date
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.strftime('%Y-%m-%d')
    mask = (df['Track'].str.lower() == track.lower()) & (df['Date'] == date)
    if race_no:
        mask &= (df['Race_No'] == race_no)

    race_df = df[mask].copy()
    if race_df.empty:
        raise ValueError(
            f"No race found in stride_results for track={track!r}, "
            f"date={date!r}, race_no={race_no!r}"
        )

    if not race_no:
        # Default to race with most starters if ambiguous
        race_no = race_df['Race_No'].value_counts().idxmax()
        race_df = race_df[race_df['Race_No'] == race_no]

    # One row per horse — take most recent entry if duplicates
    race_df = race_df.sort_values('Barrier').drop_duplicates(subset=['Horse'])

    runners = []
    for _, row in race_df.iterrows():
        sp_val = row.get('SP', '')
        sp_float = 0.0
        if sp_val and str(sp_val).startswith('$'):
            try:
                sp_float = float(str(sp_val).replace('$', '').replace(',', ''))
            except ValueError:
                pass

        barrier = row.get('Barrier', 0)
        try:
            barrier = int(barrier)
        except (ValueError, TypeError):
            barrier = 0

        nr = parse_nr_from_class(str(row.get('Class', '')))

        runners.append(Runner(
            horse=str(row['Horse']),
            slug=str(row.get('Slug', slugify(str(row['Horse'])))),
            barrier=barrier,
            driver=str(row.get('Driver', '')),
            trainer=str(row.get('Trainer', '')),
            nr=nr,
            tab_no=int(row.get('Tab_No', 0)) if not pd.isna(row.get('Tab_No', 0)) else 0,
            sp=sp_float,
        ))

    # Get race context from dividends if available
    start_type = "MS"
    nr_conditions = ""
    distance_m = int(race_df['Distance_m'].iloc[0]) if 'Distance_m' in race_df.columns else 0

    if os.path.exists(divs_path):
        divs = pd.read_csv(divs_path, low_memory=False)
        divs['Date'] = pd.to_datetime(divs['Date'], errors='coerce').dt.strftime('%Y-%m-%d')
        div_row = divs[
            (divs['Track'].str.lower() == track.lower()) &
            (divs['Date'] == date) &
            (divs['Race_No'] == race_no)
        ]
        if not div_row.empty:
            start_type = str(div_row.iloc[0].get('Start_Type', 'MS'))
            nr_conditions = str(div_row.iloc[0].get('NR_Conditions', ''))
            if not distance_m and 'Distance_m' in div_row.columns:
                try:
                    distance_m = int(div_row.iloc[0]['Distance_m'])
                except (ValueError, TypeError):
                    pass

    return RaceInfo(
        track=track,
        date=date,
        race_no=race_no,
        distance_m=distance_m,
        start_type=start_type,
        nr_conditions=nr_conditions,
        runners=runners,
    )


# ---------------------------------------------------------------------------
# Mode 2: parse real harness racing PDF form guide
# ---------------------------------------------------------------------------

def _parse_driver_trainer(raw: str) -> tuple[str, str]:
    """Parse 'D: T: Jacob Duggan' or 'D: John Smith T: Jane Doe' into (driver, trainer)."""
    raw = raw.strip()
    # Format 1: D: T: Name  (same person for both)
    m = re.match(r'^D:\s*T:\s*(.+)$', raw, re.IGNORECASE)
    if m:
        name = m.group(1).strip().title()
        return name, name
    # Format 2: D: Driver T: Trainer
    m = re.match(r'^D:\s*(.+?)\s+T:\s*(.+)$', raw, re.IGNORECASE)
    if m:
        return m.group(1).strip().title(), m.group(2).strip().title()
    return '', ''




def _extract_pages_from_pdf(pdf_path: str) -> list[list[str]]:
    """
    Extract text from each page as a list of line lists.
    Tries pymupdf → pdfplumber → pypdf → pdftotext.
    """
    # 1. pymupdf (fitz)
    try:
        import fitz
        pages = []
        with fitz.open(pdf_path) as doc:
            for page in doc:
                pages.append(page.get_text().splitlines())
        if pages:
            total = sum(len(p) for p in pages)
            print(f"  [PDF] Read with pymupdf ({len(pages)} pages, {total} lines)")
            return pages
    except ImportError:
        pass
    except Exception as e:
        print(f"  [PDF] pymupdf failed: {e}")

    # 2. pdfplumber
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text.splitlines())
        if pages:
            print(f"  [PDF] Read with pdfplumber ({len(pages)} pages)")
            return pages
    except ImportError:
        pass
    except Exception as e:
        print(f"  [PDF] pdfplumber failed: {e}")

    # 3. pypdf
    try:
        from pypdf import PdfReader
        pages = []
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text.splitlines())
        if pages:
            print(f"  [PDF] Read with pypdf ({len(pages)} pages)")
            return pages
    except ImportError:
        pass
    except Exception as e:
        print(f"  [PDF] pypdf failed: {e}")

    # 4. pdftotext (Poppler)
    try:
        import subprocess
        result = subprocess.run(
            ['pdftotext', '-layout', pdf_path, '-'],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Split into pages on form-feed character
            raw_pages = result.stdout.split('\f')
            pages = [p.splitlines() for p in raw_pages if p.strip()]
            print(f"  [PDF] Read with pdftotext ({len(pages)} pages)")
            return pages
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  [PDF] pdftotext failed: {e}")

    raise RuntimeError(
        f"Could not read PDF {pdf_path!r}.\n"
        "Run:  pip install pymupdf"
    )


# Lines to ignore when parsing a page
_PAGE_SKIP = {
    'name', 'last 5', 'track/', 'dist', 'prize', 'money', 'best mr', 'rating',
    'd/t', '#', 'no', 'horse',
}


def _parse_runner_group(lines: list[str]) -> Optional[Runner]:
    """
    Parse one runner's group of lines.

    Real PDF layout (one line per element, from pymupdf):
      Line 0:  "D: T: Jacob Duggan"       ← driver / trainer
      Line 1:  "1. Iamahunter"            ← tab. Horse Name
      Line 2:  "91249"                    ← last 5
      Line 3:  "3-1-1-0"                  ← prize / career summary
      Line 4:  "-"  or  "Hobart/2090"     ← track/dist or placeholder
      Line 5:  "00:00:00"                 ← best time
      Line 6:  "NR61"                     ← MR rating
      Line 7:  "(A60)"                    ← optional allowance
    """
    if len(lines) < 2:
        return None

    # Line 0: driver / trainer
    driver, trainer = _parse_driver_trainer(lines[0])

    # Line 1: "1. Horse Name"  or  "1 Horse Name"
    m = re.match(r'^(\d{1,2})[\.\s]\s*(.+)$', lines[1].strip())
    if not m:
        return None
    tab_no = int(m.group(1))
    if tab_no < 1 or tab_no > 20:
        return None
    horse = m.group(2).strip().title()

    # Scan remaining lines for NR
    nr = 0.0
    for line in lines[2:]:
        nr_m = re.match(r'^NR\s*(\d+)', line.strip(), re.IGNORECASE)
        if nr_m:
            nr = float(nr_m.group(1))
            break

    return Runner(
        horse=horse,
        slug=slugify(horse),
        barrier=tab_no,
        driver=driver,
        trainer=trainer,
        nr=nr,
        tab_no=tab_no,
    )


def _parse_page(page_lines: list[str], track: str, date: str) -> Optional[RaceInfo]:
    """
    Parse one PDF page into a RaceInfo.

    Page structure:
      [header lines: Hobart Harness, date, column names]
      [runner groups: D: T: line, then horse line, then data lines]
      [footer: "R1 Race Name HH:MM", "NR conditions, 2090m", tips, gambling warning]
    """
    lines = [l.strip() for l in page_lines]

    # --- Find race info in the footer (scan from bottom) ---
    race_no = None
    race_name = ""
    conditions = ""
    distance_m = 0
    start_type = "MS"

    for line in reversed(lines):
        if not line:
            continue
        # "R1 Fehlberg's Produce Sprint Lane Pace 14:19"
        m = re.match(r'^R(\d+)\s+(.+?)(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$', line)
        if m and race_no is None:
            race_no = int(m.group(1))
            race_name = m.group(2).strip()
            if re.search(r'\bstanding\b|\bSS\b', race_name, re.IGNORECASE):
                start_type = "SS"
            continue
        # "NR 60 to 64., 2090m"  or  "MAIDEN, 1609m"
        m2 = re.match(r'^(.+?),\s*(\d{3,4})\s*m', line, re.IGNORECASE)
        if m2 and distance_m == 0:
            conditions = m2.group(1).strip().rstrip('.')
            distance_m = int(m2.group(2))
            continue

    if race_no is None:
        return None

    # --- Parse runner groups ---
    # Each group starts with a "D:" line
    groups: list[list[str]] = []
    current: list[str] = []

    footer_started = False
    for line in lines:
        if not line:
            continue
        # Skip column header lines
        if line.lower() in _PAGE_SKIP:
            continue
        if re.search(r'GAMBLE|HELPLINE|1800\s*858', line, re.IGNORECASE):
            continue
        if re.match(r'^Top \d+ tips', line, re.IGNORECASE):
            footer_started = True
        if footer_started:
            continue
        # Footer: R{n} line signals end of runners
        if re.match(r'^R\d+\s+', line):
            footer_started = True
            continue

        if re.match(r'^D:', line, re.IGNORECASE):
            if current:
                groups.append(current)
            current = [line]
        elif current:
            current.append(line)

    if current:
        groups.append(current)

    runners = []
    for g in groups:
        r = _parse_runner_group(g)
        if r:
            runners.append(r)

    return RaceInfo(
        track=track,
        date=date,
        race_no=race_no,
        distance_m=distance_m,
        start_type=start_type,
        nr_conditions=conditions,
        runners=runners,
    )


# ---------------------------------------------------------------------------
# Detailed format parser  (e.g. Burnie/RaceDay full form)
# ---------------------------------------------------------------------------

# "R1 04:24 pm  Greg Byrne Memorial 2180m"
_DETAILED_RACE_HDR = re.compile(
    r'^R(\d+)\s+\d{1,2}:\d{2}\s*(?:am|pm)?\s+(.+?)\s+(\d{3,4})\s*m\s*$',
    re.IGNORECASE,
)

# "1. SHEEZA SOHO (FR1) NR48"  or  "1. JEREMY WELLS NZ (FT FR1) NR53 (A50)"
_DETAILED_RUNNER = re.compile(
    r'^(\d{1,2})\.\s+([A-Z][A-Z0-9\s\'\-]+?)\s+\(([^)]+)\)\s+NR(\d+)',
)

# "Trainer: T R Langley (TAS)     Driver: Jacob Duggan (C)"
_TRAINER_DRIVER = re.compile(
    r'^Trainer:\s*(.+?)\s{2,}Driver:\s*(.+)$'
)


def _parse_barrier_code(code: str, tab_no: int) -> int:
    """Extract numeric barrier from codes like FR1, SR1, FT FR2, 10 FR3."""
    m = re.search(r'FR(\d+)', code, re.IGNORECASE)
    return int(m.group(1)) if m else tab_no


def _extract_races_detailed(
    pages: list[list[str]], track: str, date: str
) -> list[RaceInfo]:
    """Parse the full-form 'detailed' PDF format (Burnie / RaceDay style)."""
    all_lines = [l.strip() for page in pages for l in page]

    races: list[RaceInfo] = []
    race_no = None
    distance_m = 0
    start_type = "MS"
    conditions = ""
    runners: list[Runner] = []

    def _flush():
        if race_no is not None and runners:
            races.append(RaceInfo(
                track=track,
                date=date,
                race_no=race_no,
                distance_m=distance_m,
                start_type=start_type,
                nr_conditions=conditions,
                runners=list(runners),
            ))

    i = 0
    while i < len(all_lines):
        line = all_lines[i]

        # Race header
        mh = _DETAILED_RACE_HDR.match(line)
        if mh:
            _flush()
            race_no = int(mh.group(1))
            race_name = mh.group(2).strip()
            distance_m = int(mh.group(3))
            start_type = "SS" if re.search(r'\bstanding\b', race_name, re.IGNORECASE) else "MS"
            conditions = ""
            runners = []
            # Peek at next line for conditions (not Track/R/digit lines)
            if i + 1 < len(all_lines):
                nxt = all_lines[i + 1]
                if nxt and not re.match(r'^Track|^R\d+|^\d+\.|^Page', nxt, re.IGNORECASE):
                    conditions = nxt[:120]
            i += 1
            continue

        # Runner line
        mr = _DETAILED_RUNNER.match(line)
        if mr and race_no is not None:
            tab_no = int(mr.group(1))
            horse = mr.group(2).strip().title()
            barrier = _parse_barrier_code(mr.group(3), tab_no)
            nr = float(mr.group(4))

            # Scan next 4 lines for Trainer/Driver
            driver, trainer = "", ""
            for j in range(i + 1, min(i + 5, len(all_lines))):
                mtd = _TRAINER_DRIVER.match(all_lines[j])
                if mtd:
                    # Strip state codes like "(TAS)", "(VIC)"
                    trainer = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', mtd.group(1)).strip().title()
                    driver = re.sub(r'\s*\(C\)\s*$', '', mtd.group(2)).strip().title()
                    break

            runners.append(Runner(
                horse=horse,
                slug=slugify(horse),
                barrier=barrier,
                driver=driver,
                trainer=trainer,
                nr=nr,
                tab_no=tab_no,
            ))
            i += 1
            continue

        i += 1

    _flush()
    return sorted(races, key=lambda r: r.race_no)


def _detect_format(pages: list[list[str]]) -> str:
    """Return 'detailed' or 'simple' based on first-page content."""
    for line in (pages[0] if pages else [])[:15]:
        line = line.strip()
        if _DETAILED_RACE_HDR.match(line):
            return 'detailed'
        if _TRAINER_DRIVER.match(line):
            return 'detailed'
    return 'simple'


def extract_races_from_pdf(pdf_path: str) -> tuple[str, str, list[RaceInfo]]:
    """
    Extract all races from a harness racing PDF form guide.
    Auto-detects format (simple one-race-per-page vs detailed full form).

    Returns:
        (track, date, list_of_RaceInfo)
    """
    pages = _extract_pages_from_pdf(pdf_path)

    # Track and date from first page header lines
    track = "Unknown"
    date = ""
    for line in (pages[0][:10] if pages else []):
        line = line.strip()
        if track == "Unknown":
            t = extract_track(line)
            if t:
                track = t
        if not date:
            d = parse_date(line)
            if d:
                date = d
        # Detailed forms have "FRI 13 MAR" (no year) — try with current year
        if not date:
            m = re.search(r'(\d{1,2})\s+([A-Za-z]{3})\s*$', line)
            if m:
                mon = MONTH_ABBR.get(m.group(2).lower())
                if mon:
                    import datetime
                    year = datetime.date.today().year
                    date = f"{year}-{mon:02d}-{int(m.group(1)):02d}"

    fmt = _detect_format(pages)

    if fmt == 'detailed':
        races = _extract_races_detailed(pages, track, date)
    else:
        races = []
        for page_lines in pages:
            info = _parse_page(page_lines, track, date)
            if info and info.runners:
                races.append(info)
        races.sort(key=lambda r: r.race_no)

    return track, date, races


def list_races(races: list[RaceInfo]) -> None:
    """Print a summary of all races found in the form."""
    print(f"\n  {'R#':<4} {'Conditions':<20} {'Dist':>5}  Runners")
    print("  " + "-" * 50)
    for r in races:
        conds = r.nr_conditions[:18] if r.nr_conditions else "—"
        print(f"  R{r.race_no:<3} {conds:<20} {r.distance_m:>5}m  {len(r.runners)} runners")
    print()


def parse_pdf_form(pdf_path: str, race_no: int = 0) -> RaceInfo:
    """
    Parse a harness racing PDF form, returning the specified race.

    If race_no == 0, lists all available races and raises SystemExit.
    """
    track, date, races = extract_races_from_pdf(pdf_path)

    if not races:
        raise ValueError(f"No races could be parsed from {pdf_path!r}")

    if race_no == 0:
        print(f"\n[form_parser] Found {len(races)} race(s) in {os.path.basename(pdf_path)}")
        print(f"              Track: {track}   Date: {date}")
        list_races(races)
        print("  Specify a race with --race N  (e.g. --race 3)")
        raise SystemExit(0)

    for r in races:
        if r.race_no == race_no:
            return r

    # Race not found
    print(f"\n[form_parser] Race {race_no} not found. Available races:")
    list_races(races)
    raise ValueError(f"Race {race_no} not found in PDF (found R{[r.race_no for r in races]})")


# ---------------------------------------------------------------------------
# Mode 3: parse free-text form
# ---------------------------------------------------------------------------

def parse_text_form(text: str) -> RaceInfo:
    """Parse a pasted text form into a RaceInfo.

    Handles three input styles:

    Style A — numbered list with explicit barrier (recommended for paste):
        1. Away Game — barrier 1 — Liam Older — NR45
        2. Nikita Jo — barrier 2 — Ryan Backhouse — NR50

    Style B — numbered list, barrier = position (classic sim format):
        1. Away Game  Liam Older  NR45
        2. Nikita Jo  Ryan Backhouse  NR50

    Style C — plain name list, barrier from context (no numbers needed):
        Away Game — barrier 1
        Nikita Jo — barrier 2

    Header (any style, placed before field):
        Burnie Race 4 — 13 Mar 2026 — 2180m — Standing start (SS)
        Start type: SS   Distance: 2180m

    Separators between fields can be " — ", ", ", " | ", or whitespace.
    NR, Driver, and trainer are all optional.
    """
    info = RaceInfo()
    runners = []

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # --- Scan ALL lines for header metadata (not just first 5) ---
    # This lets users put "Start type: SS" anywhere in their paste.
    for line in lines:
        tl = line.lower()

        # Track
        if not info.track:
            t = extract_track(line)
            if t:
                info.track = t

        # Date
        if not info.date:
            d = parse_date(line)
            if d:
                info.date = d

        # Race number
        if not info.race_no:
            m = re.search(r'[Rr]ace\s+(\d+)', line)
            if m:
                info.race_no = int(m.group(1))

        # Distance
        if not info.distance_m:
            m = re.search(r'(\d{3,5})\s*m\b', line)
            if m:
                info.distance_m = int(m.group(1))

        # Start type — check for SS/standing anywhere in the paste
        if info.start_type == 'MS':
            if re.search(r'\bstanding\s+start\b|\bSS\b|\bstart\s*type\s*[:\-–]\s*SS\b',
                         line, re.IGNORECASE):
                info.start_type = 'SS'

    # --- Normalise delimiter: replace em-dash / pipe / semicolon with plain comma ---
    def _norm(s: str) -> str:
        return re.sub(r'\s*[—–|;]\s*', ', ', s).strip()

    # --- Pattern A: "Horse Name — barrier N [— Driver] [— NR N] [— $SP]" ---
    # Barrier is explicit; horse name is everything before it.
    # Works with or without a leading tab number.
    pat_barrier_explicit = re.compile(
        r'^(?:(\d{1,2})[\.\):\s]+)?'                # optional tab number
        r'([A-Za-z][A-Za-z0-9\s\'\-]+?)'            # horse name (greedy up to barrier)
        r'[,\s]+[Bb]arrier\s+(\d{1,2})'             # "barrier N" — anchor
        r'(?:[,\s]+([A-Za-z][A-Za-z\s\.]+?))?'      # optional driver
        r'(?:[,\s]+NR\s*(\d+))?'                    # optional NR
        r'(?:[,\s]+\$?([\d\.]+))?$',                # optional SP
        re.IGNORECASE,
    )

    # --- Pattern B: numbered list where barrier = tab number ---
    # "1. Away Game  Liam Older  NR45"
    # Horse name ends at a recognised NR/driver-boundary token.
    pat_numbered = re.compile(
        r'^(\d{1,2})[\.\):\s]+'                      # tab number (required)
        r'([A-Z][A-Za-z0-9\s\'\-]+?)'               # horse name
        r'(?:\s+(?:Fr|Sr|Barrier\s*)?(\d{1,2})\b)?'  # optional explicit barrier
        r'(?:\s+([A-Za-z][A-Za-z\s\.]+?))?'          # optional driver
        r'(?:\s*/\s*([A-Za-z][A-Za-z\s\.]+?))?'      # optional trainer (after /)
        r'(?:\s+NR\s*(\d+))?'                        # optional NR
        r'(?:\s+\$?([\d\.]+))?$',
        re.IGNORECASE,
    )

    for line in lines:
        # Skip obvious header/metadata lines
        if re.match(r'^(track|date|race|distance|start|field|actual|result|[0-9]+st|[0-9]+nd|[0-9]+rd|[0-9]+th)\b',
                    line, re.IGNORECASE):
            continue

        normed = _norm(line)

        # Try Pattern A first (explicit "barrier N" anchor is unambiguous)
        m = pat_barrier_explicit.match(normed)
        if m:
            tab_no   = int(m.group(1)) if m.group(1) else len(runners) + 1
            horse    = m.group(2).strip().title()
            barrier  = int(m.group(3))
            driver   = (m.group(4) or '').strip()
            nr       = float(m.group(5)) if m.group(5) else 0.0
            sp       = float(m.group(6)) if m.group(6) else 0.0
            runners.append(Runner(
                horse=horse, slug=slugify(horse),
                barrier=barrier, driver=driver,
                nr=nr, sp=sp, tab_no=tab_no,
            ))
            continue

        # Try Pattern B (numbered list)
        m = pat_numbered.match(normed)
        if m:
            tab_no   = int(m.group(1))
            horse    = m.group(2).strip().title()
            barrier  = int(m.group(3)) if m.group(3) else tab_no
            driver   = (m.group(4) or '').strip()
            trainer  = (m.group(5) or '').strip()
            nr       = float(m.group(6)) if m.group(6) else 0.0
            sp       = float(m.group(7)) if m.group(7) else 0.0
            runners.append(Runner(
                horse=horse, slug=slugify(horse),
                barrier=barrier, driver=driver, trainer=trainer,
                nr=nr, sp=sp, tab_no=tab_no,
            ))

    if not runners:
        # Last resort: any line starting with a capital and containing "barrier N"
        for line in lines:
            m = re.search(r'([A-Z][A-Za-z\s\'\-]+?)\s*[,—–]\s*barrier\s+(\d+)', line, re.IGNORECASE)
            if m:
                horse   = m.group(1).strip().title()
                barrier = int(m.group(2))
                runners.append(Runner(
                    horse=horse, slug=slugify(horse),
                    barrier=barrier, tab_no=barrier,
                ))

    info.runners = runners
    return info


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_form(
    form_str: str,
    data_dir: str,
    track_override: Optional[str] = None,
    race_no: int = 0,
    filename: str = '',
) -> RaceInfo:
    """Detect form type and return a RaceInfo.

    Args:
        form_str:       Path to a PDF form file, or a pasted text block.
        data_dir:       Path to output/claude_data/ (used only for standings lookups).
        track_override: Explicit track name (overrides anything parsed from form).
        race_no:        Race number to select from the PDF (required for PDFs).
        filename:       Original filename (used to extract track from filename).

    Returns:
        RaceInfo with runners populated.
    """
    form_str = form_str.strip()

    # --- PDF: primary mode ---
    if form_str.lower().endswith('.pdf'):
        if not os.path.isfile(form_str):
            raise FileNotFoundError(f"PDF not found: {form_str!r}")
        info = parse_pdf_form(form_str, race_no=race_no)
        if track_override:
            info.track = normalise_track(track_override)
        return info

    # --- Fields document (harness.au format) ---
    if _is_fields_doc(form_str):
        races = parse_fields_doc(form_str, race_no=race_no, data_dir=data_dir, filename=filename)
        if track_override:
            for r in races:
                r.track = normalise_track(track_override)
        if race_no:
            for r in races:
                if r.race_no == race_no:
                    return r
            raise ValueError(f"Race {race_no} not found in fields document")
        # List all races and exit
        print(f"\n[form_parser] Found {len(races)} race(s) in fields document")
        if races:
            print(f"              Track: {races[0].track or '(use --track)'}   Date: {races[0].date or '?'}")
        list_races(races)
        print("  Specify a race with --race N  (e.g. --race 3)")
        raise SystemExit(0)

    # --- Free-text pasted form ---
    info = parse_text_form(form_str)
    if track_override:
        info.track = normalise_track(track_override)
    if race_no:
        info.race_no = race_no
    return info
