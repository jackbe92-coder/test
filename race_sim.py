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
import sys

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from form_parser import parse_form, Runner, RaceInfo
from feature_extractor import DataLoader, extract_all_features
from report_generator import generate_report
from track_profiles import apply_track_profile
from sim_config import (
    WEIGHT_RANGES, TRACK_OVERRIDES, STANDING_START_BARRIER_ADJUSTMENT,
    DEFAULT_RUNS, PACE_GAP_LEADER, PACE_GAP_ON_PACE, PACE_GAP_MIDFIELD,
    FIELD_STRENGTH_BASELINE,
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

def run_single(
    slugs: List[str],
    z_scores: Dict[str, Dict[str, float]],
    raw_features: Dict[str, Dict[str, float]],
    weights: Dict[str, float],
    track: str = '',
) -> Tuple[List[str], List[str], Dict[str, str]]:
    """Run one simulation.

    Returns:
        finishing_order: list of slugs from 1st to last
        pace_order:      list of slugs by 800m position (front to back)
        pace_positions:  {slug: position_label} for this run
    """
    n = len(slugs)
    main_scores = np.zeros(n)
    pace_score_dict: Dict[str, float] = {}

    for i, slug in enumerate(slugs):
        z = z_scores[slug]

        # --- Main score: variable-weighted features ---
        raw = raw_features[slug]

        # --- Field strength: scales class discrimination ---
        fs = raw.get('field_strength', FIELD_STRENGTH_BASELINE)
        field_scale = fs / FIELD_STRENGTH_BASELINE  # 1.0 at baseline, higher in strong fields

        # --- Field size: scales barrier advantage (bigger field → draw matters more) ---
        field_size_ratio = raw.get('field_size_adjustment', 1.0)
        barrier_scale = 0.7 + 0.3 * min(field_size_ratio, 2.0) / 2.0  # 0.7–1.0

        # --- Driver experience modifier: scales driver quality weight ---
        exp_mod = raw.get('driver_experience_years', 0.0)
        driver_quality_scale = 1.0 + exp_mod * 0.3  # ±15% for junior/veteran

        s = (
            weights['w_gate_speed'] * (
                z.get('gate_speed', 0)           * 0.35 +
                z.get('last_800m_pos', 0)         * 0.20 +
                z.get('position_800m_margin', 0)  * 0.30 +
                z.get('position_400m_margin', 0)  * 0.15
            ) +
            weights['w_finishing_speed'] * (
                z.get('finishing_speed', 0) * 0.70 +
                z.get('width_penalty', 0)   * 0.30
            ) +
            weights['w_barrier'] * barrier_scale * (
                z.get('barrier_score', 0)      * 0.50 +
                z.get('start_type_rate', 0)    * 0.25 +
                z.get('mobile_barrier_rate', 0) * 0.25
            ) +
            weights['w_venue_rate'] * (
                z.get('venue_win_rate', 0)        * 0.45 +
                z.get('venue_place_rate', 0)      * 0.25 +
                z.get('last_win_venue_match', 0)  * 0.15 +
                z.get('track_condition_rate', 0)  * 0.15
            ) +
            weights['w_driver']         * z.get('driver_venue_rate', 0) +
            weights['w_driver_quality'] * driver_quality_scale * z.get('driver_season_winrate', 0) +
            weights['w_driver_combo']   * z.get('driver_horse_combo', 0) +
            weights['w_driver_class']   * z.get('driver_group_experience', 0) +
            weights['w_class'] * field_scale * (
                z.get('class_relativity', 0)       * 0.50 +
                z.get('class_trajectory', 0)       * 0.30 +
                z.get('career_class_experience', 0) * 0.20
            ) +
            weights['w_trend'] * (
                z.get('mile_rate_trend', 0)       * 0.50 +
                z.get('winner_beaten_quality', 0) * 0.30 +
                z.get('days_since_last_win', 0)   * 0.20
            ) +
            weights['w_value']    * z.get('sp_vs_performance', 0) +
            weights['w_distance'] * (
                z.get('distance_suitability', 0)   * 0.55 +
                z.get('distance_optimal_range', 0) * 0.45
            ) +
            weights['w_trainer']  * z.get('trainer_form', 0)
        )

        # --- Fixed adjustments (raw values, not z-scored) ---
        s += FIXED_WEIGHTS['stewards_flag']    * raw.get('stewards_flag', 0)
        s += FIXED_WEIGHTS['freshness']        * raw.get('freshness', 0)
        s += FIXED_WEIGHTS['injury_return']    * raw.get('injury_return', 0)
        s += FIXED_WEIGHTS['driver_suspended'] * raw.get('driver_suspended', 0)

        # --- Luck noise — wider for inconsistent horses ---
        consistency = raw.get('consistency_score', 0.0)
        noise_scale = 1.0 + min(consistency * 0.15, 1.0)  # max 2x noise for very erratic horses
        s += np.random.normal(0, weights['w_luck'] * noise_scale)

        main_scores[i] = s

        # --- Pace score (800m position): gate speed + barrier + positional margins ---
        p = (
            weights['w_gate_speed'] * (
                z.get('gate_speed', 0)          * 0.50 +
                z.get('position_800m_margin', 0) * 0.35 +
                z.get('last_800m_pos', 0)        * 0.15
            ) +
            weights['w_barrier'] * z.get('barrier_score', 0) * 0.5
        )
        p += np.random.normal(0, weights['w_luck'] * 0.5)
        pace_score_dict[slug] = float(p)

    # --- Track profile post-processing (fixed multipliers, stochastic positions) ---
    main_score_dict = {slugs[i]: float(main_scores[i]) for i in range(n)}
    if track:
        adjusted_dict, pace_positions = apply_track_profile(main_score_dict, pace_score_dict, track)
    else:
        adjusted_dict = main_score_dict
        pace_positions = {s: 'midfield' for s in slugs}

    adjusted_arr = np.array([adjusted_dict[s] for s in slugs])
    finish_order = [slugs[i] for i in np.argsort(-adjusted_arr)]
    pace_order = [slugs[i] for i in np.argsort([-pace_score_dict[s] for s in slugs])]

    return finish_order, pace_order, pace_positions


def assign_pace_bucket(rank: int, n_runners: int, leader_score: float, my_score: float) -> str:
    """Classify pace position based on z-score gap from leader."""
    gap = leader_score - my_score
    if gap <= PACE_GAP_LEADER:
        return 'leader'
    elif gap <= PACE_GAP_ON_PACE:
        return 'on_pace'
    elif gap <= PACE_GAP_MIDFIELD:
        return 'midfield'
    else:
        return 'back'


def run_simulation(
    race_info: RaceInfo,
    all_features: Dict[str, Dict[str, float]],
    n_runs: int = DEFAULT_RUNS,
) -> Dict:
    """Run the full Monte Carlo simulation.

    Returns a results dict with:
        win_counts, place_counts (top-3), pos_counts (all positions),
        pace_counts (leader/on_pace/midfield/back),
        weight_samples (list of weight dicts),
        weight_win_rates (for robustness analysis)
    """
    runners = race_info.runners
    slugs = [r.slug for r in runners]
    n = len(slugs)

    # Normalise features across field
    z_scores = zscore_field(all_features, slugs, SCORED_FEATURES)

    # Effective weight ranges for this track/start type
    eff_ranges = _effective_weight_ranges(race_info.track, race_info.start_type)

    # Accumulators
    win_counts = {s: 0 for s in slugs}
    place_counts = {s: 0 for s in slugs}  # top 3
    pos_total = {s: 0 for s in slugs}     # sum of finish positions (for avg)
    pace_counts = {s: {'leader': 0, 'on_pace': 0, 'midfield': 0, 'back': 0} for s in slugs}
    weight_samples = []

    # For robustness: bin runs by w_gate_speed tertile and track win rates
    gate_wins = {'low': {s: 0 for s in slugs}, 'mid': {s: 0 for s in slugs}, 'high': {s: 0 for s in slugs}}
    gate_runs = {'low': 0, 'mid': 0, 'high': 0}

    for _ in range(n_runs):
        weights = draw_weights(eff_ranges)
        weight_samples.append(weights)

        finish_order, pace_order, pace_positions = run_single(
            slugs, z_scores, all_features, weights, track=race_info.track
        )

        # Tally finishing positions
        for pos, slug in enumerate(finish_order):
            if pos == 0:
                win_counts[slug] += 1
            if pos < 3:
                place_counts[slug] += 1
            pos_total[slug] += (pos + 1)

        # Tally pace positions from track profile assignment
        for slug, bucket in pace_positions.items():
            # Map sprint-lane labels back to standard buckets for the report counter
            std_bucket = bucket if bucket in pace_counts[slug] else (
                'on_pace' if bucket in ('garden_seat', 'midfield_runner') else bucket
            )
            if std_bucket in pace_counts[slug]:
                pace_counts[slug][std_bucket] += 1

        # Robustness binning
        g = weights['w_gate_speed']
        lo, hi = eff_ranges['w_gate_speed']
        mid = (lo + hi) / 2
        q1 = lo + (mid - lo) / 2
        q3 = mid + (hi - mid) / 2
        if g < q1:
            band = 'low'
        elif g < q3:
            band = 'mid'
        else:
            band = 'high'
        gate_runs[band] += 1
        winner = finish_order[0]
        gate_wins[band][winner] += 1

    # Convert counts to probabilities
    win_pct = {s: win_counts[s] / n_runs for s in slugs}
    place_pct = {s: place_counts[s] / n_runs for s in slugs}
    avg_pos = {s: pos_total[s] / n_runs for s in slugs}
    pace_pct = {
        s: {k: v / n_runs for k, v in pace_counts[s].items()}
        for s in slugs
    }

    # Robustness: win rate per weight band
    weight_win_rates = {}
    for slug in slugs:
        rates = {}
        for band in ('low', 'mid', 'high'):
            denom = gate_runs[band]
            rates[band] = gate_wins[band][slug] / denom if denom > 0 else 0.0
        weight_win_rates[slug] = rates

    return {
        'win_pct': win_pct,
        'place_pct': place_pct,
        'avg_pos': avg_pos,
        'pace_pct': pace_pct,
        'weight_win_rates': weight_win_rates,
        'n_runs': n_runs,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Monte Carlo Race Simulation Engine — Tasmanian Harness Racing'
    )
    parser.add_argument(
        '--form', required=True,
        help='Path to PDF race form guide (e.g. "Hobart Harness 15-03-2026.pdf")'
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
        help='Race number to simulate (e.g. --race 3). Omit to list all races in the PDF.'
    )
    args = parser.parse_args()

    # 1. Parse form
    print(f"\n[race_sim] Parsing form: {args.form!r}")
    try:
        race_info = parse_form(
            form_str=args.form,
            data_dir=args.data,
            track_override=args.track,
            race_no=args.race,
        )
    except Exception as e:
        print(f"[ERROR] Could not parse form: {e}", file=sys.stderr)
        sys.exit(1)

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
