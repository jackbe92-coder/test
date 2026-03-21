"""
Diagnostic wrapper for Burnie Race 4 — Friday 13 Mar 2026 — 2180m Mobile Start
Outputs: feature vectors, 800m position assignments, position multipliers,
win/place probabilities, and feature importance chart.
"""
import sys, io, numpy as np
from form_parser import parse_form
from feature_extractor import DataLoader, extract_all_features
from report_generator import generate_report
from track_profiles import TRACK_PROFILES, get_position_multiplier
from race_sim import run_simulation, zscore_field, SCORED_FEATURES, FIXED_FEATURES

FORM = """\
Burnie Race 4 — 13 Mar 2026 — 2180m
1. Away Game — barrier 1 — Liam Older — NR45
2. Nikita Jo — barrier 2 — Ryan Backhouse — NR50
3. Rock Amour — barrier 3 — Charlie Castles — NR46
4. La Pierre — barrier 4 — Brent Parish — NR47
5. Modern Jive — barrier 5 — Gareth Rattray — NR52
6. Andaman Bay — barrier 6 — Mark Yole — NR54
"""
DATA  = "output/claude_data"
RUNS  = 5000

out = []
def p(*args, **kw):
    line = " ".join(str(a) for a in args)
    out.append(line)
    print(line, **kw)

p("=" * 78)
p("  DIAGNOSTIC LOG — BURNIE RACE 4")
p("  Friday 13 March 2026  |  2180m  |  Mobile Start  |  5,000 Monte Carlo runs")
p("=" * 78)

# ── 1. Parse form ──────────────────────────────────────────────────────────────
p("\n[1/5] PARSING RACE FORM")
p("-" * 78)
race_info = parse_form(form_str=FORM, data_dir=DATA, track_override="Burnie")
race_info.start_type = "MS"
p(f"Track     : {race_info.track}")
p(f"Date      : {race_info.date}")
p(f"Race      : {race_info.race_no}")
p(f"Distance  : {race_info.distance_m}m")
p(f"Start type: {race_info.start_type}")
p(f"Runners   : {len(race_info.runners)}")
p()
p(f"{'#':<4} {'Horse':<26} {'Barrier':>7} {'Driver':<22} {'NR':>5}")
p("-" * 70)
for r in race_info.runners:
    p(f"{r.tab_no or r.barrier:<4} {r.horse:<26} {r.barrier:>7} {r.driver:<22} {r.nr:>5.0f}")

# ── 2. Load data ───────────────────────────────────────────────────────────────
p("\n[2/5] LOADING DATA")
p("-" * 78)
data = DataLoader(DATA)
p(f"stride_results  : {len(data.stride_results)} rows, "
  f"{data.stride_results['Slug'].nunique() if not data.stride_results.empty else 0} horses")
p(f"stride_profiles : {len(data.stride_profiles)} rows")
p(f"drivers_profile : {len(data.drivers_profile)} rows")

# ── 3. Feature vectors ────────────────────────────────────────────────────────
p("\n[3/5] FEATURE VECTORS (raw, pre-z-score normalisation)")
p("-" * 78)
all_features, warnings = extract_all_features(race_info, data)

all_feat_names = SCORED_FEATURES + FIXED_FEATURES + [
    'field_strength', 'field_size_adjustment', 'consistency_score',
    'driver_experience_years',
]

slugs = [r.slug for r in race_info.runners]
names = {r.slug: r.horse for r in race_info.runners}

p(f"{'Feature':<32}", end="")
for slug in slugs:
    p(f" {names[slug][:11]:>11}", end="")
p()
p("-" * (32 + 12 * len(slugs)))

for feat in all_feat_names:
    p(f"{feat:<32}", end="")
    for slug in slugs:
        val = all_features.get(slug, {}).get(feat, float('nan'))
        if val != val:
            p(f" {'—':>11}", end="")
        else:
            p(f" {val:>11.4f}", end="")
    p()

# ── Z-scores ──────────────────────────────────────────────────────────────────
p("\nZ-SCORED FEATURES (normalised across field)")
p("-" * 78)
z_scores = zscore_field(all_features, slugs, SCORED_FEATURES)

p(f"{'Feature':<32}", end="")
for slug in slugs:
    p(f" {names[slug][:11]:>11}", end="")
p()
p("-" * (32 + 12 * len(slugs)))
for feat in SCORED_FEATURES:
    p(f"{feat:<32}", end="")
    for slug in slugs:
        val = z_scores.get(slug, {}).get(feat, 0.0)
        p(f" {val:>11.4f}", end="")
    p()

# ── Warnings ──────────────────────────────────────────────────────────────────
if warnings:
    p("\nDATA WARNINGS")
    p("-" * 78)
    seen = set()
    for w in warnings:
        if w not in seen:
            p(f"  ! {w}")
            seen.add(w)
else:
    p("\n  No data warnings — all horses found in dataset.")

# ── 4. Track profile ──────────────────────────────────────────────────────────
p("\n[4/5] TRACK PROFILE — BURNIE POSITION MULTIPLIERS")
p("-" * 78)
profile = TRACK_PROFILES.get('Burnie', TRACK_PROFILES.get('burnie', {}))
p(f"Track length : {profile.get('length_m')}m")
p(f"Straight     : {profile.get('straight_m')}m")
p(f"Sprint lane  : {profile.get('sprint_lane')}")
p(f"Leader win % : {profile.get('leader_win_pct', 0)*100:.1f}%")
p(f"Garden seat  : {profile.get('garden_seat_win_pct', 0)*100:.1f}%")
p()
p("Fixed position multipliers applied post-score each simulation run:")
for pos, mult in profile.get('position_multiplier', {}).items():
    bar = "█" * int(mult * 10)
    p(f"  {pos:<20}: {mult:.2f}x  {bar}")
