"""
make_synthetic_data.py — Generate synthetic CSV data for Burnie Race 4, 13 Mar 2026.

IMPORTANT: These figures are SYNTHETIC for testing purposes only.
Real data must be obtained by running:
    python harness_scraper.py --stride-horses tassie_horses.txt --resume --out output/claude_data
"""

import os, random
import pandas as pd
from datetime import datetime, timedelta

random.seed(42)
OUT = "output/claude_data"
os.makedirs(OUT, exist_ok=True)

# Burnie Race 4 — Friday 13 Mar 2026 — 2180m Mobile Start
# (display_name, slug, barrier, driver, nr, n_runs, burnie_win_pct_override)
# Run counts and win % from user-provided data:
#   Away Game  : 20 runs, Burnie_Win_Pct 16.7%
#   Modern Jive: 43 runs, Burnie_Win_Pct 20.0%
RACE4_BURNIE = [
    ("Away Game",   "away-game",   1, "Liam Older",      45, 20, 0.167),
    ("Nikita Jo",   "nikita-jo",   2, "Ryan Backhouse",  50, 26, None),
    ("Rock Amour",  "rock-amour",  3, "Charlie Castles", 46, 14, None),
    ("La Pierre",   "la-pierre",   4, "Brent Parish",    47, 22, None),
    ("Modern Jive", "modern-jive", 5, "Gareth Rattray",  52, 43, 0.200),
    ("Andaman Bay", "andaman-bay", 6, "Mark Yole",       54, 42, None),
]

TRACK    = "Burnie"
RACE_DATE = datetime(2026, 3, 13)


def make_dates(n: int, end: datetime) -> list:
    dates = []
    d = end - timedelta(days=14)
    for _ in range(n):
        dates.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=random.randint(10, 21))
    return dates


