"""
make_synthetic_data.py — Generate minimal synthetic CSV data for Race 4 horses.

Creates realistic-but-fictional race records that match the exact schema
expected by feature_extractor.py, so the lookup-fix changes can be verified
end-to-end without network access to the Stride API.

Stored in output/claude_data/ alongside what real scraping would produce.

IMPORTANT: These figures are SYNTHETIC for testing purposes only.
Real data must be obtained by running:
    python harness_scraper.py --stride-horses tassie_horses.txt --resume --out output/claude_data
"""

import os, random
import pandas as pd
from datetime import datetime, timedelta

random.seed(99)
OUT = "output/claude_data"
os.makedirs(OUT, exist_ok=True)

# Race 4 horses with their actual NR, barrier, and driver from the form
RACE4 = [
    # (display_name, slug, barrier, driver, nr)
    ("James Cagney NZ",   "james-cagney-nz",   1, "Wayne Yole",       65),
    ("Always Aurora",     "always-aurora",      2, "Adrian Duggan",    65),
    ("Thee Old Bomb NZ",  "thee-old-bomb-nz",   3, "Wayne Yole",       64),
    ("My Way NZ",         "my-way-nz",          4, "Wayne Yole",       66),
    ("Imperial Laz NZ",   "imperial-laz-nz",    5, "Juanita Mckenzie", 73),
    ("Ray Dan",           "ray-dan",            6, "Tammy Langley",    68),
    ("Custom Harley",     "custom-harley",      7, "Heath Woods",      71),
    ("Zara Tindall NZ",   "zara-tindall-nz",    8, "Michael Laugher",  76),
    ("Mr Bondi NZ",       "mr-bondi-nz",        9, "Wayne Yole",       72),
    ("Cuzzy Bro",         "cuzzy-bro",         10, "Todd Rattray",     77),
    ("Captain Pins NZ",   "captain-pins-nz",   11, "Tammy Langley",    74),
]

# Actual race result (for realistic win/place numbers)
RESULT_ORDER = [
    "ray-dan",        # 1st
    "mr-bondi-nz",    # 2nd
    "imperial-laz-nz",# 3rd (checked at 400m)
    "captain-pins-nz",# 4th
    "cuzzy-bro",
    "zara-tindall-nz",
    "always-aurora",
    "custom-harley",
    "my-way-nz",
    "james-cagney-nz",
    "thee-old-bomb-nz", # broke hopple strap
]

TRACK = "Hobart"
RACE_DATE = datetime(2026, 3, 15)


def make_dates(n: int, end: datetime) -> list:
    """Generate n race dates going backward ~14 days each."""
    dates = []
    d = end - timedelta(days=14)
    for _ in range(n):
        dates.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=random.randint(10, 21))
    return dates


# ---------------------------------------------------------------------------
# stride_results.csv
# ---------------------------------------------------------------------------
# Columns used by feature_extractor:
#   Horse, Slug, Date, Track, Race_No, Place, Driver, Trainer,
#   800_Margin_m, 400_Margin_m, Last_800m_Pos, 400_0_Time, Mile_Rate,
#   800_Width, 400_Width, Start_Type, Track_Condition, Field_Size

