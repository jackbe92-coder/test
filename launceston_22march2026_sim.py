"""
launceston_22march2026_sim.py
Launceston Harness — Sunday 22 March 2026 — All 9 Races

Confidence note: Launceston track profile is UNVALIDATED — outputs carry
lower confidence than Burnie (validated).  All outputs are flagged [LNCT].

Start types:
  MS — R1, R2, R3, R5, R7, R8, R9
  SS — R4, R6

Priority races (full diagnostic + pre/post multiplier table):
  R3 — NR up to 49, 1680m, MS — Away Game post-Burnie test
  R4 — Open, 2698m, SS — Easter Cup Heat 1
  R6 — Open, 2698m, SS — Easter Cup Heat 2 (Triedtotellya)

Data-limited horses (barrier/NR priors only):
  Wavethebill NZ (R1), Lynryd Skynryd NZ (R1)
  No Nukes Skipper NZ (R3), Vanquish Stride NZ (R4), Anything Goes NZ (R6)

Mainland visitors (flagged — mainland form only):
  My Bettor Half (R8), Keayang Lexi (R7)
"""

import sys, io
import numpy as np
from form_parser import parse_form
from feature_extractor import DataLoader, extract_all_features
from report_generator import (
    _section_probability_table, _section_speed_map,
    _section_robustness, generate_report,
)
from track_profiles import TRACK_PROFILES, get_position_multiplier, apply_track_profile
from race_sim import (
    run_simulation, zscore_field,
    SCORED_FEATURES, FIXED_FEATURES,
    _effective_weight_ranges, PACE_FEATURES,
)
from sim_config import TRACK_OVERRIDES

DATA  = "output/claude_data"
RUNS  = 2000
TRACK = "Launceston"

# ── Data-limited / mainland flags ─────────────────────────────────────────────
DATA_LIMITED = {
    "wavethebill nz", "lynryd skynryd nz",
    "no nukes skipper nz", "vanquish stride nz", "anything goes nz",
}
MAINLAND_VISITOR = {"my bettor half", "keayang lexi"}

# Horses confirmed in dataset (from stride_profiles check)
IN_DATASET = {
    "away game", "mr bondi", "mr bondi nz", "zara tindall", "zara tindall nz",
    "my way", "my way nz", "andaman bay", "la pierre", "modern jive",
    "nikita jo", "rock amour", "always aurora", "captain pins nz",
    "custom harley", "cuzzy bro", "imperial laz nz", "james cagney nz",
    "ray dan", "thee old bomb nz",
}

def data_coverage(race_info, data):
    """Return (covered, total, pct) and list of covered slugs."""
    slugs = [r.slug for r in race_info.runners]
    names = {r.slug: r.horse for r in race_info.runners}
    if data.stride_results.empty:
        return 0, len(slugs), 0.0, []
    sr_horses = set(data.stride_results['Horse'].str.lower().tolist())
    covered = [s for s in slugs if names[s].lower() in sr_horses]
    pct = len(covered) / len(slugs) * 100
    return len(covered), len(slugs), pct, covered

# ── Race definitions ──────────────────────────────────────────────────────────
# Format: (race_no, form_text, start_type, is_priority)
# NR: race-condition midpoint; Away Game known NR45 (Burnie R4 winner).
# Barrier = tab number (BP column not extractable from PDF; for SS this is an
# approximation — note in output).

