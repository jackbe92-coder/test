"""
make_synthetic_data.py — Generate synthetic CSV data for the full Tasmanian
harness racing horse pool, covering both meetings currently in use:

  • Hobart Race 4  — Sun 15 Mar 2026 — 1609m MS
  • Burnie Race 4  — Fri 13 Mar 2026 — 2180m MS

The CSVs represent a shared horse/driver database (all runners, all tracks).
They must NOT be replaced or cleared when switching between race cards.

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

# ---------------------------------------------------------------------------
# Horse pools
# ---------------------------------------------------------------------------

# Hobart Race 4 — Sun 15 Mar 2026 — 1609m MS
# (display_name, slug, barrier, driver, nr, n_runs)
HOBART_HORSES = [
    ("James Cagney NZ",  "james-cagney-nz",   1, "Wayne Yole",       65, 8),
    ("Always Aurora",    "always-aurora",      2, "Adrian Duggan",    65, 8),
    ("Thee Old Bomb NZ", "thee-old-bomb-nz",   3, "Wayne Yole",       64, 7),
    ("My Way NZ",        "my-way-nz",          4, "Wayne Yole",       66, 9),
    ("Imperial Laz NZ",  "imperial-laz-nz",    5, "Juanita Mckenzie", 73, 8),
    ("Ray Dan",          "ray-dan",            6, "Tammy Langley",    68, 7),
    ("Custom Harley",    "custom-harley",      7, "Heath Woods",      71, 8),
    ("Zara Tindall NZ",  "zara-tindall-nz",    8, "Michael Laugher",  76, 7),
    ("Mr Bondi NZ",      "mr-bondi-nz",        9, "Wayne Yole",       72, 8),
    ("Cuzzy Bro",        "cuzzy-bro",         10, "Todd Rattray",     77, 7),
    ("Captain Pins NZ",  "captain-pins-nz",   11, "Tammy Langley",    74, 7),
]

# Burnie Race 4 — Fri 13 Mar 2026 — 2180m MS
# Run counts and Burnie_Win_Pct from user-provided data:
#   Away Game  : 20 runs, 16.7%   Modern Jive: 43 runs, 20.0%
# (display_name, slug, barrier, driver, nr, n_runs, burnie_win_pct_override)
BURNIE_HORSES = [
    ("Away Game",   "away-game",   1, "Liam Older",      45, 20, 0.167),
    ("Nikita Jo",   "nikita-jo",   2, "Ryan Backhouse",  50, 26, None),
    ("Rock Amour",  "rock-amour",  3, "Charlie Castles", 46, 14, None),
    ("La Pierre",   "la-pierre",   4, "Brent Parish",    47, 22, None),
    ("Modern Jive", "modern-jive", 5, "Gareth Rattray",  52, 43, 0.200),
    ("Andaman Bay", "andaman-bay", 6, "Mark Yole",       54, 42, None),
]

HOBART_DATE = datetime(2026, 3, 15)
BURNIE_DATE = datetime(2026, 3, 13)


def make_dates(n: int, end: datetime) -> list:
    dates, d = [], end - timedelta(days=14)
    for _ in range(n):
        dates.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=random.randint(10, 21))
    return dates


# ---------------------------------------------------------------------------
# stride_results.csv  — one row per race start, all horses all tracks
# ---------------------------------------------------------------------------
stride_rows = []

# Hobart horses
for display, slug, barrier, driver, nr, n_runs in HOBART_HORSES:
    dates   = make_dates(n_runs, HOBART_DATE)
    avg_pos = max(1, 12 - (nr - 60) // 3 + random.randint(-2, 2))
    for i, date in enumerate(dates):
        place      = max(1, min(12, avg_pos + random.randint(-3, 3)))
        field_size = random.randint(8, 12)
        base_800m  = max(0.5, (nr - 60) * -0.3 + random.uniform(-2, 4))
        mile_rate  = 115.0 + random.uniform(-3, 3) - (nr - 60) * 0.1
        trend      = i * 0.05
        sp_raw = round(random.uniform(2.5, 18.0), 1)
        stride_rows.append({
            "Horse": display, "Slug": slug, "Date": date,
            "Track": "Hobart" if random.random() > 0.3 else random.choice(["Launceston", "Burnie"]),
            "Race_No": random.randint(1, 8), "Place": place, "Driver": driver,
            "Trainer": driver,  # use driver name so trainer fallback lookup works
            "Field_Size": field_size,
            "Starters": field_size,
            "Distance_m": random.choice([1609, 1609, 2080, 2620]),
            "SP": f"${sp_raw}",
            "800_Margin_m":  round(base_800m + trend, 1),
            "400_Margin_m":  round(base_800m * 0.6 + random.uniform(-1, 2), 1),
            "Last_800m_Pos": max(1, place - 1 + random.randint(-1, 2)),
            "400_0_Time":    round(29.5 - (nr - 60) * 0.05 + random.uniform(-0.5, 0.5), 2),
            "Mile_Rate":     round(mile_rate - trend * 0.1, 2),
            "800_Width":     round(random.uniform(0.5, 3.5), 1),
            "400_Width":     round(random.uniform(0.5, 2.5), 1),
            "Start_Type": "MS", "Track_Condition": random.choice(["Good", "Good", "Slow"]),
        })

# Burnie horses
for display, slug, barrier, driver, nr, n_runs, _wp in BURNIE_HORSES:
    dates   = make_dates(n_runs, BURNIE_DATE)
    avg_pos = max(1, 7 - (nr - 45) // 2 + random.randint(-1, 1))
    for i, date in enumerate(dates):
        place      = max(1, min(8, avg_pos + random.randint(-2, 2)))
        field_size = random.randint(6, 8)
        base_800m  = max(0.3, (nr - 45) * 0.12 + random.uniform(-0.5, 2.0))
        mile_rate  = 116.0 + random.uniform(-2, 2) + (nr - 45) * 0.06
        trend      = i * 0.04
        sp_raw = round(random.uniform(2.0, 15.0), 1)
        stride_rows.append({
            "Horse": display, "Slug": slug, "Date": date,
            "Track": "Burnie" if random.random() > 0.35 else random.choice(["Hobart", "Launceston"]),
            "Race_No": random.randint(1, 8), "Place": place, "Driver": driver,
            "Trainer": driver,  # use driver name so trainer fallback lookup works
            "Field_Size": field_size,
            "Starters": field_size,
            "Distance_m": random.choice([2180, 2180, 1609, 2620]),
            "SP": f"${sp_raw}",
            "800_Margin_m":  round(base_800m + trend, 1),
            "400_Margin_m":  round(base_800m * 0.55 + random.uniform(-0.5, 1.5), 1),
            "Last_800m_Pos": max(1, place - 1 + random.randint(-1, 2)),
            "400_0_Time":    round(29.2 + (nr - 45) * 0.04 + random.uniform(-0.4, 0.4), 2),
            "Mile_Rate":     round(mile_rate - trend * 0.1, 2),
            "800_Width":     round(random.uniform(0.5, 3.0), 1),
            "400_Width":     round(random.uniform(0.3, 2.0), 1),
            "Start_Type": "MS", "Track_Condition": random.choice(["Good", "Good", "Slow"]),
        })

sr_df = pd.DataFrame(stride_rows)
sr_df.to_csv(os.path.join(OUT, "stride_results.csv"), index=False)
print(f"✓ stride_results.csv   — {len(sr_df)} rows, {sr_df['Slug'].nunique()} horses")


# ---------------------------------------------------------------------------
# stride_profiles.csv  — career/venue summary, one row per horse
# ---------------------------------------------------------------------------
profile_rows = []

# Hobart horses
for display, slug, barrier, driver, nr, _n in HOBART_HORSES:
    hs = random.randint(4, 15)
    hw = max(0, int(hs * (nr - 60) / 80 + random.uniform(-0.5, 1.5)))
    hp = min(hs, hw + random.randint(0, 3))
    cs = random.randint(20, 80)
    cw = max(0, int(cs * (nr - 60) / 100 + random.uniform(-1, 3)))
    cp = min(cs, cw + random.randint(2, 10))
    bs = random.randint(1, 6); bw = random.randint(0, 1); bp = random.randint(0, 2)
    profile_rows.append({
        "Horse": display, "Slug": slug, "Trainer": f"Trainer_{slug[:6]}",
        "Last_Win_Date":  (HOBART_DATE - timedelta(days=random.randint(30, 200))).strftime("%Y-%m-%d"),
        "Last_Win_Venue": "Hobart",
        "Career_Starts": cs, "Career_Wins": cw,
        "Career_Win_Pct":   f"{cw/cs:.1%}" if cs else "0.0%",
        "Career_Place_Pct": f"{cp/cs:.1%}" if cs else "0.0%",
        "Hobart_Starts": hs, "Hobart_Wins": hw, "Hobart_Places": hp,
        "Hobart_Win_Pct": f"{hw/hs:.1%}" if hs else "0.0%",
        "Launceston_Starts": random.randint(2, 8), "Launceston_Wins": random.randint(0, 2),
        "Launceston_Places": random.randint(0, 3),
        "Launceston_Win_Pct": f"{random.uniform(0, 0.25):.1%}",
        "Burnie_Starts": bs, "Burnie_Wins": bw, "Burnie_Places": bp,
        "Burnie_Win_Pct": f"{random.uniform(0, 0.2):.1%}",
    })

# Burnie horses
for display, slug, barrier, driver, nr, n_runs, burnie_wp in BURNIE_HORSES:
    cs = n_runs
    cw = max(0, int(cs * (nr - 40) / 120 + random.uniform(-0.5, 2)))
    cp = min(cs, cw + random.randint(2, 8))
    if burnie_wp is not None:
        bs = n_runs; bw = max(0, round(bs * burnie_wp)); bp = min(bs, bw + random.randint(1, 5))
        b_pct = burnie_wp
    else:
        bs = random.randint(8, 14)
        bw = max(0, int(bs * (nr - 40) / 150 + random.uniform(-0.3, 1)))
        bp = min(bs, bw + random.randint(1, 4))
        b_pct = bw / bs if bs else 0.0
    hs = random.randint(3, 8)
    hw = max(0, int(hs * (nr - 40) / 160 + random.uniform(-0.3, 1)))
    hp = min(hs, hw + random.randint(0, 3))
    ls = random.randint(2, 6)
    lw = max(0, int(ls * (nr - 40) / 160 + random.uniform(-0.2, 0.8)))
    lp = min(ls, lw + random.randint(0, 2))
    profile_rows.append({
        "Horse": display, "Slug": slug, "Trainer": f"Trainer_{slug[:6]}",
        "Last_Win_Date":  (BURNIE_DATE - timedelta(days=random.randint(30, 200))).strftime("%Y-%m-%d"),
        "Last_Win_Venue": "Burnie",
        "Career_Starts": cs, "Career_Wins": cw,
        "Career_Win_Pct":   f"{cw/cs:.1%}" if cs else "0.0%",
        "Career_Place_Pct": f"{cp/cs:.1%}" if cs else "0.0%",
        "Burnie_Starts": bs, "Burnie_Wins": bw, "Burnie_Places": bp,
        "Burnie_Win_Pct": f"{b_pct:.1%}",
        "Hobart_Starts": hs, "Hobart_Wins": hw, "Hobart_Places": hp,
        "Hobart_Win_Pct": f"{hw/hs:.1%}" if hs else "0.0%",
        "Launceston_Starts": ls, "Launceston_Wins": lw, "Launceston_Places": lp,
        "Launceston_Win_Pct": f"{lw/ls:.1%}" if ls else "0.0%",
    })

sp_df = pd.DataFrame(profile_rows)
sp_df.to_csv(os.path.join(OUT, "stride_profiles.csv"), index=False)
print(f"✓ stride_profiles.csv  — {len(sp_df)} rows ({sp_df['Slug'].nunique()} horses)")


# ---------------------------------------------------------------------------
# drivers_profile.csv + driver_results_recent.csv
# ---------------------------------------------------------------------------
ALL_DRIVERS = list({h[3] for h in HOBART_HORSES} | {h[3] for h in BURNIE_HORSES})
driver_rows = []
for drv in ALL_DRIVERS:
    ls = random.randint(200, 1200); lw = int(ls * random.uniform(0.08, 0.22))
    ss = random.randint(20, 80);    sw = int(ss * random.uniform(0.10, 0.25))
    driver_rows.append({
        "Name": drv,
        "driver.lifetime_summary.starts": ls, "driver.lifetime_summary.wins": lw,
        "driver.current_season_summary.starts": ss, "driver.current_season_summary.wins": sw,
        "driver.venue_summary.Hobart.starts":     random.randint(10, 60),
        "driver.venue_summary.Hobart.wins":       random.randint(1, 12),
        "driver.venue_summary.Burnie.starts":     random.randint(10, 60),
        "driver.venue_summary.Burnie.wins":       random.randint(1, 12),
        "driver.venue_summary.Launceston.starts": random.randint(5, 30),
        "driver.venue_summary.Launceston.wins":   random.randint(0, 6),
    })
pd.DataFrame(driver_rows).to_csv(os.path.join(OUT, "drivers_profile.csv"), index=False)
print(f"✓ drivers_profile.csv  — {len(driver_rows)} drivers")

dr_rows = []
for drv in ALL_DRIVERS:
    for _ in range(random.randint(20, 45)):
        dr_rows.append({
            "Trainer": drv,
            "Track":   random.choice(["Burnie", "Burnie", "Hobart", "Launceston"]),
            "Place":   random.randint(1, 8),
            "Date":    (BURNIE_DATE - timedelta(days=random.randint(1, 180))).strftime("%Y-%m-%d"),
        })
pd.DataFrame(dr_rows).to_csv(os.path.join(OUT, "driver_results_recent.csv"), index=False)
print(f"✓ driver_results_recent.csv — {len(dr_rows)} rows")


# ---------------------------------------------------------------------------
# trainer_results_recent.csv
# Keyed by driver name (= Trainer value in stride_results) so the driver-name
# fallback in compute_trainer_form() finds real records.
# ---------------------------------------------------------------------------
tr_rows = []
for display, slug, barrier, driver, nr, n_runs in HOBART_HORSES:
    win_rate = max(0.05, (nr - 60) / 80 + random.uniform(-0.05, 0.1))
    for _ in range(random.randint(8, 18)):
        tr_rows.append({
            "Trainer": driver,
            "Track":   random.choice(["Hobart", "Hobart", "Launceston", "Burnie"]),
            "Place":   1 if random.random() < win_rate else random.randint(2, 10),
            "Date":    (HOBART_DATE - timedelta(days=random.randint(1, 90))).strftime("%Y-%m-%d"),
        })
for display, slug, barrier, driver, nr, n_runs, _wp in BURNIE_HORSES:
    win_rate = max(0.05, (nr - 45) / 120 + random.uniform(-0.05, 0.1))
    for _ in range(random.randint(8, 18)):
        tr_rows.append({
            "Trainer": driver,
            "Track":   random.choice(["Burnie", "Burnie", "Hobart", "Launceston"]),
            "Place":   1 if random.random() < win_rate else random.randint(2, 8),
            "Date":    (BURNIE_DATE - timedelta(days=random.randint(1, 90))).strftime("%Y-%m-%d"),
        })
pd.DataFrame(tr_rows).to_csv(os.path.join(OUT, "trainer_results_recent.csv"), index=False)
print(f"✓ trainer_results_recent.csv — {len(tr_rows)} rows")


# ---------------------------------------------------------------------------
# stewards_notes.csv  — incidents across both meetings
# ---------------------------------------------------------------------------
sn_rows = [
    # Hobart Race 4 — Sun 15 Mar 2026
    {"Horse": "Ray Dan",         "Horse_Slug": "ray-dan",
     "Date": "2026-03-15", "Note": "checked at 400m, held up for clear run"},
    {"Horse": "Imperial Laz NZ", "Horse_Slug": "imperial-laz-nz",
     "Date": "2026-03-15", "Note": "checked by Ray Dan at 400m"},
    {"Horse": "Thee Old Bomb NZ","Horse_Slug": "thee-old-bomb-nz",
     "Date": "2026-03-15", "Note": "broke a hopple strap at 400m — eased to finish"},
    {"Horse": "Ray Dan",         "Horse_Slug": "ray-dan",
     "Date": "2026-02-20", "Note": "galloped when checked near 400m mark"},
    # Burnie Race 4 — Fri 13 Mar 2026
    # Rock Amour: two prior concern notes that should downweight it
    {"Horse": "Rock Amour",      "Horse_Slug": "rock-amour",
     "Date": "2026-02-28", "Note": "galloped out approaching the home turn — lost all momentum"},
    {"Horse": "Rock Amour",      "Horse_Slug": "rock-amour",
     "Date": "2026-02-14", "Note": "pulled hard in the early stages, unruly at start"},
]
pd.DataFrame(sn_rows).to_csv(os.path.join(OUT, "stewards_notes.csv"), index=False)
print(f"✓ stewards_notes.csv   — {len(sn_rows)} rows")

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
        "Date":            (BURNIE_DATE - timedelta(days=i * 14)).strftime("%Y-%m-%d"),
        "Track":           random.choice(["Burnie", "Hobart"]),
        "Race_No":         random.randint(1, 8),
        "Start_Type":      "MS",
        "Track_Condition": random.choice(["Good", "Good", "Slow"]),
    })
pd.DataFrame(div_rows).to_csv(os.path.join(OUT, "dividends.csv"), index=False)
print(f"✓ dividends.csv        — {len(div_rows)} rows")

print(f"\n✅ Full dataset written to {OUT}/")
print(f"   Hobart horses : {', '.join(h[0] for h in HOBART_HORSES)}")
print(f"   Burnie horses : {', '.join(h[0] for h in BURNIE_HORSES)}")
