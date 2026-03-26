# Claude Code Instructions — Fields Document Parser
# File: form_parser.py — add new parsing mode for harness.au fields document

---

## TASK OVERVIEW

Add a new parsing mode to `form_parser.py` that handles the harness.au
fields document format. This is simpler and more consistent than the PDF
format and should become the PRIMARY input method going forward.

Test file: `C:\Users\evil_\Desktop\Scraper\Form PDFs\Launceston22032026.txt`

Do NOT modify any existing parsing modes. Add the new mode alongside them.

---

## FIELDS DOCUMENT FORMAT — REFERENCE

```
R1 05:07 pm NR 70 to 79. PBD/NR. Pacers, Total Prizemoney: $9,700.00, Mobile Start The Bottle O Hadspen Pace 2200m
NO. FORM NAME TRAINER DRIVER CLASS HCP ODDS*
1 78768 WAVETHEBILL NZ W J Yole Dylan Ford NR61 (A70) FR1 41.00
2 09652 ROCKANDAHARDPLACE K E Rattray John Walters NR70 FR2 18.00
...
8 05517 LYNRYD SKYNRYD NZ W J Yole Gareth Rattray NR77 SR1 41.00
9 36421 ANOTHER NIEN A C Duggan Jacob Duggan (C) NR79 SR2 10.00
```

### Column definitions

| Column   | Example              | Notes |
|----------|----------------------|-------|
| NO.      | 1, 2 ... 12          | Tab number |
| FORM     | 78768, 1111, 1b580   | Recent form string — not used by sim, but capture it |
| NAME     | WAVETHEBILL NZ       | Horse name. May include NZ/GB/USA suffix |
| TRAINER  | W J Yole             | 2-4 word trainer name |
| DRIVER   | Dylan Ford           | 2-3 word driver name. May have (C) or (C,5) suffix |
| CLASS    | NR61, NR61 (A70)     | NR rating. Adjusted rating in brackets if present |
| HCP      | FR1, SR2, FT, 10, 20 | Barrier/handicap — see decode table below |
| ODDS*    | 41.00, 1.13          | Market odds at time of print |

### HCP column decode

```
FR1, FR2 ... FR9   = Front row, barrier 1-9 (mobile start)
SR1, SR2 ... SR6   = Second row, barrier 10-15 approx (mobile start)
FT                 = Front tape — no handicap (standing start)
10                 = 10 metres handicap (standing start)
20                 = 20 metres handicap (standing start)
SCR                = Scratched — exclude from field
```

### Special flags in NAME or HCP column

```
ODM    = Out of Draw in Mobiles — horse starts outside normal draw
ODS    = Out of Draw in Standing starts
RODS   = Restricted Out of Draw in Standing starts (one more chance)
SCRATCHED = Horse withdrawn — skip this runner entirely
```

### Driver suffixes

```
(C)    = Conditional/claiming rider — treat as junior driver flag
(C,5)  = Conditional, 5kg claim — same flag
Strip these from the driver name before storing
```

### Adjusted NR notation

```
NR61 (A70)  → operative NR for this race = 70 (the adjusted figure)
NR66 (A70)  → operative NR = 70
NR76 (A71)  → operative NR = 71
NR57        → operative NR = 57 (no adjustment)
```

Always use the adjusted NR (A-value) when present. This is the actual
rating the horse races off tonight. Store both raw and adjusted.

---

## CHANGES TO form_parser.py

### Step 1 — Update Runner dataclass

Add these fields:

```python
@dataclass
class Runner:
    horse: str
    slug: str
    barrier: int
    driver: str = ""
    trainer: str = ""
    nr: float = 0.0
    nr_raw: float = 0.0         # NEW: original NR before adjustment
    nr_adjusted: float = 0.0    # NEW: adjusted NR (A-value) if present, else same as nr
    tab_no: int = 0
    sp: float = 0.0
    form_string: str = ""       # NEW: recent form digits e.g. "78768"
    barrier_row: str = ""       # NEW: "FR" or "SR"
    handicap_m: int = 0         # NEW: standing start handicap in metres (0, 10, or 20)
    is_front_tape: bool = False # NEW: True if HCP == "FT"
    odm_mobile: bool = False    # NEW: ODM flag
    odm_standing: bool = False  # NEW: ODS or RODS flag
    is_conditional: bool = False # NEW: driver has (C) or (C,5) suffix
    scratched: bool = False     # NEW: horse is scratched
```