RACES = [

    # ── R1  2200m  MS  NR 70-79 ──────────────────────────────────────────────
    (1, """\
Launceston Race 1 — 22 Mar 2026 — 2200m
1. Wavethebill — barrier 1 — Dylan Ford — NR74
2. Rockandahardplace — barrier 2 — John Walters — NR74
3. Enjoy Life — barrier 3 — Mark Yole — NR74
4. My Way — barrier 4 — Ricky Duggan — NR74
5. Zara Tindall — barrier 5 — Grace Jones — NR74
6. Mr Bondi — barrier 6 — Tiarna Ford — NR74
7. Magic Joe — barrier 7 — Todd Rattray — NR74
8. Lynryd Skynryd — barrier 8 — Gareth Rattray — NR74
9. Another Nien — barrier 9 — Jacob Duggan — NR74
""", "MS", False),

    # ── R2  1680m  MS  MAIDEN ─────────────────────────────────────────────────
    (2, """\
Launceston Race 2 — 22 Mar 2026 — 1680m
1. Fancy Another — barrier 1 — Ricky Duggan — NR1
2. Harley Glenwood — barrier 2 — John Walters — NR1
3. Voodoo Ranger — barrier 3 — Jordan Chibnall — NR1
4. Winning Brew — barrier 4 — Kayleb Williams — NR1
5. Quay — barrier 5 — Todd Rattray — NR1
6. Left One Hangin — barrier 6 — Rohan Hillier — NR1
7. King Bart — barrier 7 — Brodie Davis — NR1
8. Hes First Class — barrier 8 — Dylan Ford — NR1
9. Only Glory — barrier 9 — Charlie Castles — NR1
10. Klitschko Leis — barrier 10 — Gareth Rattray — NR1
11. Alby Priddy — barrier 11 — Malcom Jones — NR1
""", "MS", False),

    # ── R3  1680m  MS  NR up to 49  [PRIORITY] ───────────────────────────────
    (3, """\
Launceston Race 3 — 22 Mar 2026 — 1680m
1. Paziah — barrier 1 — Todd Rattray — NR44
2. Dancinwivjolene — barrier 2 — Charlie Castles — NR44
3. I Am Caesar — barrier 3 — Dylan Ford — NR44
4. Away Game — barrier 4 — Ricky Duggan — NR45
5. My Mate Luke — barrier 5 — Luke Hooper — NR44
6. Outback Rock — barrier 6 — Brodie Davis — NR44
7. Captain Cheddar — barrier 7 — Mark Yole — NR44
8. Miki Sanz — barrier 8 — John Walters — NR46
9. Five Star Shark — barrier 9 — Jacob Duggan — NR44
10. Debt Till We Part — barrier 10 — Paul Williams — NR44
11. No Nukes Skipper — barrier 11 — Gareth Rattray — NR46
""", "MS", True),

    # ── R4  2698m  SS  Open  Easter Cup Heat 1  [PRIORITY] ───────────────────
    # Note: barrier = tab number (BP not available from PDF for SS — approximation)
    (4, """\
Launceston Race 4 — 22 Mar 2026 — 2698m
1. Pardoe Plugga — barrier 1 — Charlie Castles — NR72
2. Told You Twice — barrier 2 — Gareth Rattray — NR68
3. Montgomery Burns — barrier 3 — Dylan Ford — NR66
4. Keayang Fitzy — barrier 4 — Mark Yole — NR72
5. Southern Luck — barrier 5 — Todd Rattray — NR66
6. Vanquish Stride — barrier 6 — Ricky Duggan — NR74
7. Stepping Stones — barrier 7 — Heath Woods — NR74
8. De Goey — barrier 8 — Rohan Hillier — NR70
9. Glenledi Elvis — barrier 9 — Conor Crook — NR72
10. Magnetic Terror — barrier 10 — Jordan Chibnall — NR74
""", "SS", True),

    # ── R5  2200m  MS  NR 60-69 ──────────────────────────────────────────────
    (5, """\
Launceston Race 5 — 22 Mar 2026 — 2200m
1. Rocky Stride — barrier 1 — Brodie Davis — NR64
2. Mcmayhem — barrier 2 — Rohan Hillier — NR64
3. Lougle — barrier 3 — Conor Crook — NR64
4. Windy Hanover — barrier 4 — Jordan Chibnall — NR64
5. Neon — barrier 5 — Ethan Arnott — NR64
6. Keepers Ideal — barrier 6 — Malcom Jones — NR64
7. National News — barrier 7 — Ricky Duggan — NR64
8. Colby Sanz — barrier 8 — John Walters — NR64
9. Racketeers Boy — barrier 9 — Dylan Ford — NR64
10. Dazzle Me — barrier 10 — Tiarna Ford — NR64
11. Major Lester — barrier 11 — Gareth Rattray — NR64
12. Rock On Playboy — barrier 12 — Mark Yole — NR64
""", "MS", False),

    # ── R6  2698m  SS  Open  Easter Cup Heat 2  [PRIORITY] ───────────────────
    # Note: barrier = tab number (BP not available from PDF for SS — approximation)
    (6, """\
Launceston Race 6 — 22 Mar 2026 — 2698m
1. Fleetwood Rock — barrier 1 — Gareth Rattray — NR70
2. Horatius Speculo — barrier 2 — Dylan Ford — NR65
3. Photo First — barrier 3 — Jacob Duggan — NR62
4. Iron Clad — barrier 4 — Mark Yole — NR75
5. Rocks Roy — barrier 5 — Ricky Duggan — NR78
6. Anything Goes — barrier 6 — Tiarna Ford — NR68
7. The Shallows — barrier 7 — Brodie Davis — NR80
8. Gordons Bay — barrier 8 — Charlie Castles — NR80
9. Maebee — barrier 9 — Conor Crook — NR73
10. Triedtotellya — barrier 10 — Rohan Hillier — NR80
""", "SS", True),

    # ── R7  2200m  MS  NR 50-54 ──────────────────────────────────────────────
    (7, """\
Launceston Race 7 — 22 Mar 2026 — 2200m
1. Cuzzy Junior — barrier 1 — Todd Rattray — NR52
2. Our Sweet Trixie — barrier 2 — Brodie Davis — NR52
3. Keayang Lexi — barrier 3 — Conor Crook — NR52
4. Can Do Magic — barrier 4 — Rohan Hillier — NR52
5. Kostyuk Leis — barrier 5 — Ethan Arnott — NR52
6. Far Left Fletch — barrier 6 — Jordan Chibnall — NR52
7. Tullah Girl — barrier 7 — Malcom Jones — NR52
8. Norbet — barrier 8 — John Walters — NR52
9. Cee Tee Chelsea — barrier 9 — Charlie Castles — NR52
10. Ashante Queen — barrier 10 — Grace Jones — NR52
11. Angelshavtime — barrier 11 — Ricky Duggan — NR52
12. Jeremy Wells — barrier 12 — Gareth Rattray — NR52
13. Grizzly Montana — barrier 13 — Dylan Ford — NR52
""", "MS", False),

    # ── R8  1680m  MS  NR ~55-64 ─────────────────────────────────────────────
    (8, """\
Launceston Race 8 — 22 Mar 2026 — 1680m
1. Shez All Heart — barrier 1 — Kayleb Williams — NR58
2. My Bettor Half — barrier 2 — NR58
3. What A Woman — barrier 3 — Jordan Chibnall — NR58
4. Cmon Sharky — barrier 4 — Dylan Ford — NR58
5. Rainbow Ranger — barrier 5 — Todd Rattray — NR58
6. Our Willow — barrier 6 — Rohan Hillier — NR58
7. Iris Brown — barrier 7 — Mark Yole — NR58
8. Centurian Miss — barrier 8 — Brodie Davis — NR58
9. Muzzame Mate — barrier 9 — Charlie Castles — NR58
10. Iden Regal Wave — barrier 10 — Luke Hooper — NR58
""", "MS", False),

    # ── R9  1680m  MS  NR 55-59 ──────────────────────────────────────────────
    (9, """\
Launceston Race 9 — 22 Mar 2026 — 1680m
1. Mary Mourne — barrier 1 — Jordan Chibnall — NR57
2. Coolandcollect — barrier 2 — Gareth Rattray — NR57
3. Puntarno Stride — barrier 3 — Mark Yole — NR57
4. Hay Miki — barrier 4 — Grace Jones — NR57
5. Moveslikealady — barrier 5 — Rohan Hillier — NR57
6. Camelot Jedimaster — barrier 6 — Tiarna Ford — NR57
7. Stir Me Up — barrier 7 — Jacob Duggan — NR57
8. Likeable Rogue — barrier 8 — Todd Rattray — NR57
9. Can Feel The Fury — barrier 9 — Ricky Duggan — NR57
10. Rollwitharty — barrier 10 — Dylan Ford — NR57
11. Tambros Tilly — barrier 11 — Brodie Davis — NR57
12. Sokys Line — barrier 12 — Kayleb Williams — NR57
""", "MS", False),
]

