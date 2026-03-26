"""Validation tests for fields document parser — from CLAUDE_CODE_FIELDS_PARSER.md"""

import sys
sys.path.insert(0, 'Sim')

from form_parser import parse_fields_doc

# Load test file
with open(r'Form PDFs\Launceston22032026.txt', encoding='utf-8') as f:
    text = f.read()

# Parse all races once
all_races = parse_fields_doc(text)

def get_race(n):
    return next((r for r in all_races if r.race_no == n), None)

passed = 0
failed = 0

def check(label, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS  {label}")
        passed += 1
    else:
        print(f"  FAIL  {label}  {detail}")
        failed += 1


# Test 10 — All races parse without error (run first as sanity check)
print("\n--- Test 10: All races parse ---")
check("9 races found", len(all_races) == 9, f"got {len(all_races)}")
for r in all_races:
    check(f"R{r.race_no} has runners", len(r.runners) > 0)
    check(f"R{r.race_no} has distance", r.distance_m > 0, f"got {r.distance_m}")
    check(f"R{r.race_no} valid start type", r.start_type in ('MS', 'SS'), f"got {r.start_type}")


# Test 1 — Field counts match (includes scratched)
print("\n--- Test 1: Field counts ---")
expected = {1: 9, 2: 11, 3: 11, 4: 10, 5: 12, 6: 10, 7: 13, 8: 10, 9: 12}  # spec had R6=9 but actual=10 (includes scratched Triedtotellya)
for race_no, count in expected.items():
    race = get_race(race_no)
    if race:
        check(f"R{race_no}: {count} total runners", len(race.all_runners) == count,
              f"got {len(race.all_runners)}")
    else:
        check(f"R{race_no}: found", False, "race not found")


# Test 2 — Active field excludes scratched
print("\n--- Test 2: Scratched exclusion ---")
r6 = get_race(6)
check("R6 active runners = 9", len(r6.runners) == 9, f"got {len(r6.runners)}")
check("Triedtotellya is scratched",
      any(r.scratched and 'TRIEDTOTELLYA' in r.horse.upper() for r in r6.all_runners))

r7 = get_race(7)
check("R7 active runners = 12", len(r7.runners) == 12, f"got {len(r7.runners)}")

r9 = get_race(9)
check("R9 active runners = 11", len(r9.runners) == 11, f"got {len(r9.runners)}")


# Test 3 — Barrier derivation
print("\n--- Test 3: Barrier derivation ---")
r1 = get_race(1)
wavethebill = next((r for r in r1.runners if 'WAVE' in r.horse.upper()), None)
another_nien = next((r for r in r1.runners if 'NIEN' in r.horse.upper()), None)
check("Wavethebill found", wavethebill is not None)
if wavethebill:
    check("Wavethebill barrier = 1", wavethebill.barrier == 1, f"got {wavethebill.barrier}")
check("Another Nien found", another_nien is not None)
if another_nien:
    check("Another Nien barrier = 11", another_nien.barrier == 11, f"got {another_nien.barrier}")


# Test 4 — Adjusted NR
print("\n--- Test 4: Adjusted NR ---")
if wavethebill:
    check("Wavethebill nr = 70.0", wavethebill.nr == 70.0, f"got {wavethebill.nr}")
    check("Wavethebill nr_raw = 61.0", wavethebill.nr_raw == 61.0, f"got {wavethebill.nr_raw}")
    check("Wavethebill nr_adjusted = 70.0", wavethebill.nr_adjusted == 70.0, f"got {wavethebill.nr_adjusted}")


# Test 5 — Standing start fields
print("\n--- Test 5: Standing start ---")
r4 = get_race(4)
check("R4 start_type = SS", r4.start_type == 'SS', f"got {r4.start_type}")

pardoe = next((r for r in r4.runners if 'PARDOE' in r.horse.upper()), None)
stepping = next((r for r in r4.runners if 'STEPPING' in r.horse.upper()), None)
magnetic = next((r for r in r4.runners if 'MAGNETIC' in r.horse.upper()), None)

check("Pardoe Plugga found", pardoe is not None)
if pardoe:
    check("Pardoe handicap_m = 0", pardoe.handicap_m == 0, f"got {pardoe.handicap_m}")
    check("Pardoe is_front_tape = True", pardoe.is_front_tape == True, f"got {pardoe.is_front_tape}")
check("Stepping Stones found", stepping is not None)
if stepping:
    check("Stepping handicap_m = 10", stepping.handicap_m == 10, f"got {stepping.handicap_m}")
check("Magnetic Terror found", magnetic is not None)
if magnetic:
    check("Magnetic handicap_m = 20", magnetic.handicap_m == 20, f"got {magnetic.handicap_m}")


# Test 6 — ODM flags
print("\n--- Test 6: ODM flags ---")
r8 = get_race(8)
muzzame = next((r for r in r8.runners if 'MUZZAME' in r.horse.upper()), None)
iden = next((r for r in r8.runners if 'IDEN' in r.horse.upper()), None)
check("Muzzame Mate found", muzzame is not None)
if muzzame:
    check("Muzzame odm_mobile = True", muzzame.odm_mobile == True, f"got {muzzame.odm_mobile}")
check("Iden Regal Wave found", iden is not None)
if iden:
    check("Iden odm_mobile = True", iden.odm_mobile == True, f"got {iden.odm_mobile}")


# Test 7 — Conditional driver flag
print("\n--- Test 7: Conditional driver ---")
if another_nien:
    check("Another Nien is_conditional", another_nien.is_conditional == True,
          f"got {another_nien.is_conditional}")
    check("Another Nien driver = 'Jacob Duggan'", another_nien.driver == 'Jacob Duggan',
          f"got {another_nien.driver!r}")
    check("No (C) in driver name", '(C)' not in another_nien.driver)


# Test 8 — Page break rejoining
print("\n--- Test 8: Page break ---")
keayang = next((r for r in r4.runners if 'KEAYANG' in r.horse.upper()), None)
check("Keayang Fitzy found (page break handled)", keayang is not None)
if keayang:
    check("Keayang nr = 102.0", keayang.nr == 102.0, f"got {keayang.nr}")
    check("Keayang tab_no = 4", keayang.tab_no == 4, f"got {keayang.tab_no}")

# Also check R7 page break (Grizzly Montana, tab 13)
grizzly = next((r for r in r7.all_runners if 'GRIZZLY' in r.horse.upper()), None)
check("Grizzly Montana found (page break 2)", grizzly is not None)
if grizzly:
    check("Grizzly tab_no = 13", grizzly.tab_no == 13, f"got {grizzly.tab_no}")


# Summary
print(f"\n{'='*40}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*40}")
sys.exit(1 if failed else 0)