stride_rows = []
for display, slug, barrier, driver, nr in RACE4:
    n_runs = random.randint(5, 10)
    dates = make_dates(n_runs, RACE_DATE)
    # Synthetic performance: better NR → better average finishing position
    avg_pos = max(1, 12 - (nr - 60) // 3 + random.randint(-2, 2))

    for i, date in enumerate(dates):
        place = max(1, min(12, avg_pos + random.randint(-3, 3)))
        field_size = random.randint(8, 12)
        # 800m margin: approx metres behind leader (better horses run tighter)
        base_800m = max(0.5, (nr - 60) * -0.3 + random.uniform(-2, 4))
        base_800m = max(0.5, base_800m)  # ensure positive margin
        mile_rate = 115.0 + random.uniform(-3, 3) - (nr - 60) * 0.1

        # Introduce mild trend: later dates (lower index) = slightly better
        trend_adjust = i * 0.05

        stride_rows.append({
            "Horse":        display,
            "Slug":         slug,
            "Date":         date,
            "Track":        TRACK if random.random() > 0.3 else random.choice(["Launceston", "Burnie"]),
            "Race_No":      random.randint(1, 8),
            "Place":        place,
            "Driver":       driver,
            "Trainer":      f"Trainer_{slug[:6]}",
            "Field_Size":   field_size,
            "800_Margin_m": round(base_800m + trend_adjust, 1),
            "400_Margin_m": round(base_800m * 0.6 + random.uniform(-1, 2), 1),
            "Last_800m_Pos": max(1, place - 1 + random.randint(-1, 2)),
            "400_0_Time":   round(29.5 - (nr - 60) * 0.05 + random.uniform(-0.5, 0.5), 2),
            "Mile_Rate":    round(mile_rate + trend_adjust * -0.1, 2),
            "800_Width":    round(random.uniform(0.5, 3.5), 1),
            "400_Width":    round(random.uniform(0.5, 2.5), 1),
            "Start_Type":   "MS",
            "Track_Condition": random.choice(["Good", "Good", "Slow"]),
        })

sr_df = pd.DataFrame(stride_rows)
sr_path = os.path.join(OUT, "stride_results.csv")
sr_df.to_csv(sr_path, index=False)
print(f"✓ stride_results.csv   — {len(sr_df)} rows, {sr_df['Slug'].nunique()} horses")


# ---------------------------------------------------------------------------
# stride_profiles.csv
# ---------------------------------------------------------------------------
# Columns used by feature_extractor (via VENUE_* config maps):
#   Horse, Slug, Trainer, Last_Win_Date, Last_Win_Venue,
#   Hobart_Wins, Hobart_Places, Hobart_Starts, Hobart_Win_Pct,
#   Career_Starts, Career_Wins, Career_Win_Pct, Career_Place_Pct

profile_rows = []
for display, slug, barrier, driver, nr in RACE4:
    # Synthesise Hobart-specific win record proportional to NR
    hobart_starts = random.randint(4, 15)
    hobart_wins   = max(0, int(hobart_starts * (nr - 60) / 80 + random.uniform(-0.5, 1.5)))
    hobart_places = min(hobart_starts, hobart_wins + random.randint(0, 3))
    last_win_days = random.randint(30, 200)
    last_win_date = (RACE_DATE - timedelta(days=last_win_days)).strftime("%Y-%m-%d")

    career_starts = random.randint(20, 80)
    career_wins   = max(0, int(career_starts * (nr - 60) / 100 + random.uniform(-1, 3)))
    career_places = min(career_starts, career_wins + random.randint(2, 10))

    profile_rows.append({
        "Horse":            display,
        "Slug":             slug,
        "Trainer":          f"Trainer_{slug[:6]}",
        "Last_Win_Date":    last_win_date,
        "Last_Win_Venue":   TRACK,
        "Career_Starts":    career_starts,
        "Career_Wins":      career_wins,
        "Career_Win_Pct":   f"{career_wins/career_starts:.1%}" if career_starts > 0 else "0.0%",
        "Career_Place_Pct": f"{career_places/career_starts:.1%}" if career_starts > 0 else "0.0%",
        "Hobart_Starts":    hobart_starts,
        "Hobart_Wins":      hobart_wins,
        "Hobart_Places":    hobart_places,
        "Hobart_Win_Pct":   f"{hobart_wins/hobart_starts:.1%}" if hobart_starts > 0 else "0.0%",
        "Launceston_Starts": random.randint(2, 8),
        "Launceston_Wins":   random.randint(0, 2),
        "Launceston_Places": random.randint(0, 3),
        "Launceston_Win_Pct": f"{random.uniform(0, 0.25):.1%}",
        "Burnie_Starts":    random.randint(1, 6),
        "Burnie_Wins":      random.randint(0, 1),
        "Burnie_Places":    random.randint(0, 2),
        "Burnie_Win_Pct":   f"{random.uniform(0, 0.2):.1%}",
    })

sp_df = pd.DataFrame(profile_rows)
sp_path = os.path.join(OUT, "stride_profiles.csv")
sp_df.to_csv(sp_path, index=False)
print(f"✓ stride_profiles.csv  — {len(sp_df)} rows")


# ---------------------------------------------------------------------------
# drivers_profile.csv
# ---------------------------------------------------------------------------
# Columns: Name, driver.lifetime_summary.starts, driver.lifetime_summary.wins,
#          driver.current_season_summary.starts, driver.current_season_summary.wins,
#          driver.venue_summary.<Track>.wins, driver.venue_summary.<Track>.starts

DRIVERS = list({h[3] for h in RACE4})
driver_rows = []
for drv in DRIVERS:
    lifetime_starts = random.randint(200, 1200)
    lifetime_wins   = int(lifetime_starts * random.uniform(0.08, 0.22))
    season_starts   = random.randint(20, 80)
    season_wins     = int(season_starts * random.uniform(0.10, 0.25))
    driver_rows.append({
        "Name": drv,
        "driver.lifetime_summary.starts": lifetime_starts,
        "driver.lifetime_summary.wins":   lifetime_wins,
        "driver.current_season_summary.starts": season_starts,
        "driver.current_season_summary.wins":   season_wins,
        f"driver.venue_summary.{TRACK}.starts": random.randint(10, 60),
        f"driver.venue_summary.{TRACK}.wins":   random.randint(1, 12),
    })

dp_df = pd.DataFrame(driver_rows)
dp_path = os.path.join(OUT, "drivers_profile.csv")
dp_df.to_csv(dp_path, index=False)
print(f"✓ drivers_profile.csv  — {len(dp_df)} drivers")


# ---------------------------------------------------------------------------
# driver_results_recent.csv
# ---------------------------------------------------------------------------
# Columns: Trainer (= driver name, API quirk), Track, Place, Date

dr_rows = []
for drv in DRIVERS:
    n = random.randint(15, 40)
    for _ in range(n):
        track = random.choice([TRACK, "Launceston", "Burnie", TRACK])
        dr_rows.append({
            "Trainer": drv,
            "Track":   track,
            "Place":   random.randint(1, 10),
            "Date":    (RACE_DATE - timedelta(days=random.randint(1, 180))).strftime("%Y-%m-%d"),
        })

dr_df = pd.DataFrame(dr_rows)
dr_path = os.path.join(OUT, "driver_results_recent.csv")
dr_df.to_csv(dr_path, index=False)
print(f"✓ driver_results_recent.csv — {len(dr_df)} rows")


# ---------------------------------------------------------------------------
# trainer_results_recent.csv
# ---------------------------------------------------------------------------
tr_rows = []
trainers = {f"Trainer_{h[1][:6]}" for h in RACE4}
for tr in trainers:
    for _ in range(random.randint(5, 20)):
        tr_rows.append({
            "Trainer": tr,
            "Track":   random.choice([TRACK, "Launceston", "Burnie"]),
            "Place":   random.randint(1, 10),
            "Date":    (RACE_DATE - timedelta(days=random.randint(1, 90))).strftime("%Y-%m-%d"),
        })

tr_df = pd.DataFrame(tr_rows)
tr_path = os.path.join(OUT, "trainer_results_recent.csv")
tr_df.to_csv(tr_path, index=False)
print(f"✓ trainer_results_recent.csv — {len(tr_df)} rows")


# ---------------------------------------------------------------------------
# stewards_notes.csv  (include Ray Dan hopple incident)
# ---------------------------------------------------------------------------
sn_rows = [
    {"Horse": "Ray Dan",        "Horse_Slug": "ray-dan",
     "Date": "2026-03-15", "Note": "checked at 400m, held up for clear run"},
    {"Horse": "Imperial Laz NZ","Horse_Slug": "imperial-laz-nz",
     "Date": "2026-03-15", "Note": "checked by Ray Dan at 400m — stewards noted incident"},
    {"Horse": "Thee Old Bomb NZ","Horse_Slug": "thee-old-bomb-nz",
     "Date": "2026-03-15", "Note": "broke a hopple strap at 400m — eased to finish"},
    {"Horse": "Ray Dan",        "Horse_Slug": "ray-dan",
     "Date": "2026-02-20", "Note": "galloped when checked near 400m mark"},
]
sn_df = pd.DataFrame(sn_rows)
sn_path = os.path.join(OUT, "stewards_notes.csv")
sn_df.to_csv(sn_path, index=False)
print(f"✓ stewards_notes.csv   — {len(sn_df)} rows")


# Empty stand-downs and penalties (no horses on stand-down)
for fname in ("stewards_stand_downs.csv", "stewards_penalties.csv"):
    pd.DataFrame(columns=["Horse", "Horse_Slug", "Date", "Note"]).to_csv(
        os.path.join(OUT, fname), index=False
    )
    print(f"✓ {fname} — empty (no stand-downs)")


# ---------------------------------------------------------------------------
# dividends.csv
# ---------------------------------------------------------------------------
div_rows = []
for i in range(20):
    div_rows.append({
        "Date":            (RACE_DATE - timedelta(days=i * 14)).strftime("%Y-%m-%d"),
        "Track":           TRACK,
        "Race_No":         random.randint(1, 8),
        "Start_Type":      "MS",
        "Track_Condition": random.choice(["Good", "Good", "Slow"]),
    })
div_df = pd.DataFrame(div_rows)
div_path = os.path.join(OUT, "dividends.csv")
div_df.to_csv(div_path, index=False)
print(f"✓ dividends.csv        — {len(div_df)} rows")

print(f"\n✅ Synthetic data written to {OUT}/")
print("   Re-run: python run_diagnostic.py")