### Step 2 — Add barrier number derivation function

```python
def derive_barrier(hcp_str: str, tab_no: int) -> int:
    """
    Derive numeric barrier from HCP column value.
    
    FR1 → 1
    FR7 → 7
    SR1 → 10  (second row starts at barrier 10)
    SR2 → 11
    SR3 → 12  etc.
    FT  → tab_no (standing start, barrier = tab position)
    10  → tab_no (standing start with 10m handicap)
    20  → tab_no (standing start with 20m handicap)
    SCR → 0 (scratched)
    
    SR offset: second row horses are numbered sequentially from 10.
    SR1=10, SR2=11, SR3=12, SR4=13, SR5=14, SR6=15.
    """
    hcp = hcp_str.strip().upper()
    
    if hcp == 'SCR':
        return 0
    
    m = re.match(r'FR(\d+)', hcp)
    if m:
        return int(m.group(1))
    
    m = re.match(r'SR(\d+)', hcp)
    if m:
        return 9 + int(m.group(1))   # SR1=10, SR2=11, etc.
    
    # Standing start: FT, 10, 20 — barrier = tab_no
    if hcp in ('FT', '10', '20'):
        return tab_no
    
    # Fallback
    return tab_no
```

### Step 3 — Add NR parsing function

```python
def parse_nr_fields_doc(class_str: str) -> tuple[float, float, float]:
    """
    Parse CLASS column from fields document.
    Returns (operative_nr, raw_nr, adjusted_nr).
    
    Examples:
      "NR61"          → (61.0, 61.0, 0.0)
      "NR61 (A70)"    → (70.0, 61.0, 70.0)   ← use adjusted as operative
      "NR66 (A70)"    → (70.0, 66.0, 70.0)
    """
    class_str = str(class_str).strip()
    
    raw_nr = 0.0
    adj_nr = 0.0
    
    # Raw NR
    m = re.search(r'NR(\d+)', class_str, re.IGNORECASE)
    if m:
        raw_nr = float(m.group(1))
    
    # Adjusted NR
    m = re.search(r'\(A(\d+)\)', class_str, re.IGNORECASE)
    if m:
        adj_nr = float(m.group(1))
    
    operative = adj_nr if adj_nr > 0 else raw_nr
    return operative, raw_nr, adj_nr
```

### Step 4 — Add race header parsing function

```python
def parse_fields_race_header(line: str) -> Optional[dict]:
    """
    Parse a race header line from the fields document.
    
    Input:  "R1 05:07 pm NR 70 to 79. PBD/NR. Pacers, Total Prizemoney: $9,700.00, Mobile Start The Bottle O Hadspen Pace 2200m"
    Output: {
        'race_no': 1,
        'distance_m': 2200,
        'start_type': 'MS',   # or 'SS'
        'nr_conditions': 'NR 70 to 79',
        'race_name': 'The Bottle O Hadspen Pace',
        'prizemoney': 9700.00,
    }
    
    Start type detection:
      "Mobile Start" → 'MS'
      "Standing Start" → 'SS'
    """
    if not re.match(r'^R\d+\s', line):
        return None
    
    result = {}
    
    # Race number
    m = re.match(r'^R(\d+)', line)
    if m:
        result['race_no'] = int(m.group(1))
    
    # Distance
    m = re.search(r'(\d{3,5})m\s*$', line)
    if m:
        result['distance_m'] = int(m.group(1))
    
    # Start type
    if re.search(r'Mobile Start', line, re.IGNORECASE):
        result['start_type'] = 'MS'
    elif re.search(r'Standing Start', line, re.IGNORECASE):
        result['start_type'] = 'SS'
    else:
        result['start_type'] = 'MS'  # default
    
    # NR conditions
    m = re.search(r'(NR[^.]+|MAIDEN|\d+YO[^.]*)', line)
    if m:
        result['nr_conditions'] = m.group(1).strip()
    
    # Prizemoney
    m = re.search(r'\$([\d,]+\.?\d*)', line)
    if m:
        result['prizemoney'] = float(m.group(1).replace(',', ''))
    
    return result
```

### Step 5 — Add runner line parsing function

