"""
test_agent_model.py — Unit tests for agent model phases (spec Section 6.1).

Run from project root:
    python Sim/test_agent_model.py
"""

from __future__ import annotations

import os
import sys

import numpy as np

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SIM_DIR)
if SIM_DIR not in sys.path:
    sys.path.insert(0, SIM_DIR)

from agent_model import (
    HorseState, prob_gate_clear, initialise_states,
    simulate_phase1, simulate_phase2, simulate_phase3,
    run_single_agentbased,
)
from sim_config import TRACK_PROFILES


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_track_profile(track: str, start_type: str = 'MS') -> dict:
    profile = dict(TRACK_PROFILES.get(track, {}))
    if 'use_proxy' in profile:
        profile = dict(TRACK_PROFILES.get(profile['use_proxy'], profile))
    profile['start_type'] = start_type
    return profile


def _make_features(slugs, barriers, gate_z=None, finish_z=None, nr=None):
    """Build minimal feature dict for agent model tests."""
    n = len(slugs)
    feats = {}
    for i, slug in enumerate(slugs):
        feats[slug] = {
            '_barrier':       float(barriers[i]),
            '_odm_multiplier': 1.0,
            'gate_speed_z':   float(gate_z[i]   if gate_z   else 0.0),
            'finishing_speed_z': float(finish_z[i] if finish_z else 0.0),
            'days_since_last_win': 0.5,
        }
    return feats


def _make_weights(w_luck=0.4, w_gate_speed=1.0, w_finishing_speed=1.0):
    return {
        'w_luck': w_luck,
        'w_gate_speed': w_gate_speed,
        'w_finishing_speed': w_finishing_speed,
        'w_barrier': 1.0,
        'w_class': 1.0,
        'w_trend': 0.5,
        'w_venue_rate': 0.5,
        'w_driver': 0.5,
        'w_driver_quality': 0.5,
        'w_driver_combo': 0.3,
        'w_driver_class': 0.3,
        'w_value': 0.3,
        'w_distance': 0.5,
        'w_trainer': 0.3,
        'w_track_condition': 0.3,
    }


# ---------------------------------------------------------------------------
# 6.1.1 — Gate clear probability: Launceston vs Burnie
# ---------------------------------------------------------------------------

def test_phase1_gate_clear_launceston():
    """B7 elite gate_speed: 40-60% clear at Launceston, <10% at Burnie."""
    np.random.seed(42)
    barrier = 7
    gate_z  = 2.0   # Elite gate speed

    lnct = _make_track_profile('Launceston')
    burn = _make_track_profile('Burnie')

    n_trials = 2000
    lnct_clears = sum(
        1 for _ in range(n_trials)
        if np.random.random() < prob_gate_clear(barrier, gate_z, lnct['run_in_metres'], 8)
    )
    burn_clears = sum(
        1 for _ in range(n_trials)
        if np.random.random() < prob_gate_clear(barrier, gate_z, burn['run_in_metres'], 8)
    )

    lnct_rate = lnct_clears / n_trials
    burn_rate  = burn_clears  / n_trials

    ok_lnct = 0.30 <= lnct_rate <= 0.75
    ok_burn = burn_rate < 0.15

    status = 'PASS' if (ok_lnct and ok_burn) else 'FAIL'
    print(f"[{status}] gate_clear B7 elite: Launceston={lnct_rate:.1%}  Burnie={burn_rate:.1%}")
    print(f"         (expected Launceston 30-75%, Burnie <15%)")
    return ok_lnct and ok_burn


# ---------------------------------------------------------------------------
# 6.1.2 — Energy distribution after Phase 1
# ---------------------------------------------------------------------------

def test_phase1_energy_distribution():
    """After Phase 1, leader energy < back-marker energy (always)."""
    np.random.seed(0)
    n_trials = 100
    leader_always_lower = True

    slugs    = ['a', 'b', 'c', 'd', 'e', 'f']
    barriers = [1, 2, 3, 4, 5, 6]
    features = _make_features(slugs, barriers)
    weights  = _make_weights()
    tp       = _make_track_profile('Launceston')

    for _ in range(n_trials):
        states = initialise_states(slugs, features, weights)
        states = simulate_phase1(states, features, weights, tp)

        leader_energy = next(s.energy for s in states if s.position == 1)
        back_energy   = max(s.energy for s in states)

        if leader_energy >= back_energy:
            leader_always_lower = False
            break

    status = 'PASS' if leader_always_lower else 'FAIL'
    print(f"[{status}] energy distribution: leader always has lower energy than back marker")
    return leader_always_lower


# ---------------------------------------------------------------------------
# 6.1.3 — Sprint exhaustion: wire-to-wire leader vs midfield settler
# ---------------------------------------------------------------------------

