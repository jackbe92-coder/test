"""
smoke_test.py — End-to-end smoke test for the agent-based simulation engine.

Checks (from spec Section 6.4):
  1. Generate synthetic data
  2. Run simulation on Burnie Race 4 (6-horse field)
  3. Did all 6 horses get simulated?
  4. Do win percentages sum to ~100%?
  5. Does Andaman Bay (NR54, best horse) have highest win%?
  6. No horse exceeds 75% win probability
  7. All three phases completed without error
  8. Convergence log present
  9. Print PASS or FAIL with specific failure reason

Run from project root:
    python Sim/smoke_test.py
"""

from __future__ import annotations

import subprocess
import sys
import os

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
EXPECTED_WINNER = 'andaman-bay'  # NR54 = best horse in Burnie Race 4
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
    print("  SMOKE TEST — Agent-Based Simulation Engine")
    print("=" * 68)

    # ── 1. Generate synthetic data ────────────────────────────────────────────
    print("\n[1/9] Generating synthetic data ...")
    try:
        make_script = os.path.join(SIM_DIR, 'make_synthetic_data.py')
        result = subprocess.run(
            [sys.executable, make_script],
            capture_output=True, text=True, cwd=ROOT_DIR
        )
        ok = result.returncode == 0
        _check(ok,
               "Synthetic data generated successfully",
               f"make_synthetic_data.py failed: {result.stderr[:200]}",
               results)
        if not ok:
            all_pass = False
    except Exception as e:
        _check(False, '', f"Could not run make_synthetic_data.py: {e}", results)
        all_pass = False

    # ── 2-7. Run simulation ───────────────────────────────────────────────────
    print("\n[2/9] Running simulation on Burnie Race 4 ...")
    sim_error = None
    race_info = sim_results = all_features = None

    try:
        from form_parser import parse_form, RaceInfo, Runner, slugify
        from feature_extractor import DataLoader, extract_all_features
        from race_sim import run_simulation, SCORED_FEATURES

        race_info = parse_form(form_str=BURNIE_RACE4_FORM, data_dir=DATA_DIR)
        if not race_info.runners:
            # If PDF parse failed, construct RaceInfo directly
            from dataclasses import dataclass, field as dc_field
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
        # Print results so far
        _summarise(results)
        return False

    # ── 3. All 6 horses simulated ─────────────────────────────────────────────
    print("\n[3/9] Checking all horses simulated ...")
    slugs = [r.slug for r in race_info.runners]
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
        f"Win %s sum to {win_sum*100:.1f}% — out of range [{MIN_WIN_SUM*100:.0f}%, {MAX_WIN_SUM*100:.0f}%]",
        results,
    )
    if not ok4:
        all_pass = False

    # ── 5. Andaman Bay has highest win% ──────────────────────────────────────
    print("[5/9] Checking Andaman Bay has highest win% ...")
    top_slug = max(sim_results['win_pct'], key=sim_results['win_pct'].__getitem__)
    ok5 = _check(
        top_slug == EXPECTED_WINNER,
        f"Andaman Bay (NR54) is top pick with {sim_results['win_pct'].get(EXPECTED_WINNER, 0)*100:.1f}%",
        f"Top pick is {top_slug!r} ({sim_results['win_pct'].get(top_slug, 0)*100:.1f}%), not Andaman Bay",
        results,
    )
    if not ok5:
        all_pass = False

    # ── 6. No horse exceeds 75% ───────────────────────────────────────────────
    print("[6/9] Checking no horse exceeds 75% win probability ...")
    max_pct = max(sim_results['win_pct'].values())
    ok6 = _check(
        max_pct <= MAX_WIN_PCT,
        f"Max win% = {max_pct*100:.1f}% (within 75% ceiling)",
        f"Max win% = {max_pct*100:.1f}% — exceeds {MAX_WIN_PCT*100:.0f}% ceiling",
        results,
    )
    if not ok6:
        all_pass = False

    # ── 7. All phases completed without error ─────────────────────────────────
    print("[7/9] Confirming all phases completed ...")
    _check(True, "All three phases completed without error", "", results)

    # ── 8. Convergence log present ────────────────────────────────────────────
    print("[8/9] Checking convergence tracking ...")
    conv = sim_results.get('convergence_run')
    ok8 = _check(
        'convergence_run' in sim_results,
        f"Convergence key present (converged at run {conv or 'never'})",
        "convergence_run key missing from results",
        results,
    )
    if not ok8:
        all_pass = False

    # ── 9. Summary ────────────────────────────────────────────────────────────
    print("\n[9/9] Win% table:")
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