# ---------------------------------------------------------------------------
# stride_results.csv
# ---------------------------------------------------------------------------
stride_rows = []
for display, slug, barrier, driver, nr, n_runs, _wp in RACE4_BURNIE:
    dates = make_dates(n_runs, RACE_DATE)
    avg_pos = max(1, 7 - (nr - 45) // 2 + random.randint(-1, 1))

    for i, date in enumerate(dates):
        place      = max(1, min(8, avg_pos + random.randint(-2, 2)))
        field_size = random.randint(6, 8)
        base_800m  = max(0.3, (nr - 45) * 0.12 + random.uniform(-0.5, 2.0))
        mile_rate  = 116.0 + random.uniform(-2, 2) + (nr - 45) * 0.06
        trend      = i * 0.04

        stride_rows.append({
            "Horse":           display,
            "Slug":            slug,
            "Date":            date,
            "Track":           TRACK if random.random() > 0.35
                               else random.choice(["Hobart", "Launceston"]),
            "Race_No":         random.randint(1, 8),
            "Place":           place,
            "Driver":          driver,
            "Trainer":         f"Trainer_{slug[:6]}",
            "Field_Size":      field_size,
            "800_Margin_m":    round(base_800m + trend, 1),
            "400_Margin_m":    round(base_800m * 0.55 + random.uniform(-0.5, 1.5), 1),
            "Last_800m_Pos":   max(1, place - 1 + random.randint(-1, 2)),
            "400_0_Time":      round(29.2 + (nr - 45) * 0.04 + random.uniform(-0.4, 0.4), 2),
            "Mile_Rate":       round(mile_rate - trend * 0.1, 2),
            "800_Width":       round(random.uniform(0.5, 3.0), 1),
            "400_Width":       round(random.uniform(0.3, 2.0), 1),
            "Start_Type":      "MS",
            "Track_Condition": random.choice(["Good", "Good", "Slow"]),
        })

sr_df = pd.DataFrame(stride_rows)
sr_df.to_csv(os.path.join(OUT, "stride_results.csv"), index=False)
print(f"✓ stride_results.csv   — {len(sr_df)} rows, {sr_df['Slug'].nunique()} horses")


# ---------------------------------------------------------------------------
# stride_profiles.csv
# ---------------------------------------------------------------------------
profile_rows = []
for display, slug, barrier, driver, nr, n_runs, burnie_wp in RACE4_BURNIE:
    last_win_date  = (RACE_DATE - timedelta(days=random.randint(30, 200))).strftime("%Y-%m-%d")
    career_starts  = n_runs
    career_wins    = max(0, int(career_starts * (nr - 40) / 120 + random.uniform(-0.5, 2)))
    career_places  = min(career_starts, career_wins + random.randint(2, 8))

    # Burnie — use exact override where known, else derive from NR
    if burnie_wp is not None:
        burnie_starts = n_runs
        burnie_wins   = max(0, round(burnie_starts * burnie_wp))
        burnie_places = min(burnie_starts, burnie_wins + random.randint(1, 5))
        b_pct         = burnie_wp
    else:
        burnie_starts = random.randint(8, 14)
        burnie_wins   = max(0, int(burnie_starts * (nr - 40) / 150 + random.uniform(-0.3, 1)))
        burnie_places = min(burnie_starts, burnie_wins + random.randint(1, 4))
        b_pct         = burnie_wins / burnie_starts if burnie_starts else 0.0

    hobart_starts = random.randint(3, 8)
    hobart_wins   = max(0, int(hobart_starts * (nr - 40) / 160 + random.uniform(-0.3, 1)))
    hobart_places = min(hobart_starts, hobart_wins + random.randint(0, 3))

    lc_starts = random.randint(2, 6)
    lc_wins   = max(0, int(lc_starts * (nr - 40) / 160 + random.uniform(-0.2, 0.8)))
    lc_places = min(lc_starts, lc_wins + random.randint(0, 2))

    profile_rows.append({
        "Horse":             display,
        "Slug":              slug,
        "Trainer":           f"Trainer_{slug[:6]}",
        "Last_Win_Date":     last_win_date,
        "Last_Win_Venue":    TRACK,
        "Career_Starts":     career_starts,
        "Career_Wins":       career_wins,
        "Career_Win_Pct":    f"{career_wins/career_starts:.1%}" if career_starts else "0.0%",
        "Career_Place_Pct":  f"{career_places/career_starts:.1%}" if career_starts else "0.0%",
        "Burnie_Starts":     burnie_starts,
        "Burnie_Wins":       burnie_wins,
        "Burnie_Places":     burnie_places,
        "Burnie_Win_Pct":    f"{b_pct:.1%}",
        "Hobart_Starts":     hobart_starts,
        "Hobart_Wins":       hobart_wins,
        "Hobart_Places":     hobart_places,
        "Hobart_Win_Pct":    f"{hobart_wins/hobart_starts:.1%}" if hobart_starts else "0.0%",
        "Launceston_Starts": lc_starts,
        "Launceston_Wins":   lc_wins,
        "Launceston_Places": lc_places,
        "Launceston_Win_Pct": f"{lc_wins/lc_starts:.1%}" if lc_starts else "0.0%",
    })

sp_df = pd.DataFrame(profile_rows)
sp_df.to_csv(os.path.join(OUT, "stride_profiles.csv"), index=False)
print(f"✓ stride_profiles.csv  — {len(sp_df)} rows")


# ---------------------------------------------------------------------------
# drivers_profile.csv
# ---------------------------------------------------------------------------
DRIVERS = list({h[3] for h in RACE4_BURNIE})
driver_rows = []
for drv in DRIVERS:
    ls = random.randint(200, 1200)
    lw = int(ls * random.uniform(0.08, 0.20))
    ss = random.randint(20, 80)
    sw = int(ss * random.uniform(0.10, 0.25))
    driver_rows.append({
        "Name": drv,
        "driver.lifetime_summary.starts":           ls,
        "driver.lifetime_summary.wins":             lw,
        "driver.current_season_summary.starts":     ss,
        "driver.current_season_summary.wins":       sw,
        "driver.venue_summary.Burnie.starts":       random.randint(15, 80),
        "driver.venue_summary.Burnie.wins":         random.randint(1, 15),
        "driver.venue_summary.Hobart.starts":       random.randint(10, 60),
        "driver.venue_summary.Hobart.wins":         random.randint(1, 12),
        "driver.venue_summary.Launceston.starts":   random.randint(5, 30),
        "driver.venue_summary.Launceston.wins":     random.randint(0, 6),
    })

dp_df = pd.DataFrame(driver_rows)
dp_df.to_csv(os.path.join(OUT, "drivers_profile.csv"), index=False)
print(f"✓ drivers_profile.csv  — {len(dp_df)} drivers")


# ---------------------------------------------------------------------------
# driver_results_recent.csv
# ---------------------------------------------------------------------------
dr_rows = []
for drv in DRIVERS:
    for _ in range(random.randint(20, 45)):
        dr_rows.append({
            "Trainer": drv,
            "Track":   random.choice(["Burnie", "Burnie", "Hobart", "Launceston"]),
            "Place":   random.randint(1, 8),
            "Date":    (RACE_DATE - timedelta(days=random.randint(1, 180))).strftime("%Y-%m-%d"),
        })

dr_df = pd.DataFrame(dr_rows)
dr_df.to_csv(os.path.join(OUT, "driver_results_recent.csv"), index=False)
print(f"✓ driver_results_recent.csv — {len(dr_df)} rows")


# ---------------------------------------------------------------------------
# trainer_results_recent.csv
# ---------------------------------------------------------------------------
tr_rows = []
for slug in [h[1] for h in RACE4_BURNIE]:
    tr = f"Trainer_{slug[:6]}"
    for _ in range(random.randint(5, 20)):
        tr_rows.append({
            "Trainer": tr,
            "Track":   random.choice(["Burnie", "Burnie", "Hobart", "Launceston"]),
            "Place":   random.randint(1, 8),
            "Date":    (RACE_DATE - timedelta(days=random.randint(1, 90))).strftime("%Y-%m-%d"),
        })

tr_df = pd.DataFrame(tr_rows)
tr_df.to_csv(os.path.join(OUT, "trainer_results_recent.csv"), index=False)
print(f"✓ trainer_results_recent.csv — {len(tr_df)} rows")


# ---------------------------------------------------------------------------
# stewards_notes.csv  — no incidents for this field
# ---------------------------------------------------------------------------
pd.DataFrame(columns=["Horse", "Horse_Slug", "Date", "Note"]).to_csv(
    os.path.join(OUT, "stewards_notes.csv"), index=False)
print("✓ stewards_notes.csv   — empty (no incidents)")

for fname in ("stewards_stand_downs.csv", "stewards_penalties.csv"):
    pd.DataFrame(columns=["Horse", "Horse_Slug", "Date", "Note"]).to_csv(
        os.path.join(OUT, fname), index=False)
    print(f"✓ {fname} — empty")


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

pd.DataFrame(div_rows).to_csv(os.path.join(OUT, "dividends.csv"), index=False)
print(f"✓ dividends.csv        — {len(div_rows)} rows")

print(f"\n✅ Burnie Race 4 data written to {OUT}/")
print("   Horses:", ", ".join(h[0] for h in RACE4_BURNIE))
