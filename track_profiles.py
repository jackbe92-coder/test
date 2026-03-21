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
    PACE_GAP_ON_PACE_STDEV, PACE_GAP_MIDFIELD_STDEV,
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

    Thresholds are RELATIVE to the field's pace-score std-dev each run, so one
    dominant horse cannot collapse the rest of the field to 'back'.

      on_pace  : gap ≤ PACE_GAP_ON_PACE_STDEV  × field_std
      midfield : gap ≤ PACE_GAP_MIDFIELD_STDEV × field_std
      back     : gap >  PACE_GAP_MIDFIELD_STDEV × field_std

    The top horse is always the leader candidate.  If the #2 horse is within
    the on_pace threshold it also competes for the lead via a weighted draw.

    Position labels:
      Standard tracks:  leader | on_pace | midfield | back
      Hobart (sprint lane): leader | garden_seat | midfield_runner | midfield | back

    Returns {slug: position_label}
    """
    if not pace_scores:
        return {}

    profile = _resolve_profile(track)
    has_sprint_lane = profile.get('sprint_lane', False)

    scores_arr = np.array(list(pace_scores.values()), dtype=float)
    leader_score = float(scores_arr.max())
    field_std = float(scores_arr.std()) if len(scores_arr) > 1 else 1.0
    if field_std < 1e-6:
        field_std = 1.0

    on_pace_gap  = PACE_GAP_ON_PACE_STDEV  * field_std
    midfield_gap = PACE_GAP_MIDFIELD_STDEV * field_std

    # Top horse + any horse within on_pace_gap compete for the single lead slot
    sorted_slugs = sorted(pace_scores, key=pace_scores.__getitem__, reverse=True)
    candidates = [s for s in sorted_slugs if leader_score - pace_scores[s] <= on_pace_gap]

    if len(candidates) == 1:
        leader_slug = candidates[0]
    else:
        # Weighted draw: higher pace score = higher probability of leading
        raw = np.array([pace_scores[s] for s in candidates], dtype=float)
        raw -= raw.min()   # shift to ≥ 0
        raw += 1e-6        # avoid zero weights if all equal
        probs = raw / raw.sum()
        leader_slug = candidates[int(np.random.choice(len(candidates), p=probs))]

    positions = {}
    for slug, score in pace_scores.items():
        if slug == leader_slug:
            positions[slug] = 'leader'
            continue
        gap = leader_score - score
        if gap <= on_pace_gap:
            if has_sprint_lane:
                # Garden seat: on-pace runner that can access the sprint lane.
                # ~55% probability per run (stochastic — depends on race dynamics).
                positions[slug] = 'garden_seat' if np.random.random() < 0.55 else 'midfield_runner'
            else:
                positions[slug] = 'on_pace'
        elif gap <= midfield_gap:
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

    profile = _resolve_profile(track)
    cap_back = profile.get('cap_back_score', False)
    field_avg = float(np.mean(list(main_scores.values()))) if cap_back else None

    adjusted: Dict[str, float] = {}
    for slug, score in main_scores.items():
        pos = positions.get(slug, 'midfield')
        # Fix: at tight tracks (e.g. Burnie 607m) class cannot compensate for
        # poor position.  Cap the pre-multiplier score to the field average for
        # any horse that is NOT leader or on_pace.  This prevents a high-NR horse
        # that is midfield/back from outscoring the actual leader post-multiply.
        if cap_back and pos not in ('leader', 'on_pace', 'garden_seat') and field_avg is not None:
            score = min(score, field_avg)
        mult = get_position_multiplier(track, pos)
        adjusted[slug] = score * mult

    return adjusted, positions
