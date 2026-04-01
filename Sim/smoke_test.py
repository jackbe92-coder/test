"""
smoke_test.py — End-to-end smoke tests for the agent-based simulation engine.

Tests (9 checks):
  1. Data availability (stride_results.csv exists with real data)
  2. Simulation runs without error on Burnie Race 4 (6-horse field)
  3. All 6 horses present in results
  4. Win percentages sum to ~100%
  5. Finish order emerges from gap_to_leader_m, NOT from a parallel score sort
  6. Phase output matches harness.au sectional format (sectionals dict with correct keys)
  7. Recency dampening fires on a known artefact pattern (contradicting trend vs recent)
  8. Confidence flags: LOW CONFIDENCE for thin-data horses, MAINLAND — NO DATA for missing
  9. No odds comparison (VALUE/OVERBET) in report output

Run from project root:
    python Sim/smoke_test.py
"""

from __future__ import annotations

import io
import subprocess
import sys
import os
import contextlib

import numpy as np

# ── Ensure we can import from Sim/ ───────────────────────────────────────────
SIM_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SIM_DIR)
if SIM_DIR not in sys.path:
    sys.path.insert(0, SIM_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


DATA_DIR = os.path.join(ROOT_DIR, 'output', 'claude_data')
RUNS = 2000  # Smaller count for smoke test speed
EXPECTED_HORSES = 6
MAX_WIN_PCT = 0.75
MIN_WIN_SUM = 0.98
MAX_WIN_SUM = 1.02

# Burnie Race 4 inline form text
BURNIE_RACE4_FORM = """Burnie Harness — Fri 13 Mar, 2026
R4 — Pace 13:00 | NR 44 to 54 | 2180m
1  Modern Jive   D: Gareth Rattray  T: Some Trainer   NR52
2  Nikita Jo     D: Ryan Backhouse  T: Some Trainer   NR50
3  Andaman Bay   D: Mark Yole       T: Some Trainer   NR54
4  La Pierre     D: Brent Parish    T: Some Trainer   NR47
5  Rock Amour    D: Charlie Castles T: Some Trainer   NR46
6  Away Game     D: Liam Older      T: Some Trainer   NR45
"""


def _check(condition: bool, msg_pass: str, msg_fail: str, results: list) -> bool:
    if condition:
        results.append(('PASS', msg_pass))
        return True
    else:
        results.append(('FAIL', msg_fail))
        return False


def run_smoke_test() -> bool:
    """Execute full smoke test. Returns True if all checks pass."""
    results = []
    all_pass = True

    print("=" * 68)
    print("  SMOKE TEST — Agent-Based Simulation Engine (v2)")
    print("=" * 68)

    # ── 1. Data availability check ─────────────────────────────────────────────
    print("\n[1/9] Checking data availability ...")
    stride_csv = os.path.join(DATA_DIR, 'stride_results.csv')
    ok1 = _check(
        os.path.exists(stride_csv),
        "Data files present (stride_results.csv found)",
        "stride_results.csv missing — cannot run simulation",
        results,
    )
    if not ok1:
        all_pass = False
        _summarise(results)
        return False

    # ── 2-4. Run simulation ──────────────────────────────────────────────────
    print("\n[2/9] Running simulation on Burnie Race 4 ...")
    sim_error = None
    race_info = sim_results = all_features = None

    try:
        from form_parser import parse_form, RaceInfo, Runner, slugify
        from feature_extractor import DataLoader, extract_all_features
        from race_sim import run_simulation, SCORED_FEATURES

        race_info = parse_form(form_str=BURNIE_RACE4_FORM, data_dir=DATA_DIR)
        if not race_info.runners:
            runners = [
                Runner(horse='Modern Jive', slug='modern-jive', barrier=1, driver='Gareth Rattray',  nr=52),
                Runner(horse='Nikita Jo',   slug='nikita-jo',   barrier=2, driver='Ryan Backhouse',  nr=50),
                Runner(horse='Andaman Bay', slug='andaman-bay', barrier=3, driver='Mark Yole',       nr=54),
                Runner(horse='La Pierre',   slug='la-pierre',   barrier=4, driver='Brent Parish',    nr=47),
                Runner(horse='Rock Amour',  slug='rock-amour',  barrier=5, driver='Charlie Castles', nr=46),
                Runner(horse='Away Game',   slug='away-game',   barrier=6, driver='Liam Older',      nr=45),
            ]
            race_info = RaceInfo(
                track='Burnie', date='2026-03-13', race_no=4,
                distance_m=2180, start_type='MS', runners=runners,
            )

        data = DataLoader(DATA_DIR)
        all_features, warnings = extract_all_features(race_info, data)
        np.random.seed(42)
        sim_results = run_simulation(race_info, all_features, n_runs=RUNS)

    except Exception as e:
        sim_error = str(e)
        _check(False, '', f"Simulation error: {sim_error}", results)
        all_pass = False

    if sim_error:
        _summarise(results)
        return False

    _check(True, "Simulation completed without error", "", results)

    # ── 3. All 6 horses simulated ─────────────────────────────────────────────
    print("[3/9] Checking all horses simulated ...")
    n_sim = len(sim_results['win_pct'])
    ok3 = _check(
        n_sim == EXPECTED_HORSES,
        f"All {EXPECTED_HORSES} horses present in results",
        f"Expected {EXPECTED_HORSES} horses, got {n_sim}",
        results,
    )
    if not ok3:
        all_pass = False

    # ── 4. Win percentages sum to ~100% ──────────────────────────────────────
    print("[4/9] Checking win % sums to ~100% ...")
    win_sum = sum(sim_results['win_pct'].values())
    ok4 = _check(
        MIN_WIN_SUM <= win_sum <= MAX_WIN_SUM,
        f"Win %s sum to {win_sum*100:.1f}% (within ±2%)",
        f"Win %s sum to {win_sum*100:.1f}% — out of range",
        results,
    )
    if not ok4:
        all_pass = False

    # ── 5. Finish order emerges from gap_to_leader_m, NOT score sort ──────────
    print("[5/9] Verifying finish order from gap, not score ...")
    # Run a single agent simulation and verify the finish order matches
    # gap_to_leader_m sorting
    from agent_model import run_single_agentbased, PhaseCheckpoint
    from race_sim import zscore_field, draw_weights, _effective_weight_ranges
    from sim_config import TRACK_PROFILES

    slugs = [r.slug for r in race_info.runners]
    z_scores = zscore_field(all_features, slugs, SCORED_FEATURES)
    eff_ranges = _effective_weight_ranges(race_info.track, race_info.start_type)
    tp = TRACK_PROFILES.get(race_info.track, {})

    np.random.seed(123)
    weights = draw_weights(eff_ranges)
    finish_order, pace_order, pace_pos, mid_pos, checkpoints = run_single_agentbased(
        slugs=slugs,
        z_scores=z_scores,
        raw_features=all_features,
        weights=weights,
        track=race_info.track,
        start_type=race_info.start_type,
        track_profile=tp,
    )

    # The finish order must be sorted by gap from the finish checkpoints
    ckpt_fin = {c.slug: c.gap_to_leader_m for c in checkpoints['finish']}
    gap_sorted = sorted(ckpt_fin.keys(), key=lambda s: ckpt_fin[s])

    ok5 = _check(
        finish_order == gap_sorted,
        "Finish order emerges from gap_to_leader_m (not score sort)",
        f"Finish order mismatch: finish_order != gap-sorted order",
        results,
    )
    if not ok5:
        all_pass = False

    # ── 6. Phase output matches harness.au sectional format ───────────────────
    print("[6/9] Checking sectional output matches harness.au format ...")
    sectionals = sim_results.get('sectionals', {})
    has_sectionals = bool(sectionals)
    required_keys = {
        'avg_800m_margin', 'avg_400m_margin', 'avg_finish_margin',
        'avg_q3_time', 'avg_q4_time',
        'avg_gained_800_400', 'avg_gained_400_finish',
    }
    keys_ok = True
    if has_sectionals:
        first_slug = list(sectionals.keys())[0]
        actual_keys = set(sectionals[first_slug].keys())
        keys_ok = required_keys.issubset(actual_keys)
    else:
        keys_ok = False

    ok6 = _check(
        has_sectionals and keys_ok,
        f"Sectionals dict present with all {len(required_keys)} required keys",
        f"Sectionals missing or incomplete keys. Has: {set(sectionals.get(list(sectionals.keys())[0], {}).keys()) if sectionals else set()}",
        results,
    )
    if not ok6:
        all_pass = False

    # Also verify checkpoint data was returned from single run
    has_checkpoints = (
        '800m' in checkpoints and '400m' in checkpoints and 'finish' in checkpoints
        and len(checkpoints['800m']) == EXPECTED_HORSES
        and len(checkpoints['400m']) == EXPECTED_HORSES
        and len(checkpoints['finish']) == EXPECTED_HORSES
        and all(isinstance(c, PhaseCheckpoint) for c in checkpoints['finish'])
    )
    if not has_checkpoints:
        results.append(('FAIL', f"Checkpoint data incomplete: {list(checkpoints.keys())}"))
        all_pass = False

    # ── 7. Recency dampening fires on contradicting trend ─────────────────────
    print("[7/9] Testing recency dampening on contradicting trend ...")
    from feature_extractor import compute_recency_dampening
    import pandas as pd

    # Create a fake runner and stride_results where recent 3 runs contradict seasonal trend
    fake_runner = Runner(horse='Test Horse', slug='test-horse', barrier=1)

    # Seasonal trend = positive (improving), but last 3 runs show worsening (higher mile rate)
    fake_sr = pd.DataFrame({
        'Slug': ['test-horse'] * 5,
        '_slug': ['test-horse'] * 5,
        'Horse': ['Test Horse'] * 5,
        'Date': pd.date_range('2026-01-01', periods=5, freq='14D'),
        'Mile_Rate': [120.0, 119.0, 118.0, 119.5, 121.0],  # newest=121 (worst), oldest=120
        'Place': [3, 2, 1, 4, 5],
    })
    # Sort newest first (as _recent_runs would return)
    fake_sr = fake_sr.sort_values('Date', ascending=False).reset_index(drop=True)

    positive_trend = 0.5  # seasonal says improving
    dampened, damp_w = compute_recency_dampening(fake_runner, fake_sr, positive_trend)

    ok7 = _check(
        abs(dampened) < abs(positive_trend) and len(damp_w) > 0,
        f"Recency dampening fired: {positive_trend:.3f} → {dampened:.3f} (warnings: {len(damp_w)})",
        f"Recency dampening did NOT fire. Input={positive_trend}, output={dampened}, warnings={damp_w}",
        results,
    )
    if not ok7:
        all_pass = False

    # ── 8. Confidence flags for thin data / mainland horses ───────────────────
    print("[8/9] Checking confidence flags ...")
    from report_generator import _confidence_label

    # Test LOW CONFIDENCE: horse with < 5 Tasmanian starts
    thin_feats = {'_data_confidence': 0.3, '_mainland_visitor': 0.0, '_tas_starts': 3}
    thin_label = _confidence_label('test-thin', {'test-thin': thin_feats})

    # Test MAINLAND — NO DATA: horse flagged as visitor
    visitor_feats = {'_data_confidence': 0.05, '_mainland_visitor': 1.0, '_tas_starts': 0}
    visitor_label = _confidence_label('test-visitor', {'test-visitor': visitor_feats})

    # Test OK: horse with plenty of data
    ok_feats = {'_data_confidence': 0.9, '_mainland_visitor': 0.0, '_tas_starts': 30}
    ok_label = _confidence_label('test-ok', {'test-ok': ok_feats})

    ok8 = _check(
        thin_label == "LOW CONFIDENCE"
        and visitor_label == "MAINLAND — NO DATA"
        and ok_label == "",
        f"Confidence flags correct: thin='{thin_label}', visitor='{visitor_label}', ok='{ok_label}'",
        f"Confidence flags wrong: thin='{thin_label}', visitor='{visitor_label}', ok='{ok_label}'",
        results,
    )
    if not ok8:
        all_pass = False

    # ── 9. No odds comparison (VALUE/OVERBET) in report output ────────────────
    print("[9/9] Checking no VALUE/OVERBET in report output ...")
    from report_generator import generate_report

    # Capture stdout from generate_report
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        generate_report(sim_results, race_info, all_features, warnings)
    report_text = captured.getvalue()

    has_value = 'VALUE' in report_text
    has_overbet = 'OVERBET' in report_text
    has_oppose = 'OPPOSE:' in report_text

    ok9 = _check(
        not has_value and not has_overbet and not has_oppose,
        "No VALUE/OVERBET/OPPOSE flags in report output",
        f"Report still contains odds comparison: VALUE={has_value} OVERBET={has_overbet} OPPOSE={has_oppose}",
        results,
    )
    if not ok9:
        all_pass = False

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n[Summary] Win% table:")
    print(f"  {'Horse':<20} {'Win%':>7} {'Place%':>8}")
    print("  " + "-" * 37)
    for r in sorted(race_info.runners, key=lambda x: -sim_results['win_pct'].get(x.slug, 0)):
        wp = sim_results['win_pct'].get(r.slug, 0)
        pp = sim_results['place_pct'].get(r.slug, 0)
        print(f"  {r.horse:<20} {wp*100:>6.1f}%  {pp*100:>7.1f}%")

    _summarise(results)
    return all_pass


def _summarise(results: list):
    print("\n" + "=" * 68)
    passed = sum(1 for s, _ in results if s == 'PASS')
    failed = sum(1 for s, _ in results if s == 'FAIL')
    for status, msg in results:
        marker = "PASS" if status == 'PASS' else "FAIL"
        print(f"  [{marker}] {msg}")
    print("=" * 68)
    if failed == 0:
        print(f"  RESULT: PASS  ({passed}/{passed + failed} checks)")
    else:
        print(f"  RESULT: FAIL  ({passed}/{passed + failed} checks passed, {failed} failed)")
    print("=" * 68)


if __name__ == '__main__':
    import os
    # Run from project root
    os.chdir(ROOT_DIR)
    passed = run_smoke_test()
    sys.exit(0 if passed else 1)
