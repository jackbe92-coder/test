import sys
sys.path.insert(0, r'C:\Users\evil_\Desktop\Scraper')
from form_parser import extract_races_from_pdf

track, date, races = extract_races_from_pdf(r'C:\Users\evil_\Desktop\Scraper\Hobart Harness 15-03-2026.pdf')
print(f"Track: {track}")
print(f"Date: {date}")
print(f"Races found: {len(races)}")
for r in races:
    print(f"\nR{r.race_no} — {r.nr_conditions} | {r.distance_m}m | {len(r.runners)} runners")
    for runner in r.runners:
        print(f"  {runner.tab_no:>2}. {runner.horse:<30} Driver: {runner.driver:<22} NR: {runner.nr}")
