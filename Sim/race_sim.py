"""
race_sim.py — Monte Carlo Race Simulation Engine.

CLI Usage:
    # List all races in a PDF form guide:
    python race_sim.py --form "Hobart Harness 15-03-2026.pdf" --data output/claude_data

    # Simulate race 3 from a PDF form:
    python race_sim.py --form "Hobart Harness 15-03-2026.pdf" --race 3 --data output/claude_data

    # More simulation runs for stability:
    python race_sim.py --form "Hobart Harness 15-03-2026.pdf" --race 3 --data output/claude_data --runs 1000
"""

from __future__ import annotations

import argparse
import os
import sys

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from form_parser import parse_form, Runner, RaceInfo, parse_fields_doc, _is_fields_doc, normalise_track
from feature_extractor import DataLoader, extract_all_features
from report_generator import generate_report
from agent_model import run_single_agentbased
from sim_config import (
    WEIGHT_RANGES, TRACK_OVERRIDES, STANDING_START_BARRIER_ADJUSTMENT,
    DEFAULT_RUNS, TRACK_PROFILES,
    DLW_DECAY, THIN_DATA_THRESHOLD,
    MAX_WIN_PCT, MAX_PLACE_PCT,
    CONVERGENCE_BATCH_SIZE, CONVERGENCE_THRESHOLD, CONVERGENCE_STABLE_BATCHES,
    WIN_DROUGHT_WARN_DAYS,
    ODM_MOBILE_PENALTY_SHORT, ODM_MOBILE_PENALTY_LONG,
)


# ---------------------------------------------------------------------------
# Ordered feature names that feed the weighted score (z-scored across field)
# ---------------------------------------------------------------------------
SCORED_FEATURES = [
    'gate_speed',
    'finishing_speed',
    'mile_rate_trend',
    'venue_win_rate',
    'venue_place_rate',
    'barrier_score',
    'driver_venue_rate',
    'driver_season_winrate',
    'driver_horse_combo',
    'class_relativity',
    'last_800m_pos',
    'width_penalty',
    'position_800m_margin',
    'position_400m_margin',
    'sp_vs_performance',
    'distance_suitability',
    'start_type_rate',
    'trainer_form',
    'track_condition_rate',
    # Lower-priority features
    'class_trajectory',
    'winner_beaten_quality',
    'days_since_last_win',
    'last_win_venue_match',
    'driver_group_experience',
    'driver_experience_years',
    'distance_optimal_range',
    'career_class_experience',
    'mobile_barrier_rate',
    'gate_behaviour_stewards',
    'head_to_head',
]

# Features that feed the pace (800m position) sub-score
PACE_FEATURES = ['gate_speed', 'barrier_score', 'last_800m_pos', 'position_800m_margin']

# Fixed-weight features (not subject to weight variation)
FIXED_FEATURES = ['stewards_flag', 'freshness', 'injury_return', 'driver_suspended']
FIXED_WEIGHTS = {
    'stewards_flag':    0.4,
    'freshness':        0.3,
    'injury_return':    1.0,   # already pre-scaled to -0.6 or 0
    'driver_suspended': 0.8,   # -1.0 raw → strong downgrade
}


# ---------------------------------------------------------------------------
# Weight sampling
# ---------------------------------------------------------------------------

def _effective_weight_ranges(track: str, start_type: str) -> Dict[str, Tuple[float, float]]:
    """Apply track-specific and start-type overrides to base weight ranges."""
    ranges = {k: list(v) for k, v in WEIGHT_RANGES.items()}

    overrides = TRACK_OVERRIDES.get(track, {})
    for wname, (delta_lo, delta_hi) in overrides.items():
        if wname in ranges:
            ranges[wname][0] = max(0.0, ranges[wname][0] + delta_lo)
            ranges[wname][1] = max(ranges[wname][0], ranges[wname][1] + delta_hi)

    if start_type == 'SS':
        adj = STANDING_START_BARRIER_ADJUSTMENT
        ranges['w_barrier'][0] = max(0.0, ranges['w_barrier'][0] + adj)
        ranges['w_barrier'][1] = max(ranges['w_barrier'][0], ranges['w_barrier'][1] + adj)

    return {k: tuple(v) for k, v in ranges.items()}