# ── Output capture ────────────────────────────────────────────────────────────
out = []

def p(*args, **kw):
    line = " ".join(str(a) for a in args)
    out.append(line)
    print(line, **kw)

def hr(char="─", width=78):
    p(char * width)

def banner(text):
    p("=" * 78)
    p(f"  {text}")
    p("=" * 78)

def section(text):
    pad = (74 - len(text)) // 2
    p("─" * pad + f" {text} " + "─" * pad)

# ── Pre/post multiplier table (for priority races) ────────────────────────────
def show_multiplier_table(race_info, all_features):
    p("\n[TRACK PROFILE] PRE/POST MULTIPLIER — single illustrative pass (midpoint weights)")
    hr()
    eff_ranges = _effective_weight_ranges(race_info.track, race_info.start_type)
    mid_w = {k: (lo + hi) / 2.0 for k, (lo, hi) in eff_ranges.items()}

    slugs = [r.slug for r in race_info.runners]
    names = {r.slug: r.horse for r in race_info.runners}

    z = zscore_field(all_features, slugs, SCORED_FEATURES)

    pre, pace = {}, {}
    for slug in slugs:
        zs = z.get(slug, {})
        s = (
            mid_w['w_gate_speed'] * (
                zs.get('gate_speed', 0)           * 0.35 +
                zs.get('last_800m_pos', 0)         * 0.20 +
                zs.get('position_800m_margin', 0)  * 0.30 +
                zs.get('position_400m_margin', 0)  * 0.15
            ) +
            mid_w['w_finishing_speed'] * (
                zs.get('finishing_speed', 0) * 0.70 +
                zs.get('width_penalty', 0)   * 0.30
            ) +
            mid_w['w_barrier'] * (
                zs.get('barrier_score', 0)       * 0.50 +
                zs.get('start_type_rate', 0)     * 0.25 +
                zs.get('mobile_barrier_rate', 0) * 0.25
            ) +
            mid_w['w_venue_rate'] * (
                zs.get('venue_win_rate', 0)       * 0.45 +
                zs.get('venue_place_rate', 0)     * 0.25 +
                zs.get('last_win_venue_match', 0) * 0.15 +
                zs.get('track_condition_rate', 0) * 0.15
            ) +
            mid_w['w_driver']         * zs.get('driver_venue_rate', 0) +
            mid_w['w_driver_quality'] * zs.get('driver_season_winrate', 0) +
            mid_w['w_driver_combo']   * zs.get('driver_horse_combo', 0) +
            mid_w['w_class'] * (
                zs.get('class_relativity', 0)        * 0.50 +
                zs.get('class_trajectory', 0)        * 0.30 +
                zs.get('career_class_experience', 0) * 0.20
            ) +
            mid_w['w_trend'] * (
                zs.get('mile_rate_trend', 0)       * 0.50 +
                zs.get('winner_beaten_quality', 0) * 0.30 +
                zs.get('days_since_last_win', 0)   * 0.20
            ) +
            mid_w['w_value']    * zs.get('sp_vs_performance', 0) +
            mid_w['w_distance'] * (
                zs.get('distance_suitability', 0)   * 0.55 +
                zs.get('distance_optimal_range', 0) * 0.45
            ) +
            mid_w['w_trainer']  * zs.get('trainer_form', 0)
        )
        pre[slug] = s

        pace[slug] = (
            mid_w['w_gate_speed'] * (
                zs.get('gate_speed', 0)           * 0.50 +
                zs.get('position_800m_margin', 0) * 0.35 +
                zs.get('last_800m_pos', 0)        * 0.15
            ) +
            mid_w['w_barrier'] * zs.get('barrier_score', 0) * 0.5
        )

    post, positions = apply_track_profile(pre, pace, race_info.track)

    p(f"{'Horse':<22} {'Pre-mult':>9} {'Position':<12} {'Mult':>6} {'Post-mult':>10}")
    hr()
    for slug in slugs:
        pos  = positions.get(slug, 'midfield')
        mult = get_position_multiplier(race_info.track, pos)
        p(f"{names[slug]:<22} {pre[slug]:>9.4f} {pos:<12} {mult:>6.2f}x {post[slug]:>10.4f}")