```python
def parse_fields_runner_line(line: str) -> Optional[dict]:
    """
    Parse a single runner line from the fields document.
    
    Input:  "1 78768 WAVETHEBILL NZ W J Yole Dylan Ford NR61 (A70) FR1 41.00"
    Input:  "9 36421 ANOTHER NIEN A C Duggan Jacob Duggan (C) NR79 SR2 10.00"
    Input:  "10 11112 TRIEDTOTELLYA R L Hillier SCRATCHED NR120 SCR SCR"
    Input:  "6 07235 VANQUISH STRIDE NZ ODS W J Yole Ricky Duggan NR91 FT FR6 21.00"
    
    Returns dict with all runner fields, or None if line is not a runner.
    
    PARSING STRATEGY:
    Work from RIGHT to LEFT — the rightmost columns are most reliably delimited.
    
1. Tab number: first token if it is a 1-2 digit integer
    2. Odds: last token if it matches \d+\.\d+ or 'SCR'
    3. HCP: second-to-last token (FR1, SR2, FT, 10, 20, SCR)
    4. CLASS: token matching NR\d+ pattern, with optional (A\d+) following it
    5. SCRATCHED: if 'SCRATCHED' appears where driver would be
    6. ODM flags: 'ODM', 'ODS', 'RODS' tokens — strip from name, set flags
    7. FORM: second token (digit string, may contain letters like 'b' or 's')
    8. TRAINER/DRIVER SPLIT RULE:
       Trainer always uses initials then surname: one or more single uppercase
       letters followed by a surname. Pattern: ([A-Z]\s)+[A-Za-z]+
       Driver always uses full first name then surname: a complete word
       (not a single letter) followed by a surname.
       Split point = where the sequence transitions from single-letter tokens
       to a multi-letter first name token.

       Example: "W J Yole Dylan Ford"
         "W J Yole" → W, J are single letters → trainer
         "Dylan Ford" → Dylan is a full word → driver

       Example: "C V Castles Charlie Castles"
         "C V Castles" → C, V are single letters → trainer
         "Charlie Castles" → Charlie is a full word → driver

       Strip (C) and (C,5) from driver name after splitting.
       Set is_conditional = True if either suffix was present.
       No CSV lookup needed. No fallback needed. Rule is consistent
       across all harness.au fields documents by convention.
    9. Horse name: everything between FORM string and trainer
    """
```

### Step 6 — Add main fields document parser

```python
def parse_fields_doc(
    text: str,
    race_no: int = 0,
    data_dir: str = '',
) -> Union[RaceInfo, List[RaceInfo]]:
    """
    Parse a complete harness.au fields document.
    
    If race_no > 0: return just that race as RaceInfo
    If race_no == 0: return list of all RaceInfo objects
    
    Handles:
    - Page break artifacts: 
      "As at: Thu, 26 Mar 2026 04:42 pm" lines → skip
      "*Odds are subject to change..." lines → skip
      "Page 1 of 3" etc → skip
      "Page 1 of 34 72064 KEAYANG FITZY..." → the page break interrupts
      a runner line. Detect and rejoin these.
    - Column header lines: "NO. FORM NAME TRAINER DRIVER CLASS HCP ODDS*" → skip
    - Second row separator: "---SECOND ROW---" style lines → skip (SR prefix handles it)
    - Scratched horses: parse but set runner.scratched = True, exclude from
      active field but keep in RaceInfo.all_runners for reference
    
    PAGE BREAK REJOIN LOGIC (important):
    The document contains lines like:
    "As at: Thu, 26 Mar 2026 04:42 pm"
    "*Odds are subject to change. These fields are available for personal use only..."
    "Page 1 of 34 72064 KEAYANG FITZY W J Yole Mark Yole NR102 FT FR4 5.00"
    
    The last line above starts with "Page 1 of 3" then immediately continues
    with a runner entry. Split on the page marker and process the remainder
    as a normal runner line.
    
    Pattern to detect: r'Page \d+ of \d+\s*(\d+\s+.+)'
    The captured group is the actual runner data.
    """
```

### Step 7 — Integrate into parse_form()

Update the main `parse_form()` dispatcher to detect fields document format:

```python
def parse_form(form_str, data_dir='', track_override=None, race_no=0):
    
    form_str = form_str.strip()
    
    # PDF
    if form_str.lower().endswith('.pdf'):
        ...
    
    # Fields document detection
    # Key signal: contains "NO. FORM NAME TRAINER DRIVER" header
    # or starts with "R\d+ \d\d:\d\d (am|pm)"
    if _is_fields_doc(form_str):
        races = parse_fields_doc(form_str, race_no=race_no, data_dir=data_dir)
        if isinstance(races, list):
            if race_no:
                for r in races:
                    if r.race_no == race_no:
                        return r
                raise ValueError(f"Race {race_no} not found in fields document")
            # If no race specified and multiple races, return first or list them
            print(f"[form_parser] {len(races)} races found. Specify --race N.")
            for r in races:
                print(f"  R{r.race_no}: {r.distance_m}m {r.start_type} — {len(r.runners)} runners")
            raise SystemExit(0)
        return races
    
    # Existing text parser
    ...

def _is_fields_doc(text: str) -> bool:
    """Detect harness.au fields document format."""
    return bool(
        re.search(r'^R\d+\s+\d{2}:\d{2}\s+(am|pm)', text, re.MULTILINE | re.IGNORECASE)
        or 'NO. FORM NAME TRAINER DRIVER' in text
    )
```

---

## CLI USAGE AFTER IMPLEMENTATION

```bash
# Simulate race 4 from the fields document
python race_sim.py --form "C:\Users\evil_\Desktop\Scraper\Form PDFs\Launceston22032026.txt" --race 4 --data output/claude_data

# List all races in the document
python race_sim.py --form "C:\Users\evil_\Desktop\Scraper\Form PDFs\Launceston22032026.txt" --data output/claude_data

# Simulate all races sequentially (for backtest)
python race_sim.py --form "C:\Users\evil_\Desktop\Scraper\Form PDFs\Launceston22032026.txt" --all --data output/claude_data
```

Add `--all` flag to race_sim.py CLI that iterates all races and outputs
a summary table at the end.

---

## VALIDATION TESTS

After implementation, run these checks:

### Test 1 — Field counts match
```python
expected = {1: 9, 2: 11, 3: 11, 4: 10, 5: 12, 6: 9, 7: 13, 8: 10, 9: 12}
# (includes scratched horses in count)
for race_no, count in expected.items():
    race = parse_fields_doc(text, race_no=race_no)
    assert len(race.all_runners) == count, f"R{race_no}: expected {count}, got {len(race.all_runners)}"
```

### Test 2 — Active field excludes scratched
```python
# R6: Triedtotellya scratched → active field = 9
# R7: Tullah Girl scratched → active field = 12
# R9: Camelot Jedimaster scratched → active field = 11
r6 = parse_fields_doc(text, race_no=6)
assert len(r6.runners) == 9
assert any(r.scratched and r.horse == 'Triedtotellya' for r in r6.all_runners)
```

### Test 3 — Barrier derivation correct
```python
r1 = parse_fields_doc(text, race_no=1)
# Wavethebill FR1 → barrier 1
# Another Nien SR2 → barrier 11
wavethebill = next(r for r in r1.runners if 'WAVE' in r.horse.upper())
another_nien = next(r for r in r1.runners if 'NIEN' in r.horse.upper())
assert wavethebill.barrier == 1
assert another_nien.barrier == 11
```

### Test 4 — Adjusted NR used correctly
```python
r1 = parse_fields_doc(text, race_no=1)
# Wavethebill NR61 (A70) → operative nr = 70
wavethebill = next(r for r in r1.runners if 'WAVE' in r.horse.upper())
assert wavethebill.nr == 70.0
assert wavethebill.nr_raw == 61.0
assert wavethebill.nr_adjusted == 70.0
```

### Test 5 — Standing start fields parsed correctly
```python
r4 = parse_fields_doc(text, race_no=4)
assert r4.start_type == 'SS'
# Pardoe Plugga FT → handicap_m = 0, is_front_tape = True
# Stepping Stones 10 → handicap_m = 10
# Magnetic Terror 20 → handicap_m = 20
pardoe = next(r for r in r4.runners if 'PARDOE' in r.horse.upper())
stepping = next(r for r in r4.runners if 'STEPPING' in r.horse.upper())
magnetic = next(r for r in r4.runners if 'MAGNETIC' in r.horse.upper())
assert pardoe.handicap_m == 0 and pardoe.is_front_tape == True
assert stepping.handicap_m == 10
assert magnetic.handicap_m == 20
```