def draw_weights(ranges: Dict[str, Tuple[float, float]]) -> Dict[str, float]:
    """Sample one weight vector uniformly within each feature's range."""
    return {name: float(np.random.uniform(lo, hi)) for name, (lo, hi) in ranges.items()}


# ---------------------------------------------------------------------------
# Feature normalisation
# ---------------------------------------------------------------------------

def zscore_field(
    all_features: Dict[str, Dict[str, float]],
    slugs: List[str],
    feature_names: List[str],
) -> Dict[str, Dict[str, float]]:
    """Z-score each feature across the field.

    Returns z_scores[slug][feature] = normalised value.
    If std == 0 (all equal), everyone gets 0.
    """
    z: Dict[str, Dict[str, float]] = {slug: {} for slug in slugs}

    for feat in feature_names:
        vals = [all_features[s].get(feat, 0.0) for s in slugs]
        arr = np.array(vals, dtype=float)
        mean = arr.mean()
        std = arr.std()
        if std > 0:
            normalised = (arr - mean) / std
        else:
            normalised = np.zeros_like(arr)
        for i, slug in enumerate(slugs):
            z[slug][feat] = float(normalised[i])

    return z


# ---------------------------------------------------------------------------
# Simulation core
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DLW decay and convergence utilities
# ---------------------------------------------------------------------------

def _compute_dlw_decay(dlw_score: float) -> float:
    """Map normalised days_since_last_win score → pre-sim feature decay multiplier.

    dlw_score is the feature value from compute_days_since_last_win():
      +1.0 = very recent win (0 days)
       0.0 = ~WIN_DROUGHT_WARN_DAYS/2 days since last win
      -1.0 = never won or >WIN_DROUGHT_WARN_DAYS days

    Returns a multiplier in [0.30, 1.00] applied to z-scores before simulation.
    Only applies meaningful decay when approximated days exceed 200.
    """
    if dlw_score <= -0.9:  # Never won or extreme drought
        return 0.30
    # Approximate raw days from normalised score:
    # score = 1.0 - (days / WIN_DROUGHT_WARN_DAYS)  →  days = (1.0 - score) * WDWD
    approx_days = (1.0 - dlw_score) * WIN_DROUGHT_WARN_DAYS

    # Interpolate DLW_DECAY breakpoint table
    table = sorted(DLW_DECAY.items())   # [(days, mult), ...]
    for i in range(len(table) - 1):
        d0, m0 = table[i]
        d1, m1 = table[i + 1]
        if d0 <= approx_days <= d1:
            t = (approx_days - d0) / (d1 - d0)
            return float(m0 + t * (m1 - m0))
    return 0.30   # Beyond last breakpoint


def _apply_dlw_decay_to_zscores(
    z_scores: Dict[str, Dict[str, float]],
    all_features: Dict[str, Dict[str, float]],
    slugs: List[str],
) -> Dict[str, Dict[str, float]]:
    """Pre-simulation: scale z-scores for horses with long win droughts.

    Returns a new z_scores dict (original untouched) with DLW multipliers applied
    to gate_speed, finishing_speed, class_relativity, and mile_rate_trend.

    Horses with DLW <= 200 days are unaffected (multiplier = 1.0).
    """
    scaled = {slug: dict(z_scores[slug]) for slug in slugs}
    _dlw_affected_features = ('gate_speed', 'finishing_speed', 'class_relativity', 'mile_rate_trend')

    for slug in slugs:
        dlw_score = float(all_features.get(slug, {}).get('days_since_last_win', 0.0))
        decay = _compute_dlw_decay(dlw_score)
        if decay < 1.0:
            for feat in _dlw_affected_features:
                if feat in scaled[slug]:
                    scaled[slug][feat] *= decay

    return scaled