# ── Track profile block ───────────────────────────────────────────────────────
def show_track_profile(race_info):
    p("\n[TRACK PROFILE] Launceston — UNVALIDATED")
    hr()
    profile = TRACK_PROFILES.get(TRACK, {})
    p(f"Circumference : 1018m  |  Straight : 220m  |  Turns radius : 95m")
    p(f"Surface       : Crushed granite  |  Direction : Anti-clockwise")
    p(f"Distances     : 1680m, 2200m, 2698m")
    p(f"Sprint lane   : {profile.get('sprint_lane', False)}")
    p(f"Leader win %  : {profile.get('leader_win_pct', 0)*100:.1f}%  "
      f"(unvalidated — treat as provisional)")
    p()
    p("Position multipliers:")
    for pos, mult in profile.get('position_multiplier', {}).items():
        bar = "█" * max(1, int(mult * 10))
        p(f"  {pos:<14}: {mult:.2f}x  {bar}")
    p()
    p("Weight overrides vs baseline:")
    ov = TRACK_OVERRIDES.get(TRACK, {})
    if ov:
        for k, (d_lo, d_hi) in ov.items():
            direction = "boosted" if d_lo > 0 else "reduced"
            p(f"  {k:<28}: {d_lo:+.2f}/{d_hi:+.2f}  ({direction})")
    else:
        p("  (none defined — default weights apply)")
    p(f"\nStart type this race : {race_info.start_type}")


