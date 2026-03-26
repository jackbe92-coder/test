"""
Diagnostic wrapper for Race 4 — Hobart 15-Mar-2026
Outputs: feature vectors, 800m position assignments, position multipliers,
win/place probabilities, and feature importance chart.
"""
import sys, io, numpy as np
from form_parser import parse_form
from feature_extractor import DataLoader, extract_all_features
from report_generator import generate_report
from track_profiles import TRACK_PROFILES, get_position_multiplier
from race_sim import run_simulation, zscore_field, SCORED_FEATURES, FIXED_FEATURES

FORM   = "Hobart Harness 15-03-2026.pdf"
DATA   = "output/claude_data"
RACE   = 4
RUNS   = 5000
np.random.seed(42)

out = []
def p(*args, **kw):
    line = " ".join(str(a) for a in args)
    out.append(line)
    print(line, **kw)

p("=" * 78)
p("  DIAGNOSTIC LOG — HOBART RACE 4 (Lather Up @ Woodlands Stud Pace)")
p("  15 March 2026  |  1609m  |  Mobile Start  |  5,000 Monte Carlo runs")
p("=" * 78)

# ── 1. Parse form ──────────────────────────────────────────────────────────────
p("\n[1/5] PARSING RACE FORM")
p("-" * 78)
race_info = parse_form(form_str=FORM, data_dir=DATA, race_no=RACE)
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

# Header
p(f"{'Feature':<32}", end="")
for slug in slugs:
    p(f" {names[slug][:11]:>11}", end="")
p()
p("-" * (32 + 12 * len(slugs)))

for feat in all_feat_names:
    p(f"{feat:<32}", end="")
    for slug in slugs:
        val = all_features.get(slug, {}).get(feat, float('nan'))
        if val != val:          # nan
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

# ── 4. Position multipliers ──────────────────────────────────────────────────
p("\n[4/5] TRACK PROFILE — HOBART POSITION MULTIPLIERS")
p("-" * 78)
profile = TRACK_PROFILES.get('Hobart', {})
p(f"Track length : {profile.get('length_m')}m")
p(f"Straight     : {profile.get('straight_m')}m")
p(f"Sprint lane  : {profile.get('sprint_lane')}")
p(f"Leader win % : {profile.get('leader_win_pct', 0)*100:.1f}%")
p(f"Garden seat  : {profile.get('garden_seat_win_pct', 0)*100:.1f}%")
p()
p("Fixed position multipliers applied post-score each simulation run:")
mults = profile.get('position_multiplier', {})
for pos, mult in mults.items():
    bar = "█" * int(mult * 10)
    p(f"  {pos:<20}: {mult:.2f}x  {bar}")
p()
p("Weight overrides vs baseline:")
from sim_config import TRACK_OVERRIDES
overrides = TRACK_OVERRIDES.get('Hobart', {})
if overrides:
    for k, (d_lo, d_hi) in overrides.items():
        direction = "boosted" if d_lo > 0 else "reduced"
        p(f"  {k:<30}: {d_lo:+.2f} / {d_hi:+.2f}  ({direction})")
else:
    p("  (none)")

p()
p("Sprint lane position assignment (stochastic, per run):")
p("  On-pace gap → 55% chance = garden_seat (sprint lane access)")
p("                45% chance = midfield_runner (no sprint lane)")
p("  Pace gap thresholds from sim_config:")
from sim_config import PACE_GAP_LEADER, PACE_GAP_ON_PACE, PACE_GAP_MIDFIELD
p(f"    leader gap    ≤ {PACE_GAP_LEADER}")
p(f"    on_pace gap   ≤ {PACE_GAP_ON_PACE}")
p(f"    midfield gap  ≤ {PACE_GAP_MIDFIELD}")
p(f"    back          > {PACE_GAP_MIDFIELD}")

# ── 5. Simulation + report ───────────────────────────────────────────────────
p("\n[5/5] MONTE CARLO SIMULATION — 5,000 RUNS")
p("=" * 78)
results = run_simulation(race_info, all_features, n_runs=RUNS)

