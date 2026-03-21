"""
track_profiles.py — Stochastic pace position assignment + fixed track profile multipliers.

Position multipliers are DATA-DERIVED CONSTANTS (not Monte Carlo weight parameters).
They are applied as a post-processing step after the base score is computed each run.
The pace position itself is stochastic (luck noise on pace scores), but the multiplier
for each position is fixed.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from sim_config import (
    TRACK_PROFILES,
    PACE_GAP_LEADER, PACE_GAP_ON_PACE, PACE_GAP_MIDFIELD,
)


def _resolve_profile(track: str) -> dict:
    """Return the effective profile for a track, following use_proxy if set."""
    profile = TRACK_PROFILES.get(track, {})
    if 'use_proxy' in profile:
        profile = TRACK_PROFILES.get(profile['use_proxy'], profile)
    return profile


def assign_pace_positions(pace_scores: Dict[str, float], track: str) -> Dict[str, str]:
    """Assign pace position label to each runner based on gap from leader.

    Stochastic: pace_scores already include per-run luck noise from the caller.

    Only ONE horse is assigned 'leader' per run.  When multiple horses fall
    within PACE_GAP_LEADER of the field maximum, the leader is chosen
    probabilistically, weighted by each candidate's pace score, so the
    horse with the higher pace score wins the leader slot more often but not
    exclusively.  The remaining candidates are assigned 'on_pace'.

    Position labels:
      Standard tracks:  leader | on_pace | midfield | back
      Hobart (sprint lane): leader | garden_seat | midfield_runner | midfield | back

    Returns {slug: position_label}
    """
    if not pace_scores:
        return {}

    profile = _resolve_profile(track)
    has_sprint_lane = profile.get('sprint_lane', False)
    leader_score = max(pace_scores.values())

    # All horses within PACE_GAP_LEADER of the max compete for the single lead slot
    candidates = [s for s, sc in pace_scores.items() if leader_score - sc <= PACE_GAP_LEADER]

    if len(candidates) == 1:
        leader_slug = candidates[0]
    else:
        # Weighted draw: higher pace score = higher probability of leading
        raw = np.array([pace_scores[s] for s in candidates], dtype=float)
        raw -= raw.min()        # shift to ≥ 0
        raw += 1e-6             # avoid zero weights if all equal
        probs = raw / raw.sum()
        leader_slug = candidates[int(np.random.choice(len(candidates), p=probs))]

    positions = {}
    for slug, score in pace_scores.items():
        if slug == leader_slug:
            positions[slug] = 'leader'
            continue
        gap = leader_score - score
        if gap <= PACE_GAP_ON_PACE:
            if has_sprint_lane:
                # Garden seat: on-pace runner that can access the sprint lane.
                # ~55% probability per run (stochastic — depends on race dynamics).
                positions[slug] = 'garden_seat' if np.random.random() < 0.55 else 'midfield_runner'
            else:
                positions[slug] = 'on_pace'
        elif gap <= PACE_GAP_MIDFIELD:
            positions[slug] = 'midfield'
        else:
            positions[slug] = 'back'

    return positions


def get_position_multiplier(track: str, position: str) -> float:
    """Return the fixed position multiplier for a track/position combination.

    Falls back to 1.0 (no adjustment) if the track or position is not defined.
    """
    profile = _resolve_profile(track)
    multipliers = profile.get('position_multiplier', {})
    return float(multipliers.get(position, 1.0))


def apply_track_profile(
    main_scores: Dict[str, float],
    pace_scores: Dict[str, float],
    track: str,
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Apply fixed track position multipliers to base scores.

    Args:
        main_scores: {slug: float} — base weighted scores
        pace_scores: {slug: float} — pace scores with per-run luck noise
        track: venue name (e.g. 'Hobart', 'Burnie')

    Returns:
        adjusted_scores: {slug: float} — post-multiplier scores
        positions: {slug: str} — pace position labels for this run
    """
    if not TRACK_PROFILES.get(track) and track not in TRACK_PROFILES:
        # Unknown track — return unchanged scores, all midfield
        positions = {slug: 'midfield' for slug in main_scores}
        return dict(main_scores), positions

    positions = assign_pace_positions(pace_scores, track)

    adjusted: Dict[str, float] = {}
    for slug, score in main_scores.items():
        pos = positions.get(slug, 'midfield')
        mult = get_position_multiplier(track, pos)
        adjusted[slug] = score * mult

    return adjusted, positions