p()
p("Weight overrides vs baseline:")
from sim_config import TRACK_OVERRIDES
overrides = TRACK_OVERRIDES.get('Burnie', {})
if overrides:
    for k, (d_lo, d_hi) in overrides.items():
        direction = "boosted" if d_lo > 0 else "reduced"
        p(f"  {k:<30}: {d_lo:+.2f} / {d_hi:+.2f}  ({direction})")
else:
    p("  (none — default weights apply)")
p()
p("Start type: Mobile (MS) — barrier position determines early running position")
from sim_config import PACE_GAP_LEADER, PACE_GAP_ON_PACE, PACE_GAP_MIDFIELD
p(f"  Pace gap thresholds:")
p(f"    leader gap    ≤ {PACE_GAP_LEADER}")
p(f"    on_pace gap   ≤ {PACE_GAP_ON_PACE}")
p(f"    midfield gap  ≤ {PACE_GAP_MIDFIELD}")
p(f"    back          > {PACE_GAP_MIDFIELD}")

# ── 5. Simulation + report ────────────────────────────────────────────────────
p("\n[5/5] MONTE CARLO SIMULATION — 5,000 RUNS")
p("=" * 78)
results = run_simulation(race_info, all_features, n_runs=RUNS)

old_stdout = sys.stdout
sys.stdout = buf = io.StringIO()
generate_report(results, race_info, all_features, warnings)
sys.stdout = old_stdout
p(buf.getvalue())

# ── 800m position summary table ───────────────────────────────────────────────
p("\nDETAILED 800m POSITION PROBABILITY TABLE")
p("-" * 78)
pace_pct = results['pace_pct']
p(f"{'Horse':<22} {'Leader':>8} {'On Pace':>9} {'Midfield':>10} {'Back':>6}  MostLikely")
p("-" * 78)
for slug in sorted(slugs, key=lambda s: -pace_pct[s].get('leader', 0)):
    pp   = pace_pct[slug]
    lead = pp.get('leader', 0)
    on_p = pp.get('on_pace', 0)
    mid  = pp.get('midfield', 0)
    back = pp.get('back', 0)
    most = max(pp, key=pp.get).replace('_', ' ').title()
    p(f"{names[slug]:<22} {lead*100:>7.1f}% {on_p*100:>8.1f}% {mid*100:>9.1f}% {back*100:>5.1f}%  {most}")

# ── Feature importance chart ───────────────────────────────────────────────────
p("\nFEATURE IMPORTANCE CHART (discriminating power = std-dev across field)")
p("-" * 78)
feat_labels = {
    'gate_speed':            'Gate speed / early position',
    'finishing_speed':       'Finishing speed (last 400m)',
    'mile_rate_trend':       'Form trend (improving/declining)',
    'venue_win_rate':        'Track-specific win rate',
    'venue_place_rate':      'Track-specific place rate',
    'barrier_score':         'Barrier / draw advantage',
    'driver_venue_rate':     'Driver venue record',
    'driver_season_winrate': 'Driver season win rate',
    'driver_horse_combo':    'Driver-horse combination history',
    'class_relativity':      'Class level vs field (NR delta)',
    'last_800m_pos':         '800m run-home momentum',
    'width_penalty':         'Lane width (wide runner discount)',
    'position_800m_margin':  '800m positional margin',
    'position_400m_margin':  '400m positional margin',
    'sp_vs_performance':     'SP vs actual performance (value)',
    'distance_suitability':  'Distance suitability',
    'start_type_rate':       'Mobile/standing start rate',
    'trainer_form':          'Trainer form (recent wins)',
    'track_condition_rate':  'Track condition win rate',
    'class_trajectory':      'Class trajectory (moving up/down)',
    'winner_beaten_quality': 'Quality of horses beaten',
    'days_since_last_win':   'Days since last win (drought)',
    'last_win_venue_match':  'Last win at this venue',
    'driver_group_experience':'Driver group-race experience',
    'driver_experience_years':'Driver experience (years)',
    'distance_optimal_range':'Distance optimal range match',
    'career_class_experience':'Career class experience',
    'mobile_barrier_rate':   'Mobile barrier win rate',
}
feat_spreads = {f: float(np.std([all_features.get(s, {}).get(f, 0.0) for s in slugs]))
                for f in SCORED_FEATURES}
sorted_feats = sorted(feat_spreads.items(), key=lambda x: -x[1])
max_spread   = max(v for _, v in sorted_feats) if sorted_feats else 1.0
for rank, (feat, spread) in enumerate(sorted_feats, 1):
    bar = "█" * max(1, int(spread / max_spread * 50))
    p(f"  {rank:>2}. {feat_labels.get(feat, feat):<42} {spread:6.4f}  {bar}")

p()
p("=" * 78)
p("  END OF DIAGNOSTIC LOG — BURNIE RACE 4  13 Mar 2026")
p("=" * 78)

outpath = "burnie_race4_diagnostic.txt"
with open(outpath, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"\n[SAVED] Output written to {outpath}")