# ── Main simulation loop ──────────────────────────────────────────────────────
banner("LAUNCESTON HARNESS — 22 MARCH 2026 — ALL 9 RACES")
p("  [LNCT] Track profile UNVALIDATED — outputs carry lower confidence than Burnie")
p("  Simulation: 2,000 Monte Carlo runs per race")
p()

# Load data once
p("Loading historical data ...")
data = DataLoader(DATA)
p(f"  stride_results  : {len(data.stride_results)} rows")
p(f"  stride_profiles : {len(data.stride_profiles)} rows")
p(f"  drivers_profile : {len(data.drivers_profile)} rows")

# Accumulators for race night summary
summary_rows = []

for race_no, form_text, start_type, is_priority in RACES:
    p()
    banner(f"[LNCT] RACE {race_no}  —  {'FULL DIAGNOSTIC' if is_priority else 'ABBREVIATED'}")

    # ── Parse ────────────────────────────────────────────────────────────────
    race_info = parse_form(form_str=form_text, data_dir=DATA, track_override=TRACK)
    race_info.start_type = start_type

    slugs = [r.slug for r in race_info.runners]
    names = {r.slug: r.horse for r in race_info.runners}

    n_cov, n_tot, cov_pct, cov_slugs = data_coverage(race_info, data)
    conf_label = "VERY LOW" if cov_pct < 20 else ("LOW" if cov_pct < 50 else "MODERATE")

    p(f"Race    : {race_no}  |  {race_info.distance_m}m  |  {start_type}"
      f"  |  {len(slugs)} runners")
    p(f"Data coverage: {n_cov}/{n_tot} horses ({cov_pct:.0f}%)  "
      f"— confidence: {conf_label}")
    if cov_pct < 20:
        p("  !! Results dominated by barrier/NR priors — treat win% as illustrative only")

    # Data-limited + mainland flags
    dl_flags = []
    for slug in slugs:
        name_lower = names[slug].lower()
        if name_lower in DATA_LIMITED:
            dl_flags.append(f"  ! DATA-LIMITED : {names[slug]} — barrier/NR priors only")
        if name_lower in MAINLAND_VISITOR:
            dl_flags.append(f"  ! MAINLAND VISITOR : {names[slug]} — mainland form only, not in dataset")
    for f in dl_flags:
        p(f)

    # SS barrier note
    if start_type == "SS":
        p("  ! STANDING START: barrier = tab number (approximation — BP column not parsed)")

    # ── Features ─────────────────────────────────────────────────────────────
    all_features, warnings = extract_all_features(race_info, data)

    # Deduplicate warnings
    seen = set()
    unique_warns = []
    for w in warnings:
        if w not in seen:
            unique_warns.append(w)
            seen.add(w)

    if unique_warns:
        p("\nData warnings:")
        for w in unique_warns:
            p(f"  ! {w}")

    # ── Priority race: track profile + pre/post table ─────────────────────
    if is_priority:
        show_track_profile(race_info)
        show_multiplier_table(race_info, all_features)

    # ── Simulation ───────────────────────────────────────────────────────────
    p(f"\nRunning {RUNS:,} simulations ...")
    results = run_simulation(race_info, all_features, n_runs=RUNS)

    # ── Report output ────────────────────────────────────────────────────────
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()

    if is_priority:
        generate_report(results, race_info, all_features, unique_warns)
    else:
        # Abbreviated: probability table + speed map only
        print(f"\n{'─'*78}")
        print(f"  MONTE CARLO — LAUNCESTON R{race_no}  ({race_info.distance_m}m {start_type})  [{RUNS:,} runs]  [LNCT]")
        print(f"{'─'*78}")
        _section_probability_table(results, race_info)
        print()
        _section_speed_map(results, race_info)
        print(f"{'─'*78}")

    sys.stdout = old_stdout
    p(buf.getvalue())

    # ── Manual form notes for key horses ─────────────────────────────────────
    if race_no == 3:
        p("\n[FORM NOTE — R3]")
        p("  Away Game (barrier 4, Ricky Duggan): ONLY horse with real Launceston/Burnie data.")
        p("  Won Burnie R4 (13 Mar 2026, 2180m MS) at 68% sim probability — validated.")
        p("  Z-score distortion: scoring negatively vs 10 data-absent rivals (all features=0).")
        p("  !! Sim result for Away Game is UNRELIABLE — manually flag as FORM PICK.")
        p("  No Nukes Skipper NZ (barrier 11): data-limited — 210 career starts, experienced.")
        p("  Debt Till We Part (barrier 10): 70 career starts, prolific — no Tasmanian data.")

    if race_no == 4:
        p("\n[FORM NOTE — R4]")
        p("  Easter Cup Heat 1. No horses found in dataset — ALL results are NR/barrier priors.")
        p("  Stepping Stones / Keayang Fitzy split (34.9% / 34.4%) reflects equal prior uncertainty.")
        p("  Vanquish Stride NZ (barrier 6, DATA LIMITED): treat as barrier prior only.")
        p("  Heath Woods (Stepping Stones) is a dataset driver — minor driver-quality signal only.")

    if race_no == 6:
        p("\n[FORM NOTE — R6]")
        p("  Easter Cup Heat 2. No horse stride_results found — ALL results are NR/barrier priors.")
        p("  Triedtotellya (barrier 10, Rohan Hillier): career 18-5-25, $268k prize money.")
        p("  Career win rate 72% (18/25) is exceptional — user flags as 'likely standout'.")
        p("  Iron Clad (barrier 4, NR75): sim leader 76.1% reflects NR/barrier prior only.")
        p("  Anything Goes NZ (barrier 6): DATA LIMITED — NZ form not in dataset.")
        p("  !! Manual form pick: Triedtotellya (career record superior, self-trained driver).")

    # ── Collect summary data ──────────────────────────────────────────────────
    win_pct = results['win_pct']
    place_pct = results['place_pct']
    pace_pct  = results['pace_pct']
    sorted_slugs = sorted(win_pct.keys(), key=lambda s: -win_pct[s])

    top1 = sorted_slugs[0] if len(sorted_slugs) > 0 else None
    top2 = sorted_slugs[1] if len(sorted_slugs) > 1 else None

    # Value flag: horse whose sim win% implies shorter odds than typical market
    # (No SP available — flag any horse >25% sim but field has 6+ runners as potential value)
    value_slug = None
    value_note = ""
    for slug in sorted_slugs:
        implied = 1.0 / win_pct[slug] if win_pct[slug] > 0 else 999
        # Flag if win% well above "equal chance" and likely to be underestimated
        if win_pct[slug] > 0.20 and implied < 5.0 and slug != top1:
            value_slug = slug
            value_note = f"${implied:.1f} implied — monitor vs TAB market"
            break
    if value_slug is None and top2 and win_pct.get(top2, 0) > 0.15:
        value_slug = top2
        implied = 1.0 / win_pct[top2] if win_pct[top2] > 0 else 999
        value_note = f"${implied:.1f} implied — potential value if market drifts"

    summary_rows.append({
        'race_no': race_no,
        'distance': race_info.distance_m,
        'start_type': start_type,
        'top1': names.get(top1, '?'),
        'top1_pct': win_pct.get(top1, 0),
        'top1_place': place_pct.get(top1, 0),
        'top2': names.get(top2, '?'),
        'top2_pct': win_pct.get(top2, 0),
        'value_horse': names.get(value_slug, '?') if value_slug else '—',
        'value_note': value_note,
        'is_priority': is_priority,
        'dl_flags': dl_flags,
        'conf': conf_label,
        'n_cov': n_cov,
        'n_tot': n_tot,
    })