def _check_convergence(
    win_pcts_history: List[Dict[str, float]],
    slugs: List[str],
) -> bool:
    """Return True if top-3 horses' win% has stabilised across the last two checkpoints.

    Convergence criterion: max shift in win% for top-3 slugs is < CONVERGENCE_THRESHOLD
    for CONVERGENCE_STABLE_BATCHES consecutive batch comparisons.
    """
    if len(win_pcts_history) < CONVERGENCE_STABLE_BATCHES + 1:
        return False

    # Identify top-3 by most recent win%
    latest = win_pcts_history[-1]
    top3 = sorted(latest, key=latest.__getitem__, reverse=True)[:3]

    stable_count = 0
    for i in range(len(win_pcts_history) - 1, 0, -1):
        prev = win_pcts_history[i - 1]
        curr = win_pcts_history[i]
        max_shift = max(abs(curr.get(s, 0.0) - prev.get(s, 0.0)) for s in top3)
        if max_shift < CONVERGENCE_THRESHOLD:
            stable_count += 1
        else:
            break   # Need consecutive stable batches
        if stable_count >= CONVERGENCE_STABLE_BATCHES:
            return True

    return False


def run_simulation(
    race_info: RaceInfo,
    all_features: Dict[str, Dict[str, float]],
    n_runs: int = DEFAULT_RUNS,
) -> Dict:
    """Run the full agent-based simulation (Monte Carlo over the agent model).

    Each run draws a weight vector, then calls run_single_agentbased() which
    simulates the race in three physical phases. Finish order emerges from
    position state rather than from a pre-computed score.

    Returns a results dict with:
        win_pct, place_pct (top-3), avg_pos,
        pace_pct (leader/on_pace/midfield/back),
        weight_win_rates (robustness analysis),
        n_runs (actual runs completed),
        convergence_run (run count when converged, or None),
    """
    runners = race_info.runners
    slugs = [r.slug for r in runners]
    n = len(slugs)

    # --- Inject raw barrier into features for agent model ---
    for runner in runners:
        if runner.slug in all_features:
            all_features[runner.slug]['_barrier'] = float(runner.barrier)

    # --- Inject ODM multiplier into features ---
    distance_m = race_info.distance_m or 0
    for runner in runners:
        if runner.slug in all_features:
            if getattr(runner, 'odm_mobile', False) and race_info.start_type == 'MS':
                mult = ODM_MOBILE_PENALTY_SHORT if distance_m < 2000 else ODM_MOBILE_PENALTY_LONG
            else:
                mult = 1.0
            all_features[runner.slug]['_odm_multiplier'] = mult

    # --- Normalise features across field ---
    z_scores = zscore_field(all_features, slugs, SCORED_FEATURES)

    # --- Pre-simulation DLW decay: scale z-scores for horses on win droughts ---
    z_scores = _apply_dlw_decay_to_zscores(z_scores, all_features, slugs)

    # --- Track profile for agent model ---
    track_profile = TRACK_PROFILES.get(race_info.track, {})
    # Resolve proxy tracks (e.g. Scottsdale uses Burnie)
    if 'use_proxy' in track_profile:
        track_profile = TRACK_PROFILES.get(track_profile['use_proxy'], track_profile)

    # --- Effective weight ranges for this track/start type ---
    eff_ranges = _effective_weight_ranges(race_info.track, race_info.start_type)

    # --- Inject race distance into track profile for checkpoint calculations ---
    track_profile['race_distance_m'] = float(race_info.distance_m or 2000)

    # --- Accumulators ---
    win_counts  = {s: 0 for s in slugs}
    place_counts = {s: 0 for s in slugs}  # top 3
    pos_total    = {s: 0 for s in slugs}  # sum of finish positions (for avg)
    pace_counts  = {s: {'leader': 0, 'on_pace': 0, 'midfield': 0, 'back': 0} for s in slugs}
    mid_counts   = {s: {'leader': 0, 'on_pace': 0, 'midfield': 0, 'back': 0} for s in slugs}
    phase1_top3  = {s: 0 for s in slugs}
    phase2_top3  = {s: 0 for s in slugs}

    gate_wins = {'low': {s: 0 for s in slugs}, 'mid': {s: 0 for s in slugs}, 'high': {s: 0 for s in slugs}}
    gate_runs = {'low': 0, 'mid': 0, 'high': 0}

    # --- Sectional accumulators (harness.au format) ---
    sect_accum = {s: {
        'gap_800m': 0.0, 'gap_400m': 0.0, 'gap_finish': 0.0,
        'q3_time': 0.0, 'q4_time': 0.0,
        'gained_800_400': 0.0, 'gained_400_finish': 0.0,
    } for s in slugs}

    # --- Convergence tracking ---
    win_pcts_history: List[Dict[str, float]] = []
    convergence_run: int = None
    runs_done = 0

    # --- Main simulation loop ---
    for run_idx in range(n_runs):
        weights = draw_weights(eff_ranges)

        finish_order, pace_order, pace_positions, mid_positions, checkpoints = run_single_agentbased(
            slugs=slugs,
            z_scores=z_scores,
            raw_features=all_features,
            weights=weights,
            track=race_info.track,
            start_type=race_info.start_type,
            track_profile=track_profile,
        )

        runs_done += 1

        # Tally finishing positions
        for pos, slug in enumerate(finish_order):
            if pos == 0:
                win_counts[slug] += 1
            if pos < 3:
                place_counts[slug] += 1
            pos_total[slug] += (pos + 1)

        # Tally pace positions (800m)
        for slug, bucket in pace_positions.items():
            std_bucket = bucket if bucket in pace_counts[slug] else (
                'on_pace' if bucket in ('garden_seat', 'midfield_runner') else bucket
            )
            if std_bucket in pace_counts[slug]:
                pace_counts[slug][std_bucket] += 1

        # Tally mid positions (400m)
        for slug, bucket in mid_positions.items():
            if bucket in mid_counts[slug]:
                mid_counts[slug][bucket] += 1

        # Tally top-3 at 800m and 400m
        for idx, slug in enumerate(pace_order[:3]):
            phase1_top3[slug] += 1
        mid_sorted = sorted(mid_positions.keys(),
                           key=lambda s: {'leader': 0, 'on_pace': 1, 'midfield': 2, 'back': 3}.get(mid_positions[s], 4))
        for slug in mid_sorted[:3]:
            phase2_top3[slug] += 1

        # Accumulate sectional checkpoint data
        ckpt_800 = {c.slug: c for c in checkpoints['800m']}
        ckpt_400 = {c.slug: c for c in checkpoints['400m']}
        ckpt_fin = {c.slug: c for c in checkpoints['finish']}
        for slug in slugs:
            sa = sect_accum[slug]
            if slug in ckpt_800:
                sa['gap_800m'] += ckpt_800[slug].gap_to_leader_m
            if slug in ckpt_400:
                sa['gap_400m'] += ckpt_400[slug].gap_to_leader_m
                sa['q3_time'] += ckpt_400[slug].section_time_s
                sa['gained_800_400'] += ckpt_400[slug].metres_gained_vs_leader
            if slug in ckpt_fin:
                sa['gap_finish'] += ckpt_fin[slug].gap_to_leader_m
                sa['q4_time'] += ckpt_fin[slug].section_time_s
                sa['gained_400_finish'] += ckpt_fin[slug].metres_gained_vs_leader

        # Robustness binning by w_gate_speed tertile
        g = weights['w_gate_speed']
        lo, hi = eff_ranges['w_gate_speed']
        mid_pt = (lo + hi) / 2
        q1 = lo + (mid_pt - lo) / 2
        q3 = mid_pt + (hi - mid_pt) / 2
        band = 'low' if g < q1 else ('mid' if g < q3 else 'high')
        gate_runs[band] += 1
        gate_wins[band][finish_order[0]] += 1

        # --- Convergence check every CONVERGENCE_BATCH_SIZE runs ---
        if convergence_run is None and runs_done % CONVERGENCE_BATCH_SIZE == 0:
            current_pcts = {s: win_counts[s] / runs_done for s in slugs}
            win_pcts_history.append(current_pcts)
            if _check_convergence(win_pcts_history, slugs):
                convergence_run = runs_done
                print(f"[race_sim] Converged at {runs_done:,} runs")
                break

    if convergence_run is None:
        print(f"[race_sim] Ran full {runs_done:,} runs without early convergence")

    # --- Convert counts to probabilities ---
    win_pct   = {s: win_counts[s]  / runs_done for s in slugs}
    place_pct = {s: place_counts[s] / runs_done for s in slugs}
    avg_pos   = {s: pos_total[s]   / runs_done for s in slugs}
    pace_pct  = {
        s: {k: v / runs_done for k, v in pace_counts[s].items()}
        for s in slugs
    }
    mid_pct   = {
        s: {k: v / runs_done for k, v in mid_counts[s].items()}
        for s in slugs
    }

    # --- Robustness: win rate per weight band ---
    weight_win_rates = {}
    for slug in slugs:
        rates = {}
        for band_key in ('low', 'mid', 'high'):
            denom = gate_runs[band_key]
            rates[band_key] = gate_wins[band_key][slug] / denom if denom > 0 else 0.0
        weight_win_rates[slug] = rates

    # --- DLW multiplier ---
    dlw_pts = sorted(DLW_DECAY.items())
    def _dlw_mult(days: float) -> float:
        if days <= 0:
            return 1.0
        for i in range(len(dlw_pts) - 1):
            lo_d, lo_m = dlw_pts[i]
            hi_d, hi_m = dlw_pts[i + 1]
            if lo_d <= days <= hi_d:
                t = (days - lo_d) / (hi_d - lo_d)
                return lo_m + t * (hi_m - lo_m)
        return dlw_pts[-1][1]

    for slug in slugs:
        dlw = all_features[slug].get('_days_since_last_win_raw', 0.0)
        win_pct[slug] *= _dlw_mult(dlw)

    # Re-normalise win% to sum to 1.0
    total_win = sum(win_pct.values())
    if total_win > 0:
        win_pct = {s: v / total_win for s, v in win_pct.items()}

    # --- Thin data regression to mean ---
    field_avg_win = 1.0 / len(slugs)
    field_avg_place = 3.0 / len(slugs)
    for slug in slugs:
        conf = all_features[slug].get('_data_confidence', 1.0)
        if conf < THIN_DATA_THRESHOLD:
            blend = conf / THIN_DATA_THRESHOLD
            win_pct[slug] = blend * win_pct[slug] + (1.0 - blend) * field_avg_win
            place_pct[slug] = blend * place_pct[slug] + (1.0 - blend) * field_avg_place

    # --- Win% cap with proportional redistribution ---
    def _apply_cap(pct_dict, cap, slug_list):
        for s in slug_list:
            if pct_dict[s] > cap:
                excess = pct_dict[s] - cap
                pct_dict[s] = cap
                others = [x for x in slug_list if x != s]
                other_total = sum(pct_dict[x] for x in others)
                if other_total > 0:
                    for x in others:
                        pct_dict[x] += excess * (pct_dict[x] / other_total)
        return pct_dict

    win_pct = _apply_cap(win_pct, MAX_WIN_PCT, slugs)
    place_pct = _apply_cap(place_pct, MAX_PLACE_PCT, slugs)

    # --- Average sectional data across runs ---
    sectionals = {}
    for slug in slugs:
        sa = sect_accum[slug]
        sectionals[slug] = {
            'avg_800m_margin':      sa['gap_800m'] / runs_done,
            'avg_400m_margin':      sa['gap_400m'] / runs_done,
            'avg_finish_margin':    sa['gap_finish'] / runs_done,
            'avg_q3_time':          sa['q3_time'] / runs_done,
            'avg_q4_time':          sa['q4_time'] / runs_done,
            'avg_gained_800_400':   sa['gained_800_400'] / runs_done,
            'avg_gained_400_finish': sa['gained_400_finish'] / runs_done,
        }

    return {
        'win_pct':          win_pct,
        'place_pct':        place_pct,
        'avg_pos':          avg_pos,
        'pace_pct':         pace_pct,
        'mid_pct':          mid_pct,
        'phase1_top3_pct':  {s: phase1_top3[s] / runs_done for s in slugs},
        'phase2_top3_pct':  {s: phase2_top3[s] / runs_done for s in slugs},
        'weight_win_rates': weight_win_rates,
        'sectionals':       sectionals,
        'n_runs':           runs_done,
        'convergence_run':  convergence_run,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Monte Carlo Race Simulation Engine — Tasmanian Harness Racing'
    )
    parser.add_argument(
        '--form', default=None,
        help=(
            'Form input — one of:\n'
            '  PDF file path:  "Hobart Harness 15-03-2026.pdf"\n'
            '  Text file path: "race.txt"  (any non-.pdf file is read as pasted text)\n'
            '  Stdin:          "-"  (pipe or redirect: echo "..." | python race_sim.py --form -)\n'
            'Omit entirely to read pasted text from stdin interactively.'
        )
    )
    parser.add_argument(
        '--data', default='output/claude_data',
        help='Path to data directory (default: output/claude_data)'
    )
    parser.add_argument(
        '--runs', type=int, default=DEFAULT_RUNS,
        help=f'Number of simulation runs (default: {DEFAULT_RUNS})'
    )
    parser.add_argument(
        '--track',
        help='Override track name (e.g. Hobart, Burnie, Launceston)'
    )
    parser.add_argument(
        '--race', type=int, default=0,
        help='Race number to simulate from a PDF (e.g. --race 3). Omit to list all races.'
    )
    parser.add_argument(
        '--start', choices=['MS', 'SS'],
        help='Override start type: MS = mobile, SS = standing'
    )
    parser.add_argument(
        '--all', action='store_true',
        help='Simulate all races in the form document sequentially'
    )
    args = parser.parse_args()

    # 1. Resolve form input → string passed to parse_form
    #
    #   --form foo.pdf        → PDF path (unchanged)
    #   --form foo.txt        → read file, pass text content
    #   --form -              → read stdin, pass text content
    #   --form omitted        → read stdin interactively, pass text content
    #   --form "1. Horse ..." → inline text (no file, not .pdf)

    form_arg = args.form

    if form_arg is None or form_arg == '-':
        # Read from stdin
        if form_arg is None and sys.stdin.isatty():
            print("[race_sim] No --form given. Paste race field below, then press Ctrl-D (EOF):\n")
        form_str = sys.stdin.read()
        label = '<stdin>'
    elif form_arg.lower().endswith('.pdf'):
        form_str = form_arg          # parse_form handles PDF path directly
        label = form_arg
    elif os.path.isfile(form_arg):
        # Text file — read its contents
        with open(form_arg, encoding='utf-8', errors='replace') as fh:
            form_str = fh.read()
        label = form_arg
    else:
        # Treat as inline pasted text
        form_str = form_arg
        label = '<inline text>'

    print(f"\n[race_sim] Parsing form: {label!r}")

    # --- --all mode: simulate every race in the document ---
    if args.all:
        if not _is_fields_doc(form_str):
            print("[ERROR] --all requires a harness.au fields document", file=sys.stderr)
            sys.exit(1)
        races = parse_fields_doc(form_str, data_dir=args.data, filename=label)
        if args.track:
            for r in races:
                r.track = normalise_track(args.track)
        if args.start:
            for r in races:
                r.start_type = args.start
        print(f"[race_sim] {len(races)} races found — simulating all\n")

        data = DataLoader(args.data)
        summary = []

        for race_info in races:
            if not race_info.runners:
                print(f"  R{race_info.race_no}: No active runners — skipping\n")
                continue

            print(f"{'='*60}")
            print(f"R{race_info.race_no}: {race_info.distance_m}m {race_info.start_type} "
                  f"— {len(race_info.runners)} runners")
            print(f"{'='*60}")
            for r in race_info.runners:
                print(f"  {r.tab_no or r.barrier:>2}.  {r.horse:<28}  Barrier {r.barrier}  "
                      f"Driver: {r.driver}  NR: {r.nr or '—'}")

            all_features, warnings = extract_all_features(race_info, data)
            print(f"\n[race_sim] Running {args.runs:,} simulations ...")
            results = run_simulation(race_info, all_features, n_runs=args.runs)
            print()
            generate_report(results, race_info, all_features, warnings)

            # Collect top-3 for summary
            win_pct = results['win_pct']
            top3 = []
            for s in sorted(win_pct, key=win_pct.get, reverse=True)[:3]:
                horse = next((r.horse for r in race_info.runners if r.slug == s), s)
                top3.append((horse, win_pct[s]))
            summary.append((race_info.race_no, race_info.distance_m, race_info.start_type, top3))

        # Print meeting summary
        print(f"\n\n{'='*70}")
        print("MEETING SUMMARY")
        print(f"{'='*70}")
        for race_no, dist, st, top3 in summary:
            line_parts = [f"R{race_no:<2} {dist:>5}m {st}  "]
            for i, (horse, pct) in enumerate(top3):
                tag = ['1st', '2nd', '3rd'][i]
                line_parts.append(f"{tag}: {horse[:22]:<22} {pct*100:5.1f}%  ")
            print(''.join(line_parts))
        return

    # --- Single-race mode ---
    try:
        race_info = parse_form(
            form_str=form_str,
            data_dir=args.data,
            track_override=args.track,
            race_no=args.race,
            filename=label,
        )
    except Exception as e:
        print(f"[ERROR] Could not parse form: {e}", file=sys.stderr)
        sys.exit(1)

    # Apply start-type override after parsing
    if args.start:
        race_info.start_type = args.start

    if not race_info.runners:
        print("[ERROR] No runners found in form input.", file=sys.stderr)
        sys.exit(1)

    print(f"[race_sim] Track: {race_info.track}  Date: {race_info.date}  "
          f"Race: {race_info.race_no}  Start: {race_info.start_type}  "
          f"Runners: {len(race_info.runners)}")
    for r in race_info.runners:
        print(f"  {r.tab_no or r.barrier:>2}.  {r.horse:<28}  Barrier {r.barrier}  "
              f"Driver: {r.driver}  NR: {r.nr or '—'}")

    # 2. Load data
    print(f"\n[race_sim] Loading data from {args.data!r} ...")
    data = DataLoader(args.data)

    # 3. Extract features
    print("[race_sim] Extracting features ...")
    all_features, warnings = extract_all_features(race_info, data)

    if warnings:
        print("\n[WARNINGS]")
        seen = set()
        for w in warnings:
            if w not in seen:
                print(f"  ! {w}")
                seen.add(w)

    # 4. Run simulation
    print(f"\n[race_sim] Running {args.runs:,} simulations ...")
    results = run_simulation(race_info, all_features, n_runs=args.runs)

    # 5. Generate report
    print()
    generate_report(results, race_info, all_features, warnings)


if __name__ == '__main__':
    main()
