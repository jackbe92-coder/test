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

    positions = {}
    for slug, score in pace_scores.items():
        gap = leader_score - score
        if gap <= PACE_GAP_LEADER:
            label = 'leader'
        elif gap <= PACE_GAP_ON_PACE:
            if has_sprint_lane:
                # Garden seat: on-pace runner that can access the sprint lane.
                # ~55% probability per run (stochastic — depends on race dynamics).
                label = 'garden_seat' if np.random.random() < 0.55 else 'midfield_runner'
            else:
                label = 'on_pace'
        elif gap <= PACE_GAP_MIDFIELD:
            label = 'midfield'
        else:
            label = 'back'
        positions[slug] = label

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