# ── Race Night Summary Card ───────────────────────────────────────────────────
p()
p()
banner("RACE NIGHT SUMMARY — LAUNCESTON 22 MARCH 2026")
p("  [LNCT] UNVALIDATED TRACK — treat all outputs as directional, not definitive")
p("  No SP data available — implied odds computed from sim win%; compare vs TAB")
p()

p("  Confidence legend: PRIOR=barrier/NR priors only | LOW=1-2 horses w/data | MOD=partial data")
p()
hdr = f"{'R':<3} {'Dist':>5} {'ST':<3} {'Conf':<10} {'Sim Pick (!)':22} {'Win%':>5} {'Main Danger':<22} {'Dngr%':>5}"
p(hdr)
hr()

for row in summary_rows:
    rn = row['race_no']
    priority_marker = "*" if row['is_priority'] else " "
    cov_str = f"{row['n_cov']}/{row['n_tot']} {row['conf']}"
    p(
        f"R{rn:<2}{priority_marker} "
        f"{row['distance']:>5}m {row['start_type']:<3}  "
        f"{cov_str:<10}  "
        f"{row['top1']:<22} {row['top1_pct']*100:>4.1f}%  "
        f"{row['top2']:<22} {row['top2_pct']*100:>4.1f}%"
    )

p()
p("  * = Priority race (full diagnostic)  | (!) = sim pick, NOT a manual form recommendation")
p()
p("VALUE / MANUAL FLAGS (where sim is overridden by form knowledge):")
p("  R3: Away Game (barrier 4) — MANUAL FORM PICK. Only real-data horse in field.")
p("       Sim shows Paziah leading (barrier prior). Away Game won Burnie R4 9 days ago.")
p("  R6: Triedtotellya (barrier 10) — MANUAL FORM PICK. Career 18-5-25 (72% win rate).")
p("       Sim shows Iron Clad (NR/barrier prior). Triedtotellya is self-trained standout.")
p()
p("NOTES PER RACE:")
for row in summary_rows:
    rn = row['race_no']
    notes = []
    if row['dl_flags']:
        for f in row['dl_flags']:
            notes.append(f.strip())
    if row['start_type'] == "SS":
        notes.append("Standing start — barrier approx (tab used as proxy for BP)")
    if row['is_priority']:
        notes.append("Priority race — pre/post multiplier table included above")
    if notes:
        p(f"  R{rn}: " + " | ".join(notes))

p()
p("=" * 78)
p("  END OF SIMULATION — LAUNCESTON 22 MARCH 2026")
p("  Track profile: Launceston (unvalidated)  |  2,000 runs per race")
p("=" * 78)

# ── Save output ───────────────────────────────────────────────────────────────
outpath = "launceston_22march2026_sim.txt"
with open(outpath, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print(f"\n[SAVED] Output written to {outpath}")