# Capture rich report to string then re-emit as plain text
old_stdout = sys.stdout
sys.stdout = buf = io.StringIO()
generate_report(results, race_info, all_features, warnings)
sys.stdout = old_stdout
report_text = buf.getvalue()
p(report_text)

# ── 800m position summary table ──────────────────────────────────────────────
p("\nDETAILED 800m POSITION PROBABILITY TABLE")
p("-" * 78)
pace_pct = results['pace_pct']
p(f"{'Horse':<28} {'Leader':>8} {'GardenSt':>10} {'MidRun':>8} {'Midfield':>10} {'Back':>6}  MostLikely")
p("-" * 78)

# For Hobart, garden_seat and midfield_runner are sub-labels mapped to on_pace bucket
# in report; we display direct pace_pct counts
for slug in sorted(slugs, key=lambda s: -pace_pct[s].get('leader', 0)):
    pp = pace_pct[slug]
    lead = pp.get('leader', 0)
    on_p = pp.get('on_pace', 0)   # combined garden_seat + midfield_runner
    mid  = pp.get('midfield', 0)
    back = pp.get('back', 0)
    most = max(pp, key=pp.get).replace('_', ' ').title()
    p(f"{names[slug]:<28} {lead*100:>7.1f}% {on_p*100:>9.1f}% {0:>7.1f}% {mid*100:>9.1f}% {back*100:>5.1f}%  {most}")

# ── Feature importance chart ─────────────────────────────────────────────────
p("\nFEATURE IMPORTANCE CHART (discriminating power = std-dev across field)")
p("-" * 78)
feat_labels = {
    'gate_speed':          'Gate speed / early position',
    'finishing_speed':     'Finishing speed (last 400m)',
    'mile_rate_trend':     'Form trend (improving/declining)',
    'venue_win_rate':      'Track-specific win rate',
    'venue_place_rate':    'Track-specific place rate',
    'barrier_score':       'Barrier / draw advantage',
    'driver_venue_rate':   'Driver venue record',
    'driver_season_winrate':'Driver season win rate',
    'driver_horse_combo':  'Driver-horse combination history',
    'class_relativity':    'Class level vs field (NR delta)',
    'last_800m_pos':       '800m run-home momentum',
    'width_penalty':       'Lane width (wide runner discount)',
    'position_800m_margin':'800m positional margin',
    'position_400m_margin':'400m positional margin',
    'sp_vs_performance':   'SP vs actual performance (value)',
    'distance_suitability':'Distance suitability',
    'start_type_rate':     'Mobile/standing start rate',
    'trainer_form':        'Trainer form (recent wins)',
    'track_condition_rate':'Track condition win rate',
    'class_trajectory':    'Class trajectory (moving up/down)',
    'winner_beaten_quality':'Quality of horses beaten',
    'days_since_last_win': 'Days since last win (drought)',
    'last_win_venue_match':'Last win at this venue',
    'driver_group_experience':'Driver group-race experience',
    'driver_experience_years':'Driver experience (years)',
    'distance_optimal_range':'Distance optimal range match',
    'career_class_experience':'Career class experience',
    'mobile_barrier_rate': 'Mobile barrier win rate',
}
feat_spreads = {}
for feat in SCORED_FEATURES:
    vals = [all_features.get(s, {}).get(feat, 0.0) for s in slugs]
    feat_spreads[feat] = float(np.std(vals))
sorted_feats = sorted(feat_spreads.items(), key=lambda x: -x[1])
max_spread = max(v for _, v in sorted_feats) if sorted_feats else 1.0
for rank, (feat, spread) in enumerate(sorted_feats, 1):
    label = feat_labels.get(feat, feat)
    bar_len = max(1, int(spread / max_spread * 50))
    bar = "█" * bar_len
    p(f"  {rank:>2}. {label:<42} {spread:6.4f}  {bar}")

p()
p("=" * 78)
p("  END OF DIAGNOSTIC LOG")
p("=" * 78)

# Save to file
outpath = "hobart_race4_diagnostic.txt"
with open(outpath, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"\n[SAVED] Output written to {outpath}")