### Test 6 — ODM flags set correctly
```python
r8 = parse_fields_doc(text, race_no=8)
# Muzzame Mate ODM → odm_mobile = True
# Iden Regal Wave ODM → odm_mobile = True
muzzame = next(r for r in r8.runners if 'MUZZAME' in r.horse.upper())
iden = next(r for r in r8.runners if 'IDEN' in r.horse.upper())
assert muzzame.odm_mobile == True
assert iden.odm_mobile == True
```

### Test 7 — Conditional driver flag
```python
r1 = parse_fields_doc(text, race_no=1)
# Jacob Duggan (C) → is_conditional = True, driver = "Jacob Duggan"
another_nien = next(r for r in r1.runners if 'NIEN' in r.horse.upper())
assert another_nien.is_conditional == True
assert another_nien.driver == 'Jacob Duggan'
assert '(C)' not in another_nien.driver
```

### Test 8 — Page break rejoining works
```python
r4 = parse_fields_doc(text, race_no=4)
# Keayang Fitzy appears after a page break in the source
# Verify it is parsed correctly
keayang = next((r for r in r4.runners if 'KEAYANG' in r.horse.upper()), None)
assert keayang is not None, "Keayang Fitzy missing — page break not handled"
assert keayang.nr == 102.0
```

### Test 9 — Smoke test end-to-end
```bash
python race_sim.py --form "C:\Users\evil_\Desktop\Scraper\Form PDFs\Launceston22032026.txt" --race 1 --data output/claude_data
```
Expected: simulation runs without error, outputs win% for 9 runners,
no runner exceeds 75% win probability.

### Test 10 — All races parse without error
```python
races = parse_fields_doc(text, race_no=0)
assert len(races) == 9, f"Expected 9 races, got {len(races)}"
for r in races:
    assert len(r.runners) > 0, f"R{r.race_no} has no active runners"
    assert r.distance_m > 0, f"R{r.race_no} has no distance"
    assert r.start_type in ('MS', 'SS'), f"R{r.race_no} invalid start type"
```

---

## EXECUTION ORDER

1. Update Runner dataclass with new fields
2. Add helper functions: derive_barrier(), parse_nr_fields_doc(),
   parse_fields_race_header(), _is_fields_doc()
3. Add parse_fields_runner_line() — this is the hardest part,
   take time to get trainer/driver splitting right
4. Add parse_fields_doc() with page break handling
5. Update parse_form() dispatcher
6. Run Tests 1-8 (unit tests, no sim needed)
7. Run Test 9 (smoke test with sim)
8. Run Test 10 (all races)
9. If all pass: run full backtest against Launceston 22 Mar 2026 results

---

## KNOWN EDGE CASES TO HANDLE

1. **Page break mid-runner**: "Page 1 of 34 72064 KEAYANG FITZY..." — the
   page metadata interrupts a runner line. Split on `Page \d+ of \d+` and
   process the remainder as a runner line.

2. **ODS vs ODM**: ODS = out of draw in standing starts only.
   ODM = out of draw in mobiles only. Set the correct flag.
   RODS = restricted ODS (one more chance in standing draw).

3. **NR with no number**: Some conditions like "MAIDEN" have no NR.
   Set nr = 0.0 and let the sim handle via data_confidence.

4. **Multi-word horse names with NZ/GB suffix**: "ANOTHER NIEN" has no
   country suffix. "WAVETHEBILL NZ" does. The suffix is part of the horse
   name for slug matching — preserve it.

5. **Driver name ambiguity**: "W J Yole Dylan Ford" — is "W J Yole" the
   trainer and "Dylan Ford" the driver, or is "W J Yole Dylan" some other
   split? Always use the known trainer/driver lists from CSVs.
   If uncertain, log a WARNING with the raw line for manual checking.

6. **Odds as SCR**: When odds column is "SCR", horse is scratched.
   The HCP column will also be "SCR".

7. **Timestamp line at end**: "As at: Thu, 26 Mar 2026 04:42 pm" — skip.

---
_Generated by Analyst Claude — for Claude Code autonomous execution_
_Test file: C:\Users\evil_\Desktop\Scraper\Form PDFs\Launceston22032026.txt_
