"""
agent_model.py — Agent-based segment simulation for Tasmanian harness racing.

Replaces the weighted-score Monte Carlo in run_single() with a physics-driven
three-phase simulation where finish order EMERGES from position state rather
than from a pre-computed ranking.

Phases:
  Phase 1 (Start → 800m): Gate speed + barrier → early position + energy init
  Phase 2 (800m → 400m):  Energy depletion, width penalty, interference, tactics
  Phase 3 (400m → Finish): Energy-weighted sprint finish

Entry point: run_single_agentbased()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from sim_config import (
    BASE_PACE_MS,
    PHASE1_LEADER_ENERGY_COST, PHASE1_ON_PACE_ENERGY_COST,
    PHASE1_MIDFIELD_ENERGY_COST, PHASE1_BACK_ENERGY_COST,
    PHASE2_LEADER_ENERGY_COST, PHASE2_ON_PACE_ENERGY_COST,
    PHASE2_MIDFIELD_ENERGY_COST, PHASE2_BACK_ENERGY_COST,
    GATE_NOISE_STD,
    PHASE1_GAP_PER_POSITION_M,
    WIDTH_PENALTY_M_PER_LANE,
    INTERFERENCE_PROB, INTERFERENCE_ENERGY_COST, INTERFERENCE_GAP_PENALTY_M,
    TACTICAL_ENERGY_THRESHOLD, TACTICAL_MOVE_BASE_PROB, TACTICAL_GAP_GAIN_M,
)


# ---------------------------------------------------------------------------
# HorseState
# ---------------------------------------------------------------------------

@dataclass
class HorseState:
    slug: str
    position: int           # rank from front (1 = leader)
    lane: int               # 1 = rail, higher = wider
    gap_to_leader_m: float  # metres behind leader (leader = 0.0)
    energy: float           # 1.0 = full tank; depletes through race
    speed_m_per_s: float    # current speed
    checked: bool           # interference flag this segment
    finished: bool
    phases_led: int = 0     # count of phases spent as leader (for deceleration)


@dataclass
class PhaseCheckpoint:
    """Per-horse data at a phase boundary, matching harness.au sectional format."""
    slug: str
    position: int
    gap_to_leader_m: float
    section_time_s: float       # estimated quarter time for preceding segment
    metres_gained_vs_leader: float  # change in gap since previous checkpoint (negative = fell back)


# ---------------------------------------------------------------------------
# Gate clear probability (Section 3.2 of spec)
# ---------------------------------------------------------------------------

def prob_gate_clear(
    barrier: int,
    gate_speed_z: float,
    run_in_metres: float,
    n_runners: int,
) -> float:
    """Probability a horse clears from its barrier to rail before the first turn.

    Higher run_in_metres = more time = higher probability.
    Higher gate_speed_z  = faster jumper = higher probability.
    Lower barrier        = less distance to clear = higher probability.

    Returns a probability in [0.02, 0.95].
    """
    if barrier <= 1:
        return 0.0  # Already on rail — no clear needed

    lanes_to_clear = barrier - 1

    # Normalise run-in: 200m = reference (full value); quadratic so short tracks drop sharply.
    run_in_norm = min(1.0, (run_in_metres / 200.0) ** 2)

    # Gate speed multiplier: z=+2 → 30% boost, z=-2 → 30% reduction
    speed_advantage = 1.0 + gate_speed_z * 0.15

    # Exponential decay with lanes to clear: each additional lane halves the base prob.
    # Calibrated so B7 elite gate at Launceston (180m) ≈ 40-60%, Burnie (80m) ≈ 5-10%.
    base = 1.2 * run_in_norm * speed_advantage * (0.82 ** lanes_to_clear)
    return float(min(0.95, max(0.02, base)))


# ---------------------------------------------------------------------------
# Phase utilities
# ---------------------------------------------------------------------------

def _recalc_positions(states: List[HorseState]) -> None:
    """Re-rank all states by gap_to_leader_m ascending (leader = rank 1)."""
    for rank, s in enumerate(sorted(states, key=lambda x: x.gap_to_leader_m), 1):
        s.position = rank


def _position_bucket(position: int, n: int) -> str:
    """Map numeric rank to pace bucket label."""
    n_on_pace = max(1, n // 4)
    if position == 1:
        return 'leader'
    elif position <= 1 + n_on_pace:
        return 'on_pace'
    elif position <= max(3, n * 2 // 3):
        return 'midfield'
    else:
        return 'back'


# ---------------------------------------------------------------------------
# Phase 1: Start → 800m
# ---------------------------------------------------------------------------

def initialise_states(
    slugs: List[str],
    features: Dict[str, Dict],
    weights: Dict[str, float],
) -> List[HorseState]:
    """Create HorseState objects at the barrier, one per horse."""
    return [
        HorseState(
            slug=slug,
            position=int(features.get(slug, {}).get('_barrier', i + 1)),
            lane=int(features.get(slug, {}).get('_barrier', i + 1)),
            gap_to_leader_m=0.0,
            energy=1.0,
            speed_m_per_s=BASE_PACE_MS,
            checked=False,
            finished=False,
        )
        for i, slug in enumerate(slugs)
    ]


def simulate_phase1(
    states: List[HorseState],
    features: Dict[str, Dict],
    weights: Dict[str, float],
    track_profile: dict,
) -> Tuple[List[HorseState], List[PhaseCheckpoint]]:
    """Phase 1: Start → 800m.

    Establishes 800m positions and initial energy levels.
    Returns (states, checkpoints_at_800m).
    """
    n = len(states)
    if n == 0:
        return states

    start_type = track_profile.get('start_type', 'MS')
    run_in = float(track_profile.get('run_in_metres', 100))
    ss_eq = float(track_profile.get('standing_start_equalisation', 0.4))
    gate_clear_threshold = int(track_profile.get('gate_clear_threshold', 4))

    # Energy costs (track profile overrides sim_config defaults)
    e_leader  = float(track_profile.get('leader_energy_cost',   PHASE1_LEADER_ENERGY_COST))
    e_on_pace = float(track_profile.get('on_pace_cost',         PHASE1_ON_PACE_ENERGY_COST))
    e_mid     = float(track_profile.get('midfield_cost',        PHASE1_MIDFIELD_ENERGY_COST))
    e_back    = float(track_profile.get('back_cost',            PHASE1_BACK_ENERGY_COST))

    luck_noise = float(weights.get('w_luck', 0.5))
    gate_noise = GATE_NOISE_STD * luck_noise

    # --- Compute position scores ---
    scores: Dict[str, float] = {}
    for s in states:
        f = features.get(s.slug, {})
        gate_z   = float(f.get('gate_speed_z', 0.0))
        barrier  = int(f.get('_barrier', s.lane))

        # Stochastic gate draw
        gate_actual = gate_z + float(np.random.normal(0.0, gate_noise))

        # Barrier advantage (0..1 scale; inside = 1.0)
        barrier_adv = (n + 1 - barrier) / (n + 1)

        # Overall class/quality: better horses tend to find positions earlier
        class_z = float(f.get('class_relativity_z', 0.0))
        quality = class_z * float(weights.get('w_class', 0.5)) * 0.4

        if start_type == 'MS':
            # Mobile: draw matters heavily; gate speed is decisive
            score = gate_actual * 1.5 + barrier_adv * 2.0 + quality
        else:
            # Standing start: draw equalised based on track profile
            score = gate_actual * 1.5 + barrier_adv * 2.0 * ss_eq + quality

        # ODM penalty: out-of-draw horse starts disadvantaged regardless of barrier
        odm_mult = float(f.get('_odm_multiplier', 1.0))
        if odm_mult < 1.0:
            score *= odm_mult

        scores[s.slug] = score

    # --- Gate clear attempts (mobile starts only, outside gate_clear_threshold) ---
    if start_type == 'MS':
        for s in states:
            barrier = int(features.get(s.slug, {}).get('_barrier', s.lane))
            if barrier <= 1:
                continue
            gate_z = float(features.get(s.slug, {}).get('gate_speed_z', 0.0))
            p_clear = prob_gate_clear(barrier, gate_z, run_in, n)

            # Only attempt clear if horse is fast enough and track allows it
            if gate_z > -0.5 and barrier <= gate_clear_threshold + 3:
                if np.random.random() < p_clear:
                    new_lane = max(1, barrier // 2)
                    lanes_cleared = barrier - new_lane
                    s.lane = new_lane
                    # Scaled bonus: more run-in = bigger; more lanes to clear = smaller
                    bonus = (run_in / 150.0) * (1.0 / max(1, lanes_cleared)) * 0.8
                    scores[s.slug] += min(1.2, bonus)

    # --- Sort by score → establish 800m rank ---
    sorted_slugs = sorted(scores, key=scores.__getitem__, reverse=True)
    slug_to_state = {s.slug: s for s in states}

    n_on_pace = max(1, n // 4)
    n_midfield = max(1, n * 2 // 3 - n_on_pace - 1)

    for rank, slug in enumerate(sorted_slugs, 1):
        s = slug_to_state[slug]
        s.position = rank
        s.gap_to_leader_m = float((rank - 1) * PHASE1_GAP_PER_POSITION_M)
        s.speed_m_per_s = BASE_PACE_MS

        # Assign energy based on position bucket
        if rank == 1:
            s.energy = max(0.0, 1.0 - e_leader)
            s.phases_led += 1
        elif rank <= 1 + n_on_pace:
            s.energy = max(0.0, 1.0 - e_on_pace)
        elif rank <= 1 + n_on_pace + n_midfield:
            s.energy = max(0.0, 1.0 - e_mid)
        else:
            s.energy = max(0.0, 1.0 - e_back)

    # Build 800m checkpoints — no previous checkpoint so metres_gained = 0
    # Estimate section time: distance_to_800m / speed (crudely from energy)
    circuit_m = float(track_profile.get('circuit_length_m', 1000))
    distance_m = float(track_profile.get('race_distance_m', 2000))
    dist_to_800 = max(200, distance_m - 800)
    checkpoints_800m = []
    for s in states:
        sect_time = dist_to_800 / max(1.0, s.speed_m_per_s * s.energy)
        checkpoints_800m.append(PhaseCheckpoint(
            slug=s.slug,
            position=s.position,
            gap_to_leader_m=s.gap_to_leader_m,
            section_time_s=sect_time,
            metres_gained_vs_leader=0.0,
        ))

    return states, checkpoints_800m


# ---------------------------------------------------------------------------
# Phase 2: 800m → 400m (Q3)
# ---------------------------------------------------------------------------

def simulate_phase2(
    states: List[HorseState],
    features: Dict[str, Dict],
    weights: Dict[str, float],
    track_profile: dict,
) -> Tuple[List[HorseState], List[PhaseCheckpoint]]:
    """Phase 2: 800m → 400m (Q3).

    Key physics:
    - Energy depletion continues (shorter segment → lower costs than Phase 1)
    - Width penalty: horses wide of rail travel extra metres per lap
    - Random interference events (checked, bumped, etc.)
    - Tactical moves: high-energy finishers can improve position
    """
    n = len(states)
    if n == 0:
        return states, []

    # Snapshot gaps at 800m (before Phase 2 modifies them)
    gaps_at_800m = {s.slug: s.gap_to_leader_m for s in states}

    # Energy costs for this segment (scaled down from phase 1)
    e_leader  = float(track_profile.get('leader_energy_cost',   PHASE1_LEADER_ENERGY_COST))  * 0.5
    e_on_pace = float(track_profile.get('on_pace_cost',         PHASE1_ON_PACE_ENERGY_COST)) * 0.5
    e_mid     = float(track_profile.get('midfield_cost',        PHASE1_MIDFIELD_ENERGY_COST))* 0.5
    e_back    = float(track_profile.get('back_cost',            PHASE1_BACK_ENERGY_COST))    * 0.5

    n_on_pace = max(1, n // 4)

    for s in states:
        s.checked = False
        f = features.get(s.slug, {})

        # --- Energy depletion ---
        if s.position == 1:
            s.energy = max(0.0, s.energy - e_leader)
        elif s.position <= 1 + n_on_pace:
            s.energy = max(0.0, s.energy - e_on_pace)
        elif s.position <= max(3, n * 2 // 3):
            s.energy = max(0.0, s.energy - e_mid)
        else:
            s.energy = max(0.0, s.energy - e_back)

        # --- Width penalty: horses wide of rail pay extra metres ---
        if s.lane > 2:
            extra_m = float(s.lane - 2) * WIDTH_PENALTY_M_PER_LANE
            s.gap_to_leader_m += extra_m

        # --- Stochastic interference event ---
        if np.random.random() < INTERFERENCE_PROB:
            s.checked = True
            s.energy = max(0.0, s.energy - INTERFERENCE_ENERGY_COST)
            s.gap_to_leader_m += INTERFERENCE_GAP_PENALTY_M

    # --- Tactical moves: high-energy horses with good finishing speed advance ---
    for s in sorted(states, key=lambda x: x.position):
        if s.position <= 2:
            continue
        f = features.get(s.slug, {})
        finish_z = float(f.get('finishing_speed_z', 0.0))

        if s.energy > TACTICAL_ENERGY_THRESHOLD and finish_z > 0.3:
            p_move = TACTICAL_MOVE_BASE_PROB * s.energy * min(1.0, finish_z / 1.5)
            if np.random.random() < p_move:
                s.gap_to_leader_m = max(0.1, s.gap_to_leader_m - TACTICAL_GAP_GAIN_M)
                s.energy = max(0.0, s.energy - 0.08)

    _recalc_positions(states)

    for s in states:
        if s.position == 1:
            s.phases_led += 1

    # Build 400m (Q3) checkpoints
    checkpoints_400m = []
    for s in states:
        gap_800 = gaps_at_800m[s.slug]
        metres_gained = gap_800 - s.gap_to_leader_m  # positive = closed gap
        q3_time = 400.0 / max(1.0, s.speed_m_per_s * s.energy)
        checkpoints_400m.append(PhaseCheckpoint(
            slug=s.slug,
            position=s.position,
            gap_to_leader_m=s.gap_to_leader_m,
            section_time_s=q3_time,
            metres_gained_vs_leader=metres_gained,
        ))

    return states, checkpoints_400m


# ---------------------------------------------------------------------------
# Phase 3: 400m → Finish (Q4)
# ---------------------------------------------------------------------------

def simulate_phase3(
    states: List[HorseState],
    features: Dict[str, Dict],
    weights: Dict[str, float],
    track_profile: dict,
) -> Tuple[List[HorseState], List[PhaseCheckpoint]]:
    """Phase 3: 400m → Finish (Q4 home straight).

    Key physics:
    - available_speed = finishing_speed_z * energy_remaining
    - metres_gained = (available_speed - leader_speed) * time_in_straight
    - final_gap = gap_at_400m - metres_gained
    - Sprint lane bonus at Hobart (rail horse, correct position only)

    Burnie (95m straight):  very little time → position at turn ≈ final order
    Launceston (220m):      meaningful closing — back-markers can charge
    Hobart (200m + lane):   sprint lane gives rail horse a significant bonus
    """
    straight_m = float(track_profile.get('straight_m', 200))
    has_sprint_lane = bool(track_profile.get('sprint_lane', False))
    sprint_lane_bonus = float(track_profile.get('sprint_lane_bonus', 0.0))

    # Time available in the straight (seconds)
    time_in_straight = straight_m / BASE_PACE_MS

    # Compute available sprint speed for each horse
    available_speeds: Dict[str, float] = {}
    for s in states:
        f = features.get(s.slug, {})
        finish_z = float(f.get('finishing_speed_z', 0.0))

        # Class quality also boosts sprint: higher-class horses are simply faster
        class_z = float(f.get('class_relativity_z', 0.0))
        effective_finish_z = (
            finish_z * float(weights.get('w_finishing_speed', 1.0)) * 0.7 +
            class_z  * float(weights.get('w_class', 0.5))           * 0.3
        )

        # Convert z-score to speed differential:
        # Horses that led through both phases suffer deceleration — a fully
        # exhausted leader runs at 85% of base pace, not 100%.
        # Non-leaders retain the original formula (base factor = 1.0).
        if s.phases_led >= 2:
            energy_factor = 0.85 + (0.15 * max(0.0, s.energy))
        else:
            energy_factor = 1.0
        avail = BASE_PACE_MS * (energy_factor + effective_finish_z * 0.09 * max(0.0, s.energy))

        # ODM penalty also reduces sprint effectiveness: horse went wide, is
        # physically spent and out of its comfort zone in the straight.
        odm_mult = float(f.get('_odm_multiplier', 1.0))
        if odm_mult < 1.0:
            avail *= odm_mult

        # Sprint lane bonus (Hobart only) — rail horse in leading position
        if has_sprint_lane and s.lane == 1 and s.position <= 2:
            avail *= (1.0 + sprint_lane_bonus)

        available_speeds[s.slug] = avail

    # Snapshot gaps at 400m (before sprint modifies them)
    gaps_at_400m = {s.slug: s.gap_to_leader_m for s in states}

    # Identify current leader (smallest gap)
    leader = min(states, key=lambda x: x.gap_to_leader_m)
    leader_speed = available_speeds[leader.slug]

    # Update gaps based on sprint speed relative to leader
    for s in states:
        if s.slug == leader.slug:
            continue
        metres_gained = (available_speeds[s.slug] - leader_speed) * time_in_straight
        s.gap_to_leader_m -= metres_gained

    # Mark all as finished; re-rank
    for s in states:
        s.finished = True

    _recalc_positions(states)

    # Build finish (Q4) checkpoints
    checkpoints_finish = []
    for s in states:
        gap_400 = gaps_at_400m[s.slug]
        metres_gained = gap_400 - s.gap_to_leader_m  # positive = closed gap
        q4_time = straight_m / max(1.0, available_speeds.get(s.slug, BASE_PACE_MS))
        checkpoints_finish.append(PhaseCheckpoint(
            slug=s.slug,
            position=s.position,
            gap_to_leader_m=s.gap_to_leader_m,
            section_time_s=q4_time,
            metres_gained_vs_leader=metres_gained,
        ))

    return states, checkpoints_finish


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_single_agentbased(
    slugs: List[str],
    z_scores: Dict[str, Dict[str, float]],
    raw_features: Dict[str, Dict[str, float]],
    weights: Dict[str, float],
    track: str = '',
    start_type: str = 'MS',
    track_profile: Optional[dict] = None,
) -> Tuple[List[str], List[str], Dict[str, str], Dict[str, str], Dict[str, List[PhaseCheckpoint]]]:
    """Run one agent-based simulation of a harness race.

    Args:
        slugs:        Horse slugs in barrier order
        z_scores:     {slug: {feature: z_value}} — normalised across field
        raw_features: {slug: {feature: raw_value}} — includes _barrier, _odm_multiplier
        weights:      Sampled weight vector (from draw_weights)
        track:        Track name (e.g. 'Burnie')
        start_type:   'MS' or 'SS'
        track_profile: Full track profile dict from TRACK_PROFILES

    Returns:
        finish_order:   slugs from 1st to last (sorted by gap_to_leader_m)
        pace_order:     slugs by 800m position (front to back)
        pace_positions: {slug: 'leader'|'on_pace'|'midfield'|'back'} at 800m
        mid_positions:  {slug: 'leader'|'on_pace'|'midfield'|'back'} at 400m
        checkpoints:    {'800m': [...], '400m': [...], 'finish': [...]} PhaseCheckpoint lists
    """
    if track_profile is None:
        track_profile = {}

    n = len(slugs)

    # --- Merge raw features and z-scores into a single lookup dict ---
    features: Dict[str, Dict] = {}
    for slug in slugs:
        combined = dict(raw_features.get(slug, {}))
        for feat_name, z_val in z_scores.get(slug, {}).items():
            combined[feat_name + '_z'] = z_val
        features[slug] = combined

    # --- Add start_type and race_distance to track profile for phase functions ---
    tp = dict(track_profile)
    tp['start_type'] = start_type

    # --- Phase 1: Start → 800m ---
    states = initialise_states(slugs, features, weights)
    states, ckpt_800m = simulate_phase1(states, features, weights, tp)

    # Capture pace order and positions at 800m
    sorted_at_800 = sorted(states, key=lambda s: s.gap_to_leader_m)
    pace_order = [s.slug for s in sorted_at_800]
    pace_positions: Dict[str, str] = {}

    for s in sorted_at_800:
        bucket = _position_bucket(s.position, n)
        if bucket == 'on_pace' and track_profile.get('sprint_lane', False):
            bucket = 'garden_seat' if np.random.random() < 0.55 else 'midfield_runner'
        pace_positions[s.slug] = bucket

    # --- Phase 2: 800m → 400m (Q3) ---
    states, ckpt_400m = simulate_phase2(states, features, weights, tp)

    # Capture positions at 400m
    mid_positions: Dict[str, str] = {}
    for s in states:
        mid_positions[s.slug] = _position_bucket(s.position, n)

    # --- Phase 3: 400m → Finish (Q4) ---
    states, ckpt_finish = simulate_phase3(states, features, weights, tp)

    finish_order = [s.slug for s in sorted(states, key=lambda s: s.gap_to_leader_m)]
    checkpoints = {'800m': ckpt_800m, '400m': ckpt_400m, 'finish': ckpt_finish}

    return finish_order, pace_order, pace_positions, mid_positions, checkpoints