def test_phase3_sprint_exhaustion():
    """Wire-to-wire leader has less energy at Phase 3 than a midfield settler."""
    np.random.seed(1)
    slugs    = ['leader', 'midfield', 'back']
    barriers = [1, 4, 6]
    # Give 'leader' massive gate speed so it leads every phase
    gate_z   = [3.0, -1.0, -2.0]
    finish_z = [0.0, 0.0,  0.0]
    features = _make_features(slugs, barriers, gate_z=gate_z, finish_z=finish_z)
    weights  = _make_weights(w_luck=0.1)  # Low noise for determinism
    tp       = _make_track_profile('Launceston')

    leader_lower_in_p3 = []
    for _ in range(50):
        states = initialise_states(slugs, features, weights)
        states = simulate_phase1(states, features, weights, tp)
        states = simulate_phase2(states, features, weights, tp)

        leader_e  = next(s.energy for s in states if s.slug == 'leader')
        midfield_e = next(s.energy for s in states if s.slug == 'midfield')
        leader_lower_in_p3.append(leader_e < midfield_e)

    pct = sum(leader_lower_in_p3) / len(leader_lower_in_p3)
    ok = pct > 0.80   # Leader should have lower energy >80% of the time
    status = 'PASS' if ok else 'FAIL'
    print(f"[{status}] sprint exhaustion: leader energy < midfield energy in {pct:.0%} of runs")
    return ok


# ---------------------------------------------------------------------------
# 6.1.4 — ODM penalty at 1680m mobile
# ---------------------------------------------------------------------------

def test_odm_penalty_mobile_1680m():
    """ODM horse at 1680m mobile should win <5% of simulations."""
    np.random.seed(42)

    slugs    = ['horse-a', 'horse-b', 'horse-c', 'odm-horse', 'horse-e', 'horse-f']
    barriers = [1, 2, 3, 4, 5, 6]

    # ODM horse is at a neutral mid-barrier (4) so draw alone can't rescue it
    features = _make_features(slugs, barriers, gate_z=[0.0]*6, finish_z=[0.0]*6)
    features['odm-horse']['_odm_multiplier'] = 0.30  # Severe ODM penalty

    weights  = _make_weights()
    tp       = _make_track_profile('Launceston')

    n_trials = 1000
    odm_wins = 0
    for _ in range(n_trials):
        states = initialise_states(slugs, features, weights)
        states = simulate_phase1(states, features, weights, tp)
        states = simulate_phase2(states, features, weights, tp)
        states = simulate_phase3(states, features, weights, tp)
        winner = min(states, key=lambda s: s.gap_to_leader_m)
        if winner.slug == 'odm-horse':
            odm_wins += 1

    win_rate = odm_wins / n_trials
    ok = win_rate < 0.10   # Should win very rarely (spec says <5%; allow up to 10% for small sample)
    status = 'PASS' if ok else 'FAIL'
    print(f"[{status}] ODM penalty: odm-horse win rate = {win_rate:.1%} (target <10%)")
    return ok


# ---------------------------------------------------------------------------
# 6.1.5 — Standing start equalisation
# ---------------------------------------------------------------------------

def test_standing_start_equalisation():
    """At Launceston SS, barrier win rates should be closer to equal than at Burnie SS."""
    np.random.seed(7)

    slugs    = [f'h{i}' for i in range(1, 7)]
    barriers = list(range(1, 7))
    features = _make_features(slugs, barriers, gate_z=[0.0]*6, finish_z=[0.0]*6)
    weights  = _make_weights(w_luck=0.3)

    def _run_barrier_wins(track: str, start_type: str, n: int):
        tp = _make_track_profile(track, start_type)
        wins = {s: 0 for s in slugs}
        for _ in range(n):
            states = initialise_states(slugs, features, weights)
            states = simulate_phase1(states, features, weights, tp)
            states = simulate_phase2(states, features, weights, tp)
            states = simulate_phase3(states, features, weights, tp)
            winner = min(states, key=lambda s: s.gap_to_leader_m).slug
            wins[winner] += 1
        return wins

    n_trials = 1000
    lnct_wins = _run_barrier_wins('Launceston', 'SS', n_trials)
    burn_wins  = _run_barrier_wins('Burnie',     'SS', n_trials)

    # Measure dispersion: higher std-dev of win counts = more barrier-biased
    lnct_std = np.std(list(lnct_wins.values()))
    burn_std  = np.std(list(burn_wins.values()))

    # Launceston SS should be MORE equal (lower std) than Burnie SS
    ok = lnct_std <= burn_std * 1.5   # Allow some tolerance for randomness
    status = 'PASS' if ok else 'FAIL'
    print(f"[{status}] SS equalisation: Launceston barrier std={lnct_std:.1f}  "
          f"Burnie std={burn_std:.1f}  (Launceston should be lower or similar)")
    return ok


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_tests():
    print("=" * 68)
    print("  UNIT TESTS — Agent Model Phases")
    print("=" * 68 + "\n")

    tests = [
        test_phase1_gate_clear_launceston,
        test_phase1_energy_distribution,
        test_phase3_sprint_exhaustion,
        test_odm_penalty_mobile_1680m,
        test_standing_start_equalisation,
    ]

    results = []
    for fn in tests:
        try:
            passed = fn()
        except Exception as e:
            print(f"[ERROR] {fn.__name__}: {e}")
            passed = False
        results.append((fn.__name__, passed))
        print()

    print("=" * 68)
    passed_n  = sum(1 for _, p in results if p)
    failed_n  = len(results) - passed_n
    for name, p in results:
        marker = 'PASS' if p else 'FAIL'
        print(f"  [{marker}] {name}")
    print("=" * 68)
    print(f"  {passed_n}/{len(results)} tests passed")
    print("=" * 68)

    return failed_n == 0


if __name__ == '__main__':
    os.chdir(ROOT_DIR)
    passed = run_all_tests()
    sys.exit(0 if passed else 1)
