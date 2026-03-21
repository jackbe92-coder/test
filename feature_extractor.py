"""
feature_extractor.py — Load CSV data and compute feature vectors for each runner.

Feature vector keys (all normalised higher-is-better before z-scoring):
  gate_speed         – avg position at 800m (higher = closer to leader)
  finishing_speed    – avg last-400m time, negated (higher = faster)
  mile_rate_trend    – slope of Mile_Rate last N runs, negated (higher = improving)
  venue_win_rate     – win% at this specific track
  barrier_score      – inside draw advantage score
  driver_venue_rate  – driver win% at this track
  class_relativity   – horse NR vs field average NR
  freshness          – categorical score based on days since last run
  last_800m_pos      – avg Last_800m_Pos (higher = gaining ground in run home)
  width_penalty      – avg lane width (negated: higher = ran closer to rail = better)
  stewards_flag      – net excuses minus concerns
  injury_return      – 0 or -1 (stand-down return penalty)

Warnings are accumulated and returned alongside features for the report.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from form_parser import Runner, RaceInfo, slugify, parse_pct, parse_nr_from_class
from sim_config import (
    EXCUSE_KEYWORDS, CONCERN_KEYWORDS,
    EXCUSE_CREDIT, CONCERN_PENALTY, STEWARDS_FLAG_CAP, STEWARDS_LOOKBACK_NOTES,
    FRESHNESS_OPTIMAL_MIN, FRESHNESS_OPTIMAL_MAX,
    FRESHNESS_SCORE_OPTIMAL, FRESHNESS_SHORT_CUTOFF, FRESHNESS_LONG_CUTOFF,
    FRESHNESS_SCORE_SHORT, FRESHNESS_SCORE_OK_LOW, FRESHNESS_SCORE_OK_HIGH,
    FRESHNESS_SCORE_LONG,
    INJURY_RETURN_PENALTY,
    DRIVER_PENALTY_LOOKBACK_DAYS, DRIVER_SUSPENSION_LOOKBACK_DAYS, SUSPENSION_KEYWORDS,
    DRIVER_SEASON_MIN_STARTS, DRIVER_COMBO_MIN_RUNS,
    CONFIDENCE_MIN_N, CONFIDENCE_FULL_N,
    CONSISTENCY_LOOKBACK, SP_PERFORMANCE_LOOKBACK,
    DISTANCE_MATCH_WINDOW_M, DISTANCE_MIN_RUNS, START_TYPE_MIN_RUNS,
    TRACK_CONDITION_MIN_RUNS, TRAINER_FORM_LOOKBACK, FIELD_STRENGTH_BASELINE,
    CLASS_TRAJECTORY_LOOKBACK, WINNER_QUALITY_LOOKBACK, WIN_DROUGHT_WARN_DAYS,
    DISTANCE_OPTIMAL_BIN_M, MOBILE_BARRIER_MIN_RUNS, CAREER_CLASS_MIN_RUNS,
    DRIVER_JUNIOR_YEARS, DRIVER_VETERAN_YEARS,
    MIN_RUNS_RELIABLE, GATE_SPEED_LOOKBACK, FINISHING_SPEED_LOOKBACK,
    TREND_LOOKBACK, WIDTH_LOOKBACK, LAST_800_LOOKBACK,
    VENUE_WIN_PCT_COLUMNS, VENUE_WINS_COLUMNS, VENUE_PLACES_COLUMNS, VENUE_STARTS_COLUMNS,
)


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

class DataLoader:
    """Loads all relevant CSVs once and exposes them as DataFrames."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._load_all()

    def _load(self, filename: str) -> pd.DataFrame:
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            print(f"  [WARN] Data file not found: {filename}")
            return pd.DataFrame()
        return pd.read_csv(path, low_memory=False)

    def _load_all(self):
        self.stride_results = self._load('stride_results.csv')
        self.stride_profiles = self._load('stride_profiles.csv')
        self.drivers_profile = self._load('drivers_profile.csv')
        self.driver_results = self._load('driver_results_recent.csv')
        self.stewards_notes = self._load('stewards_notes.csv')
        self.stewards_stand_downs = self._load('stewards_stand_downs.csv')
        self.stewards_penalties = self._load('stewards_penalties.csv')
        self.dividends = self._load('dividends.csv')
        self.trainer_results = self._load('trainer_results_recent.csv')

        # Normalise date columns
        for df_name in ('stride_results', 'stewards_notes', 'stewards_stand_downs',
                        'stewards_penalties', 'driver_results', 'dividends',
                        'trainer_results'):
            df = getattr(self, df_name)
            if not df.empty and 'Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

        # Enrich stride_results with Start_Type and Track_Condition from dividends
        self._enrich_stride_results()

        # Slug-normalise horse name lookup columns
        if not self.stride_results.empty and 'Slug' in self.stride_results.columns:
            self.stride_results['_slug'] = self.stride_results['Slug'].str.lower().str.strip()
        if not self.stride_profiles.empty and 'Slug' in self.stride_profiles.columns:
            self.stride_profiles['_slug'] = self.stride_profiles['Slug'].str.lower().str.strip()

        # driver_results_recent: 'Trainer' column is actually the driver name (API quirk)
        if not self.driver_results.empty and 'Trainer' in self.driver_results.columns:
            self.driver_results['_driver_name'] = (
                self.driver_results['Trainer'].str.lower().str.strip()
            )

    def _enrich_stride_results(self):
        """Fill missing Start_Type / Track_Condition in stride_results from dividends.

        Only adds columns that are absent or fills NaN cells — never overwrites
        existing non-null values.  This preserves data already present in the CSV
        (e.g. stride_results generated by make_synthetic_data.py).
        """
        if self.stride_results.empty or self.dividends.empty:
            return
        div_has_join = all(c in self.dividends.columns for c in ('Date', 'Track', 'Race_No'))
        if not div_has_join:
            return

        enrich_cols = [c for c in ('Start_Type', 'Track_Condition')
                       if c in self.dividends.columns]
        if not enrich_cols:
            return

        # Which columns are actually missing (not in stride_results at all)?
        missing = [c for c in enrich_cols if c not in self.stride_results.columns]
        # Which columns exist but have some NaN values?
        has_nan = [c for c in enrich_cols
                   if c in self.stride_results.columns
                   and self.stride_results[c].isna().any()]

        cols_to_fill = list(dict.fromkeys(missing + has_nan))  # preserve order, dedupe
        if not cols_to_fill:
            return  # Nothing to do — all columns already fully populated

        div_sub = (
            self.dividends[['Date', 'Track', 'Race_No'] + cols_to_fill]
            .drop_duplicates(subset=['Date', 'Track', 'Race_No'])
        )

        try:
            merged = self.stride_results.merge(
                div_sub, on=['Date', 'Track', 'Race_No'], how='left',
                suffixes=('', '_div'),
            )
            for col in cols_to_fill:
                div_col = col + '_div'
                if div_col in merged.columns:
                    if col in self.stride_results.columns:
                        # Coalesce: keep existing value, fill NaN from dividends
                        merged[col] = merged[col].fillna(merged[div_col])
                    else:
                        merged[col] = merged[div_col]
                    merged = merged.drop(columns=[div_col])
            self.stride_results = merged
        except Exception:
            pass  # Graceful degradation if join fails


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COUNTRY_SUFFIXES = ('nz', 'gb', 'ire', 'ir', 'us', 'fr', 'de')


def normalise_horse_name(name: str) -> str:
    """Normalise country suffix to uppercase, e.g. 'Imperial Laz Nz' → 'Imperial Laz NZ'."""
    name = name.strip()
    parts = name.split()
    if parts and parts[-1].lower() in _COUNTRY_SUFFIXES:
        parts[-1] = parts[-1].upper()
        return ' '.join(parts)
    return name


def _horse_lookup_keys(runner: Runner):
    """Return (slug, slug_nz, name_norm_lower) for all lookup variants.

    Handles three common mismatches between PDF form names and stride CSV storage:
      1. Form 'My Way Nz' vs CSV slug 'my-way-nz'   — slug match works directly
      2. Form 'Always Aurora' vs CSV slug 'always-aurora' — slug match works
      3. Form 'Always Aurora' vs CSV slug 'always-aurora-nz' — need -nz fallback
    """
    slug = runner.slug.lower().strip()
    # Slug with -nz suffix (for horses stored with suffix that the PDF omitted)
    slug_nz = slug if slug.endswith('-nz') else slug + '-nz'
    name_norm = normalise_horse_name(runner.horse).lower().strip()
    # Also try with NZ suffix appended if name has no suffix
    name_parts = name_norm.split()
    if name_parts and name_parts[-1] not in _COUNTRY_SUFFIXES:
        name_norm_nz = name_norm + ' nz'
    else:
        name_norm_nz = name_norm
    return slug, slug_nz, name_norm, name_norm_nz


def _recent_runs(runner: Runner, df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Return the n most recent rows from stride_results for this horse.

    Tries four lookup strategies in order:
      1. Exact slug match
      2. Slug with -nz suffix (PDF may omit country suffix)
      3. Normalised horse name (uppercase suffix)
      4. Normalised name + ' nz' suffix
    """
    if df.empty:
        return pd.DataFrame()
    slug, slug_nz, name_norm, name_norm_nz = _horse_lookup_keys(runner)
    horse_col = df['Horse'].str.lower().str.strip() if 'Horse' in df.columns else None

    # 1. Exact slug
    mask = df['_slug'] == slug
    # 2. Slug + -nz
    if not mask.any() and slug_nz != slug:
        mask = df['_slug'] == slug_nz
    # 3. Normalised name
    if not mask.any() and horse_col is not None:
        mask = horse_col == name_norm
    # 4. Normalised name + nz
    if not mask.any() and horse_col is not None and name_norm_nz != name_norm:
        mask = horse_col == name_norm_nz

    result = df[mask].copy()
    if 'Date' in result.columns:
        result = result.sort_values('Date', ascending=False)
    return result.head(n)


def _profile_row(runner: Runner, profiles: pd.DataFrame) -> Optional[pd.Series]:
    """Return stride_profiles row for this horse.

    Tries four lookup strategies matching _recent_runs.
    """
    if profiles.empty:
        return None
    slug, slug_nz, name_norm, name_norm_nz = _horse_lookup_keys(runner)
    horse_col = profiles['Horse'].str.lower().str.strip() if 'Horse' in profiles.columns else None

    # 1. Exact slug
    rows = profiles[profiles['_slug'] == slug]
    # 2. Slug + -nz
    if rows.empty and slug_nz != slug:
        rows = profiles[profiles['_slug'] == slug_nz]
    # 3. Normalised name
    if rows.empty and horse_col is not None:
        rows = profiles[horse_col == name_norm]
    # 4. Normalised name + nz
    if rows.empty and horse_col is not None and name_norm_nz != name_norm:
        rows = profiles[horse_col == name_norm_nz]

    return rows.iloc[0] if not rows.empty else None


def _safe_float(val, default: float = np.nan) -> float:
    try:
        f = float(val)
        return f if np.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _parse_sp(val) -> Optional[float]:
    """Parse SP string like '$4.20' → 4.2. Returns None if unparseable or ≤ 0."""
    if val is None:
        return None
    s = str(val).replace('$', '').replace(',', '').strip()
    if s in ('', 'nan', 'SCR', 'N/A', 'None'):
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Individual feature functions
# ---------------------------------------------------------------------------

def compute_gate_speed(runner: Runner, stride_results: pd.DataFrame) -> Tuple[float, List[str]]:
    """Average position at 800m mark. Higher = closer to leader (better)."""
    warnings = []
    runs = _recent_runs(runner, stride_results, GATE_SPEED_LOOKBACK)
    if runs.empty:
        warnings.append(f"{runner.horse}: not found in stride_results — no gate speed data")
        return 0.0, warnings

    if len(runs) < MIN_RUNS_RELIABLE:
        warnings.append(f"{runner.horse}: only {len(runs)} run(s) in stride_results (< {MIN_RUNS_RELIABLE})")

    # Prefer 800_Margin_m (spec column), fallback to Last_800m_Pos
    col = '800_Margin_m' if '800_Margin_m' in runs.columns else 'Last_800m_Pos'
    vals = pd.to_numeric(runs[col], errors='coerce').dropna()
    if vals.empty and col == '800_Margin_m' and 'Last_800m_Pos' in runs.columns:
        vals = pd.to_numeric(runs['Last_800m_Pos'], errors='coerce').dropna()
    if vals.empty:
        return 0.0, warnings
    return float(vals.mean()), warnings


def compute_finishing_speed(runner: Runner, stride_results: pd.DataFrame) -> Tuple[float, List[str]]:
    """Avg last-400m time, negated so higher = faster finisher."""
    warnings = []
    runs = _recent_runs(runner, stride_results, FINISHING_SPEED_LOOKBACK)
    if runs.empty:
        return 0.0, warnings
    vals = pd.to_numeric(runs.get('400_0_Time', pd.Series(dtype=float)), errors='coerce').dropna()
    if vals.empty:
        return 0.0, warnings
    # Negate: lower time = faster = better = higher score
    return -float(vals.mean()), warnings


def compute_mile_rate_trend(runner: Runner, stride_results: pd.DataFrame) -> Tuple[float, List[str]]:
    """Slope of Mile_Rate over last N runs (oldest→newest). Negated so improving form = higher."""
    warnings = []
    runs = _recent_runs(runner, stride_results, TREND_LOOKBACK)
    if runs.empty or 'Mile_Rate' not in runs.columns:
        return 0.0, warnings
    # runs is sorted newest first; reverse for chronological slope
    vals = pd.to_numeric(runs['Mile_Rate'], errors='coerce').dropna()
    if len(vals) < 2:
        return 0.0, warnings
    vals_chron = vals.iloc[::-1].reset_index(drop=True)
    x = np.arange(len(vals_chron))
    slope = float(np.polyfit(x, vals_chron.values, 1)[0])
    # Negative slope = improving (lower time = faster). Negate so higher = better.
    return -slope, warnings


def compute_barrier_score(runner: Runner, track: str, start_type: str) -> Tuple[float, List[str]]:
    """Inside draw advantage. Higher = better gate position.

    Barrier 1 = maximum score. Normalised to 0-1 range assuming max 12 barriers.
    """
    warnings = []
    b = runner.barrier
    if b <= 0:
        return 0.5, warnings  # unknown — neutral
    # Score: inverse of barrier number. Cap at 12.
    max_b = 12
    score = (max_b - min(b - 1, max_b - 1)) / max_b   # barrier 1 → 1.0, barrier 12 → 0.083
    return score, warnings


def compute_driver_venue_rate(
    runner: Runner,
    driver_results: pd.DataFrame,
    track: str,
    stride_results: pd.DataFrame | None = None,
) -> Tuple[float, List[str]]:
    """Driver win% at this specific track.

    Priority:
      1. driver_results_recent.csv (recent, small)
      2. stride_results.csv 'Driver' column (larger historical dataset)
    """
    warnings = []
    if not runner.driver:
        return 0.0, warnings

    driver_lower = runner.driver.lower().strip()

    # --- 1. Try driver_results_recent ---
    if not driver_results.empty and '_driver_name' in driver_results.columns:
        driver_df = driver_results[driver_results['_driver_name'] == driver_lower]
        if not driver_df.empty:
            venue_df = driver_df[driver_df['Track'].str.lower().str.strip() == track.lower()]
            src = venue_df if not venue_df.empty else driver_df
            total = len(src)
            wins = (pd.to_numeric(src['Place'], errors='coerce') == 1).sum()
            return float(wins / total) if total > 0 else 0.0, warnings

    # --- 2. Fall back to stride_results — check Driver then Trainer columns ---
    if stride_results is not None and not stride_results.empty:
        sr = stride_results
        for col in ('Driver', 'Trainer'):
            if col not in sr.columns:
                continue
            matched = sr[sr[col].str.lower().str.strip() == driver_lower]
            if not matched.empty:
                venue_df = matched[matched['Track'].str.lower().str.strip() == track.lower()]
                src = venue_df if not venue_df.empty else matched
                total = len(src)
                wins = (pd.to_numeric(src['Place'], errors='coerce') == 1).sum()
                return float(wins / total) if total > 0 else 0.0, warnings

    warnings.append(f"Driver {runner.driver!r}: no results found in any dataset")
    return 0.0, warnings


def compute_class_relativity(runner: Runner, field_avg_nr: float) -> Tuple[float, List[str]]:
    """Horse NR relative to field average. Positive = above field level."""
    warnings = []
    nr = runner.nr
    if nr == 0.0:
        return 0.0, warnings
    return nr - field_avg_nr, warnings


def compute_freshness(runner: Runner, stride_results: pd.DataFrame, race_date: str) -> Tuple[float, List[str]]:
    """Score based on days since last run. Optimal 14–28 days."""
    warnings = []
    runs = _recent_runs(runner, stride_results, 1)
    if runs.empty or 'Date' not in runs.columns:
        return 0.0, warnings

    last_date = runs.iloc[0]['Date']
    if pd.isna(last_date):
        return 0.0, warnings

    try:
        ref = datetime.strptime(race_date, '%Y-%m-%d') if race_date else datetime.today()
    except ValueError:
        ref = datetime.today()

    days = (ref - pd.Timestamp(last_date)).days

    if days < FRESHNESS_SHORT_CUTOFF:
        return FRESHNESS_SCORE_SHORT, warnings
    elif days < FRESHNESS_OPTIMAL_MIN:
        return FRESHNESS_SCORE_OK_LOW, warnings
    elif days <= FRESHNESS_OPTIMAL_MAX:
        return FRESHNESS_SCORE_OPTIMAL, warnings
    elif days <= FRESHNESS_LONG_CUTOFF:
        return FRESHNESS_SCORE_OK_HIGH, warnings
    else:
        return FRESHNESS_SCORE_LONG, warnings


def compute_last_800m_pos(runner: Runner, stride_results: pd.DataFrame) -> Tuple[float, List[str]]:
    """Avg Last_800m_Pos over last N runs. Higher = gaining ground in the run home."""
    warnings = []
    runs = _recent_runs(runner, stride_results, LAST_800_LOOKBACK)
    if runs.empty or 'Last_800m_Pos' not in runs.columns:
        return 0.0, warnings
    vals = pd.to_numeric(runs['Last_800m_Pos'], errors='coerce').dropna()
    if vals.empty:
        return 0.0, warnings
    return float(vals.mean()), warnings


def compute_width_penalty(runner: Runner, stride_results: pd.DataFrame) -> Tuple[float, List[str]]:
    """Avg lane width. Negated so lower width (closer to rail) = higher score."""
    warnings = []
    runs = _recent_runs(runner, stride_results, WIDTH_LOOKBACK)
    if runs.empty:
        return 0.0, warnings

    w800 = pd.to_numeric(runs.get('800_Width', pd.Series(dtype=float)), errors='coerce')
    w400 = pd.to_numeric(runs.get('400_Width', pd.Series(dtype=float)), errors='coerce')

    combined = (w800.fillna(0) + w400.fillna(0)) / 2.0
    vals = combined[combined > 0]
    if vals.empty:
        return 0.0, warnings
    # Negate: lower width = better
    return -float(vals.mean()), warnings


def _classify_stewards_note(note_text: str) -> int:
    """Classify a single note as EXCUSE (+1), CONCERN (-1), or NEUTRAL (0).

    Uses full-phrase matching to avoid 'galloped' ambiguity:
    'galloped when' = excuse; 'galloped out' = concern.
    """
    note = note_text.lower()
    # Check concern first so 'galloped out' beats generic 'galloped when' check
    for kw in CONCERN_KEYWORDS:
        if kw in note:
            return -1
    for kw in EXCUSE_KEYWORDS:
        if kw in note:
            return +1
    return 0


def compute_stewards_flag(
    runner: Runner,
    stewards_notes: pd.DataFrame,
    race_date: str,
    n_notes: int = None,
) -> Tuple[float, List[str]]:
    """Net sentiment score from last N stewards notes (#23).

    +1 per excuse note (checked, held up, etc.)
    -1 per concern note (fractious, galloped out, etc.)
    Summed over last STEWARDS_LOOKBACK_NOTES runs, capped at ±STEWARDS_FLAG_CAP.
    """
    if n_notes is None:
        n_notes = STEWARDS_LOOKBACK_NOTES
    warnings = []
    if stewards_notes.empty:
        return 0.0, warnings

    slug, slug_nz, name_norm, name_norm_nz = _horse_lookup_keys(runner)
    if 'Horse_Slug' in stewards_notes.columns:
        slug_col = stewards_notes['Horse_Slug'].str.lower().str.strip()
        mask = slug_col == slug
        if not mask.any() and slug_nz != slug:
            mask = slug_col == slug_nz
    else:
        horse_col = stewards_notes['Horse'].str.lower().str.strip()
        mask = horse_col == name_norm
        if not mask.any() and name_norm_nz != name_norm:
            mask = horse_col == name_norm_nz

    notes_df = stewards_notes[mask].copy()
    if 'Date' in notes_df.columns:
        notes_df = notes_df.sort_values('Date', ascending=False)
    notes_df = notes_df.head(n_notes)

    score = 0.0
    for _, row in notes_df.iterrows():
        score += _classify_stewards_note(str(row.get('Note', '')))

    score = max(-STEWARDS_FLAG_CAP, min(STEWARDS_FLAG_CAP, score))
    return score, warnings


def compute_injury_return(
    runner: Runner,
    stewards_stand_downs: pd.DataFrame,
    race_date: str,
) -> Tuple[float, List[str]]:
    """Return -1 (penalty) if horse is returning from a vet/stewards stand-down."""
    warnings = []
    if stewards_stand_downs.empty:
        return 0.0, warnings

    slug, slug_nz, name_norm, name_norm_nz = _horse_lookup_keys(runner)
    if 'Horse_Slug' in stewards_stand_downs.columns:
        slug_col = stewards_stand_downs['Horse_Slug'].str.lower().str.strip()
        mask = slug_col == slug
        if not mask.any() and slug_nz != slug:
            mask = slug_col == slug_nz
    else:
        horse_col = stewards_stand_downs['Horse'].str.lower().str.strip()
        mask = horse_col == name_norm
        if not mask.any() and name_norm_nz != name_norm:
            mask = horse_col == name_norm_nz

    rows = stewards_stand_downs[mask]
    if rows.empty:
        return 0.0, warnings

    try:
        ref = datetime.strptime(race_date, '%Y-%m-%d') if race_date else datetime.today()
    except ValueError:
        ref = datetime.today()

    # Check if any stand-down is recent (within last 180 days)
    if 'Date' in rows.columns:
        recent = rows[
            (rows['Date'].notna()) &
            ((ref - rows['Date']).dt.days.between(0, 180))
        ]
        if not recent.empty:
            warnings.append(f"{runner.horse}: returning from vet/stewards stand-down — risk flag")
            return INJURY_RETURN_PENALTY, warnings

    return 0.0, warnings


# ---------------------------------------------------------------------------
# Confidence weighting helper (small sample discount)
# ---------------------------------------------------------------------------

def _confidence_weight(n: float) -> float:
    """Scale 0→0 at n=0, 0.5 at n=CONFIDENCE_MIN_N, 1.0 at n≥CONFIDENCE_FULL_N."""
    n = max(0.0, float(n))
    if n <= 0:
        return 0.0
    if n >= CONFIDENCE_FULL_N:
        return 1.0
    if n >= CONFIDENCE_MIN_N:
        return 0.5 + 0.5 * (n - CONFIDENCE_MIN_N) / (CONFIDENCE_FULL_N - CONFIDENCE_MIN_N)
    return 0.5 * n / CONFIDENCE_MIN_N


# ---------------------------------------------------------------------------
# Features 11 & 12 — Venue win rate and place rate (confidence-weighted)
# ---------------------------------------------------------------------------

def compute_venue_win_rate(
    runner: Runner,
    stride_profiles: pd.DataFrame,
    track: str,
) -> Tuple[float, List[str]]:
    """#11 — Win% at this venue, confidence-weighted by number of starts."""
    warnings = []
    profile = _profile_row(runner, stride_profiles)
    if profile is None:
        warnings.append(f"{runner.horse}: not found in stride_profiles — possible mainland visitor")
        return 0.0, warnings

    starts_col = VENUE_STARTS_COLUMNS.get(track)
    win_pct_col = VENUE_WIN_PCT_COLUMNS.get(track)

    if not win_pct_col or win_pct_col not in profile.index:
        warnings.append(f"{runner.horse}: no venue win rate column for track={track!r}")
        return _safe_float(parse_pct(profile.get('Career_Win_Pct', 0)), 0.0), warnings

    starts = _safe_float(profile.get(starts_col, 0), 0.0) if starts_col else 0.0
    conf = _confidence_weight(starts)

    if starts == 0:
        warnings.append(f"{runner.horse}: has never raced at {track}")

    venue_rate = _safe_float(parse_pct(profile.get(win_pct_col, 0)), 0.0)
    career_rate = _safe_float(parse_pct(profile.get('Career_Win_Pct', 0)), 0.0)

    # Blend: confidence-weighted venue rate + (1-conf) career rate as prior
    blended = conf * venue_rate + (1.0 - conf) * career_rate
    return blended, warnings


def compute_venue_place_rate(
    runner: Runner,
    stride_profiles: pd.DataFrame,
    track: str,
) -> Tuple[float, List[str]]:
    """#12 — (Wins + Places) / Starts at venue, confidence-weighted."""
    warnings = []
    profile = _profile_row(runner, stride_profiles)
    if profile is None:
        return 0.0, warnings

    starts_col  = VENUE_STARTS_COLUMNS.get(track)
    wins_col    = VENUE_WINS_COLUMNS.get(track)
    places_col  = VENUE_PLACES_COLUMNS.get(track)

    if not starts_col or not wins_col or not places_col:
        career = _safe_float(parse_pct(profile.get('Career_Place_Pct', 0)), 0.0)
        return career, warnings

    starts = _safe_float(profile.get(starts_col, 0), 0.0)
    wins   = _safe_float(profile.get(wins_col,   0), 0.0)
    places = _safe_float(profile.get(places_col, 0), 0.0)

    conf = _confidence_weight(starts)
    venue_place_rate = (wins + places) / starts if starts > 0 else 0.0
    career_place_rate = _safe_float(parse_pct(profile.get('Career_Place_Pct', 0)), 0.0)

    blended = conf * venue_place_rate + (1.0 - conf) * career_place_rate
    return blended, warnings


# ---------------------------------------------------------------------------
# Features 14 & 15 — Positional margins at 800m and 400m
# ---------------------------------------------------------------------------

def compute_position_800m_margin(
    runner: Runner, stride_results: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#14 — Avg metres behind leader at 800m, last 5 runs.
    Negated so higher score = closer to leader (better).
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, GATE_SPEED_LOOKBACK)
    if runs.empty or '800_Margin_m' not in runs.columns:
        return 0.0, warnings
    vals = pd.to_numeric(runs['800_Margin_m'], errors='coerce').dropna()
    if vals.empty:
        return 0.0, warnings
    return -float(vals.mean()), warnings   # negate: less behind = higher = better


def compute_position_400m_margin(
    runner: Runner, stride_results: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#15 — Avg metres behind leader at 400m, last 5 runs.
    Negated so higher score = closer to leader at the bell.
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, GATE_SPEED_LOOKBACK)
    if runs.empty or '400_Margin_m' not in runs.columns:
        return 0.0, warnings
    vals = pd.to_numeric(runs['400_Margin_m'], errors='coerce').dropna()
    if vals.empty:
        return 0.0, warnings
    return -float(vals.mean()), warnings   # negate: less behind = higher = better


# ---------------------------------------------------------------------------
# Features 27 & 28 — Driver career and season win rate
# ---------------------------------------------------------------------------

def _lookup_driver_profile(driver_name: str, drivers_profile: pd.DataFrame) -> Optional[pd.Series]:
    """Find a driver row in drivers_profile by name (case-insensitive)."""
    if drivers_profile.empty or 'Name' not in drivers_profile.columns:
        return None
    driver_lower = driver_name.lower().strip()
    rows = drivers_profile[drivers_profile['Name'].str.lower().str.strip() == driver_lower]
    return rows.iloc[0] if not rows.empty else None


def compute_driver_career_winrate(
    runner: Runner, drivers_profile: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#27 — Driver lifetime win rate from drivers_profile."""
    warnings = []
    if not runner.driver or drivers_profile.empty:
        return 0.0, warnings

    row = _lookup_driver_profile(runner.driver, drivers_profile)
    if row is None:
        return 0.0, warnings

    starts = _safe_float(row.get('driver.lifetime_summary.starts', 0), 0.0)
    wins   = _safe_float(row.get('driver.lifetime_summary.wins',   0), 0.0)
    if starts <= 0:
        return 0.0, warnings
    return float(wins / starts), warnings


def compute_driver_season_winrate(
    runner: Runner, drivers_profile: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#28 — Driver current season win rate.
    Used if season starts >= DRIVER_SEASON_MIN_STARTS; falls back to career rate.
    """
    warnings = []
    if not runner.driver or drivers_profile.empty:
        return 0.0, warnings

    row = _lookup_driver_profile(runner.driver, drivers_profile)
    if row is None:
        return 0.0, warnings

    season_starts = _safe_float(row.get('driver.current_season_summary.starts', 0), 0.0)
    season_wins   = _safe_float(row.get('driver.current_season_summary.wins',   0), 0.0)

    if season_starts >= DRIVER_SEASON_MIN_STARTS:
        return float(season_wins / season_starts), warnings

    # Not enough season starts — use career rate
    career_starts = _safe_float(row.get('driver.lifetime_summary.starts', 0), 0.0)
    career_wins   = _safe_float(row.get('driver.lifetime_summary.wins',   0), 0.0)
    if career_starts <= 0:
        return 0.0, warnings
    return float(career_wins / career_starts), warnings


# ---------------------------------------------------------------------------
# Feature 31 — Driver-horse combination rate
# ---------------------------------------------------------------------------

def compute_driver_horse_combo(
    runner: Runner, stride_results: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#31 — Win/place rate when this driver-horse pair have combined.
    Confidence-weighted; returns 0.0 if fewer than DRIVER_COMBO_MIN_RUNS.
    """
    warnings = []
    if not runner.driver or stride_results.empty:
        return 0.0, warnings
    if 'Driver' not in stride_results.columns:
        return 0.0, warnings

    driver_lower = runner.driver.lower().strip()
    horse_runs = _recent_runs(runner, stride_results, 50)  # broad lookback for combo
    if horse_runs.empty:
        return 0.0, warnings

    combo = horse_runs[horse_runs['Driver'].str.lower().str.strip() == driver_lower]
    n = len(combo)
    if n < DRIVER_COMBO_MIN_RUNS:
        return 0.0, warnings

    wins = (pd.to_numeric(combo['Place'], errors='coerce') == 1).sum()
    places = (pd.to_numeric(combo['Place'], errors='coerce') <= 3).sum()
    rate = float((wins + places) / n / 3)  # normalised: place-in-3 rate
    conf = _confidence_weight(n)
    return rate * conf, warnings


# ---------------------------------------------------------------------------
# Feature 32 — Driver suspension check (boolean score)
# ---------------------------------------------------------------------------

def compute_driver_suspended(
    runner: Runner,
    stewards_penalties: pd.DataFrame,
    race_date: str,
) -> Tuple[float, List[str]]:
    """#32 — Returns -1.0 if driver has an active suspension, 0.0 otherwise."""
    warnings = []
    if stewards_penalties.empty or not runner.driver:
        return 0.0, warnings

    driver_lower = runner.driver.lower().strip()
    if 'Person' not in stewards_penalties.columns:
        return 0.0, warnings

    try:
        ref = datetime.strptime(race_date, '%Y-%m-%d') if race_date else datetime.today()
    except ValueError:
        ref = datetime.today()
    cutoff = ref - timedelta(days=DRIVER_SUSPENSION_LOOKBACK_DAYS)

    relevant = stewards_penalties[
        stewards_penalties['Date'].notna() &
        (stewards_penalties['Date'] >= cutoff) &
        (stewards_penalties['Date'] <= ref)
    ]

    for _, row in relevant.iterrows():
        person = str(row.get('Person', '')).lower()
        detail = str(row.get('Detail', '')).upper()
        type_  = str(row.get('Type',   '')).upper()
        if driver_lower in person and any(kw in detail or kw in type_ for kw in SUSPENSION_KEYWORDS):
            warnings.append(
                f"Driver {runner.driver!r}: active suspension — substitute driver likely"
            )
            return -1.0, warnings

    return 0.0, warnings


# ---------------------------------------------------------------------------
# Feature 16 — Consistency Score
# ---------------------------------------------------------------------------

def compute_consistency_score(
    runner: Runner, stride_results: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#16 — Stdev of finish positions over last N runs.
    Higher value = more inconsistent = wider outcome distribution.
    Stored raw (not z-scored) — used in run_single as a per-horse noise multiplier.
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, CONSISTENCY_LOOKBACK)
    if runs.empty or 'Place' not in runs.columns:
        return 0.0, warnings
    vals = pd.to_numeric(runs['Place'], errors='coerce').dropna()
    if len(vals) < 2:
        return 0.0, warnings
    return float(vals.std()), warnings


# ---------------------------------------------------------------------------
# Feature 17 — SP vs Performance
# ---------------------------------------------------------------------------

def compute_sp_vs_performance(
    runner: Runner, stride_results: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#17 — Avg (actual finish percentile − SP implied probability) last N runs.
    Positive = horse consistently outperforms its market price (underrated).
    Negative = horse underperforms its market price (overrated).
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, SP_PERFORMANCE_LOOKBACK)
    if runs.empty:
        return 0.0, warnings

    scores = []
    for _, row in runs.iterrows():
        sp = _parse_sp(row.get('SP'))
        place = _safe_float(row.get('Place'), np.nan)
        starters = _safe_float(row.get('Starters'), np.nan)
        if sp is None or np.isnan(place) or np.isnan(starters) or starters <= 0:
            continue
        implied_prob = 1.0 / sp
        # Actual finish percentile: 1st = 1.0, last = 1/N
        actual_pct = (starters - place + 1) / starters
        scores.append(actual_pct - implied_prob)

    if not scores:
        return 0.0, warnings
    return float(np.mean(scores)), warnings


# ---------------------------------------------------------------------------
# Feature 18 — Distance Suitability
# ---------------------------------------------------------------------------

def compute_distance_suitability(
    runner: Runner, stride_results: pd.DataFrame, race_distance_m: int
) -> Tuple[float, List[str]]:
    """#18 — Win/place rate in runs within ±DISTANCE_MATCH_WINDOW_M of today's distance.
    Confidence-weighted blend with overall place rate.
    """
    warnings = []
    if not race_distance_m or 'Distance_m' not in stride_results.columns:
        return 0.0, warnings

    runs = _recent_runs(runner, stride_results, 50)
    if runs.empty:
        return 0.0, warnings

    d = float(race_distance_m)
    dist_num = pd.to_numeric(runs['Distance_m'], errors='coerce')
    dist_mask = (dist_num >= d - DISTANCE_MATCH_WINDOW_M) & (dist_num <= d + DISTANCE_MATCH_WINDOW_M)
    matched = runs[dist_mask]
    n = len(matched)

    # Overall place rate as prior
    overall_n = len(runs)
    overall_rate = (
        float((pd.to_numeric(runs['Place'], errors='coerce') <= 3).sum() / overall_n)
        if overall_n > 0 else 0.0
    )

    if n < DISTANCE_MIN_RUNS:
        return overall_rate * 0.5, warnings  # weak prior — no distance data

    wins_places = (pd.to_numeric(matched['Place'], errors='coerce') <= 3).sum()
    rate = float(wins_places / n)
    conf = _confidence_weight(n)
    return conf * rate + (1.0 - conf) * overall_rate, warnings


# ---------------------------------------------------------------------------
# Feature 19 — Start Type Rate
# ---------------------------------------------------------------------------

def compute_start_type_rate(
    runner: Runner, stride_results: pd.DataFrame, start_type: str
) -> Tuple[float, List[str]]:
    """#19 — Win/place rate in same start type (MS/SS), from enriched stride_results.
    Confidence-weighted against overall rate.
    """
    warnings = []
    if not start_type or 'Start_Type' not in stride_results.columns:
        return 0.0, warnings

    runs = _recent_runs(runner, stride_results, 50)
    if runs.empty:
        return 0.0, warnings

    st_mask = runs['Start_Type'].astype(str).str.upper().str.strip() == start_type.upper().strip()
    matched = runs[st_mask]
    n = len(matched)

    overall_n = len(runs)
    overall_rate = (
        float((pd.to_numeric(runs['Place'], errors='coerce') <= 3).sum() / overall_n)
        if overall_n > 0 else 0.0
    )

    if n < START_TYPE_MIN_RUNS:
        return overall_rate * 0.7, warnings

    wins_places = (pd.to_numeric(matched['Place'], errors='coerce') <= 3).sum()
    rate = float(wins_places / n)
    conf = _confidence_weight(n)
    return conf * rate + (1.0 - conf) * overall_rate, warnings


# ---------------------------------------------------------------------------
# Feature 26 — Trainer Stable Form
# ---------------------------------------------------------------------------

def compute_trainer_form(
    runner: Runner,
    trainer_results: pd.DataFrame,
    stride_results: pd.DataFrame,
) -> Tuple[float, List[str]]:
    """#26 — Trainer stable win rate over last TRAINER_FORM_LOOKBACK runs.
    >30% = stable firing; <10% = cold spell.
    """
    warnings = []
    trainer_name = runner.trainer if hasattr(runner, 'trainer') and runner.trainer else ''
    if not trainer_name:
        # Fallback: driver might be the trainer
        trainer_name = runner.driver or ''
    if not trainer_name:
        return 0.0, warnings

    trainer_lower = trainer_name.lower().strip()

    # --- 1. Try trainer_results_recent ---
    if not trainer_results.empty and 'Trainer' in trainer_results.columns:
        matched = trainer_results[
            trainer_results['Trainer'].str.lower().str.strip() == trainer_lower
        ].copy()
        if 'Date' in matched.columns:
            matched['Date'] = pd.to_datetime(matched['Date'], errors='coerce')
            matched = matched.sort_values('Date', ascending=False)
        matched = matched.head(TRAINER_FORM_LOOKBACK)
        n = len(matched)
        if n >= 3:
            wins = (pd.to_numeric(matched['Place'], errors='coerce') == 1).sum()
            return float(wins / n), warnings

    # --- 2. Fall back to stride_results Trainer column ---
    if not stride_results.empty and 'Trainer' in stride_results.columns:
        recent = stride_results.sort_values('Date', ascending=False).head(200)
        matched = recent[recent['Trainer'].str.lower().str.strip() == trainer_lower]
        matched = matched.head(TRAINER_FORM_LOOKBACK)
        n = len(matched)
        if n >= 3:
            wins = (pd.to_numeric(matched['Place'], errors='coerce') == 1).sum()
            return float(wins / n), warnings

    return 0.0, warnings


# ---------------------------------------------------------------------------
# Feature 33 — Track Condition Rate
# ---------------------------------------------------------------------------

def compute_track_condition_rate(
    runner: Runner, stride_results: pd.DataFrame, track_condition: str
) -> Tuple[float, List[str]]:
    """#33 — Win/place rate under today's track condition (Good/Slow/Heavy).
    Returns 0.0 if track_condition is unknown or too few runs.
    """
    warnings = []
    if not track_condition or 'Track_Condition' not in stride_results.columns:
        return 0.0, warnings

    runs = _recent_runs(runner, stride_results, 50)
    if runs.empty or 'Track_Condition' not in runs.columns:
        return 0.0, warnings

    tc_mask = (
        runs['Track_Condition'].astype(str).str.lower().str.strip()
        == track_condition.lower().strip()
    )
    matched = runs[tc_mask]
    n = len(matched)

    if n < TRACK_CONDITION_MIN_RUNS:
        return 0.0, warnings

    wins_places = (pd.to_numeric(matched['Place'], errors='coerce') <= 3).sum()
    rate = float(wins_places / n)
    conf = _confidence_weight(n)

    overall_n = len(runs)
    overall_rate = (
        float((pd.to_numeric(runs['Place'], errors='coerce') <= 3).sum() / overall_n)
        if overall_n > 0 else 0.0
    )
    return conf * rate + (1.0 - conf) * overall_rate, warnings


# ---------------------------------------------------------------------------
# Feature 34 — Field Strength Index
# ---------------------------------------------------------------------------

def compute_field_strength(
    field: List[Runner], stride_profiles: pd.DataFrame
) -> float:
    """#34 — Average career win rate of all runners in today's field.
    Returns a scalar (same for all horses). Used to scale class weight.
    """
    rates = []
    for r in field:
        profile = _profile_row(r, stride_profiles)
        if profile is not None:
            rate = _safe_float(parse_pct(profile.get('Career_Win_Pct', 0)), 0.0)
            rates.append(rate)
    return float(np.mean(rates)) if rates else FIELD_STRENGTH_BASELINE


# ---------------------------------------------------------------------------
# Lower-priority feature helpers
# ---------------------------------------------------------------------------

def _parse_class_nr(class_str) -> float:
    """Extract the lower-bound NR from a class string.

    Examples:
      'NR 50 to 52.'  → 50.0
      'NR up to 49.'  → 49.0
      'MAIDEN'        → 20.0
      "NMT 2 LTW's"   → 30.0
    """
    if class_str is None or (isinstance(class_str, float) and np.isnan(class_str)):
        return np.nan
    s = str(class_str).upper().strip()
    if 'MAIDEN' in s:
        return 20.0
    if 'NMT' in s or 'LTW' in s:
        return 30.0
    m = re.search(r'NR\s+UP\s+TO\s+(\d+)', s)
    if m:
        return float(m.group(1))
    m = re.search(r'NR\s+(\d+)', s)
    if m:
        return float(m.group(1))
    m = re.search(r'(\d+)', s)
    if m:
        return float(m.group(1))
    return np.nan


def _debut_year(debut_season_str) -> Optional[int]:
    """Parse debut_season like '93/94' → 1993, '17/18' → 2017."""
    if not debut_season_str or str(debut_season_str) in ('nan', 'None', ''):
        return None
    s = str(debut_season_str).strip()
    m = re.match(r'(\d{2})/\d{2}', s)
    if not m:
        return None
    yy = int(m.group(1))
    return (2000 + yy) if yy < 50 else (1900 + yy)


# ---------------------------------------------------------------------------
# Feature 20 — Class Trajectory
# ---------------------------------------------------------------------------

def compute_class_trajectory(
    runner: Runner, stride_results: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#20 — Slope of NR (class level) over last N runs.
    Negative slope = dropping in class (easier competition) = positive signal.
    Returned negated so higher score = dropping into weaker company.
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, CLASS_TRAJECTORY_LOOKBACK)
    if runs.empty or 'Class' not in runs.columns:
        return 0.0, warnings

    vals = runs['Class'].apply(_parse_class_nr)
    vals = vals.dropna()
    if len(vals) < 2:
        return 0.0, warnings

    # runs are sorted newest-first; reverse for chronological slope
    vals_chron = vals.iloc[::-1].reset_index(drop=True)
    x = np.arange(len(vals_chron))
    slope = float(np.polyfit(x, vals_chron.values, 1)[0])
    # Negate: negative slope (dropping NR) → positive score
    return -slope, warnings


# ---------------------------------------------------------------------------
# Feature 21 — Field Size Adjustment
# ---------------------------------------------------------------------------

def compute_field_size_adjustment(
    runner: Runner, stride_results: pd.DataFrame, today_field_size: int
) -> Tuple[float, List[str]]:
    """#21 — Ratio of today's field size to horse's historical avg field size.
    >1 means bigger field than usual; <1 means smaller.
    Feeds into barrier weight modulation.
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, 10)
    if runs.empty or 'Starters' not in runs.columns:
        return 1.0, warnings

    vals = pd.to_numeric(runs['Starters'], errors='coerce').dropna()
    if vals.empty or vals.mean() == 0:
        return 1.0, warnings

    hist_avg = float(vals.mean())
    ratio = float(today_field_size) / hist_avg if hist_avg > 0 else 1.0
    return ratio, warnings


# ---------------------------------------------------------------------------
# Feature 22 — Winner Beaten Quality
# ---------------------------------------------------------------------------

def compute_winner_beaten_quality(
    runner: Runner, stride_results: pd.DataFrame, stride_profiles: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#22 — Avg career win rate of winners this horse was beaten by (last 10 runs).
    Higher = beaten by better horses = more informative form.
    """
    warnings = []
    runs = _recent_runs(runner, stride_results, WINNER_QUALITY_LOOKBACK)
    if runs.empty or 'Winner' not in runs.columns or 'Place' not in runs.columns:
        return 0.0, warnings

    # Only runs where this horse didn't win
    beaten = runs[pd.to_numeric(runs['Place'], errors='coerce') > 1]
    if beaten.empty:
        return 0.0, warnings

    rates = []
    for _, row in beaten.iterrows():
        winner_name = str(row.get('Winner', '')).strip()
        if not winner_name or winner_name.lower() in ('', 'nan', 'none'):
            continue
        # Look up winner in stride_profiles
        winner_lower = winner_name.lower()
        rows = stride_profiles[stride_profiles['_slug'] == winner_lower] if '_slug' in stride_profiles.columns else pd.DataFrame()
        if rows.empty and 'Horse' in stride_profiles.columns:
            rows = stride_profiles[stride_profiles['Horse'].str.lower().str.strip() == winner_lower]
        if not rows.empty:
            rate = _safe_float(parse_pct(rows.iloc[0].get('Career_Win_Pct', 0)), 0.0)
            rates.append(rate)

    return float(np.mean(rates)) if rates else 0.0, warnings


# ---------------------------------------------------------------------------
# Feature 24 — Days Since Last Win
# ---------------------------------------------------------------------------

def compute_days_since_last_win(
    runner: Runner, stride_profiles: pd.DataFrame, race_date: str
) -> Tuple[float, List[str]]:
    """#24 — Days from last win to today. Long drought → negative score.
    Returns a normalised score: 0.0 at WIN_DROUGHT_WARN_DAYS, positive for recent wins.
    """
    warnings = []
    profile = _profile_row(runner, stride_profiles)
    if profile is None or 'Last_Win_Date' not in profile.index:
        return 0.0, warnings

    last_win = profile.get('Last_Win_Date')
    if not last_win or str(last_win) in ('nan', 'None', ''):
        # Never won — return strong negative
        return -1.0, warnings

    try:
        ref = datetime.strptime(race_date, '%Y-%m-%d') if race_date else datetime.today()
        lw_date = pd.Timestamp(last_win)
        days = (ref - lw_date).days
    except Exception:
        return 0.0, warnings

    # Score: +1 for very recent win (<21 days), 0 at 180 days, -1 at 365+ days
    if days < 0:
        return 0.0, warnings
    score = 1.0 - (days / WIN_DROUGHT_WARN_DAYS)
    return float(np.clip(score, -1.0, 1.0)), warnings


# ---------------------------------------------------------------------------
# Feature 25 — Last Win Venue Match
# ---------------------------------------------------------------------------

def compute_last_win_venue_match(
    runner: Runner, stride_profiles: pd.DataFrame, track: str
) -> Tuple[float, List[str]]:
    """#25 — Boolean: did horse's most recent win come at today's venue?"""
    warnings = []
    profile = _profile_row(runner, stride_profiles)
    if profile is None or 'Last_Win_Venue' not in profile.index:
        return 0.0, warnings

    last_venue = str(profile.get('Last_Win_Venue', '')).strip()
    if not last_venue or last_venue in ('nan', 'None', ''):
        return 0.0, warnings

    match = last_venue.lower() == track.lower()
    return 1.0 if match else 0.0, warnings


# ---------------------------------------------------------------------------
# Features 29 & 30 — Driver Group Experience and Experience Years
# ---------------------------------------------------------------------------

def compute_driver_group_experience(
    runner: Runner, drivers_profile: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#29 — Total black-type wins (G1+G2+G3+Listed). Log-scaled.
    Indicates top-level competency under pressure.
    """
    warnings = []
    if not runner.driver or drivers_profile.empty:
        return 0.0, warnings

    row = _lookup_driver_profile(runner.driver, drivers_profile)
    if row is None:
        return 0.0, warnings

    cols = [
        'driver.lifetime_summary.group_1_wins',
        'driver.lifetime_summary.group_2_wins',
        'driver.lifetime_summary.group_3_wins',
        'driver.lifetime_summary.listed_race_wins',
    ]
    total = sum(_safe_float(row.get(c, 0), 0.0) for c in cols)
    # Log-scale: 0 wins → 0, 1 win → 0.69, 10 wins → 2.3, 100 wins → 4.6
    return float(np.log1p(total)), warnings


def compute_driver_experience_years(
    runner: Runner, drivers_profile: pd.DataFrame
) -> Tuple[float, List[str]]:
    """#30 — Years since debut season. Junior (<3yrs) penalty, veteran (>15yrs) bonus.
    Returns a normalised modifier: -0.5 for junior, 0 for mid-career, +0.5 for veteran.
    """
    warnings = []
    if not runner.driver or drivers_profile.empty:
        return 0.0, warnings

    row = _lookup_driver_profile(runner.driver, drivers_profile)
    if row is None:
        return 0.0, warnings

    debut_str = row.get('driver.debut_season')
    debut_yr = _debut_year(debut_str)
    if debut_yr is None:
        # Fall back to age — estimate debut at age 16
        age = _safe_float(row.get('driver.age', 0), 0.0)
        if age > 0:
            debut_yr = int(datetime.today().year - (age - 16))
        else:
            return 0.0, warnings

    years = datetime.today().year - debut_yr
    if years < DRIVER_JUNIOR_YEARS:
        return -0.5, warnings
    elif years >= DRIVER_VETERAN_YEARS:
        return 0.5, warnings
    else:
        # Linear interpolation between -0.5 and +0.5
        return float(-0.5 + (years / DRIVER_VETERAN_YEARS)), warnings


# ---------------------------------------------------------------------------
# Feature 35 — Distance Optimal Range
# ---------------------------------------------------------------------------

def compute_distance_optimal_range(
    runner: Runner, stride_results: pd.DataFrame, race_distance_m: int
) -> Tuple[float, List[str]]:
    """#35 — Score based on how close today's distance is to the horse's optimal bin.
    Bins career runs by DISTANCE_OPTIMAL_BIN_M width; finds the bin with best place rate.
    Returns 1.0 at optimal, decaying toward 0 as distance from optimal increases.
    """
    warnings = []
    if not race_distance_m or 'Distance_m' not in stride_results.columns:
        return 0.0, warnings

    runs = _recent_runs(runner, stride_results, 100)
    if runs.empty:
        return 0.0, warnings

    dist = pd.to_numeric(runs['Distance_m'], errors='coerce')
    place = pd.to_numeric(runs['Place'], errors='coerce')
    valid = dist.notna() & place.notna()
    if valid.sum() < 3:
        return 0.0, warnings

    dist_v = dist[valid].values
    place_v = place[valid].values

    # Build bins
    bin_min = int(dist_v.min() // DISTANCE_OPTIMAL_BIN_M) * DISTANCE_OPTIMAL_BIN_M
    bin_max = int(dist_v.max() // DISTANCE_OPTIMAL_BIN_M + 1) * DISTANCE_OPTIMAL_BIN_M
    best_rate = -1.0
    best_bin_centre = float(dist_v.mean())

    for b in range(bin_min, bin_max, DISTANCE_OPTIMAL_BIN_M):
        mask = (dist_v >= b) & (dist_v < b + DISTANCE_OPTIMAL_BIN_M)
        if mask.sum() < 2:
            continue
        rate = float((place_v[mask] <= 3).sum() / mask.sum())
        if rate > best_rate:
            best_rate = rate
            best_bin_centre = b + DISTANCE_OPTIMAL_BIN_M / 2

    if best_rate < 0:
        return 0.0, warnings

    # Score decays with distance from optimal bin centre
    gap_m = abs(float(race_distance_m) - best_bin_centre)
    score = max(0.0, 1.0 - gap_m / (2 * DISTANCE_OPTIMAL_BIN_M))
    return score, warnings


# ---------------------------------------------------------------------------
# Feature 36 — Career Class Experience
# ---------------------------------------------------------------------------

def compute_career_class_experience(
    runner: Runner, stride_results: pd.DataFrame, race_date: str
) -> Tuple[float, List[str]]:
    """#36 — Pct of career runs at the horse's own NR (current class) or above.
    Uses runner.nr as the class benchmark.
    Higher = more experienced at this level or better.
    """
    warnings = []
    if runner.nr <= 0 or 'Class' not in stride_results.columns:
        return 0.0, warnings

    runs = _recent_runs(runner, stride_results, 100)
    if len(runs) < CAREER_CLASS_MIN_RUNS:
        return 0.0, warnings

    nr_vals = runs['Class'].apply(_parse_class_nr).dropna()
    if len(nr_vals) < CAREER_CLASS_MIN_RUNS:
        return 0.0, warnings

    at_or_above = (nr_vals >= runner.nr).sum()
    return float(at_or_above / len(nr_vals)), warnings


# ---------------------------------------------------------------------------
# Feature 37 — Mobile Barrier Rate
# ---------------------------------------------------------------------------

def compute_mobile_barrier_rate(
    runner: Runner, stride_results: pd.DataFrame, barrier: int
) -> Tuple[float, List[str]]:
    """#37 — Win/place rate when drawn at today's barrier in mobile starts specifically.
    Uses enriched stride_results (Start_Type column from dividends join).
    Confidence-weighted.
    """
    warnings = []
    if barrier <= 0 or 'Start_Type' not in stride_results.columns:
        return 0.0, warnings

    runs = _recent_runs(runner, stride_results, 100)
    if runs.empty:
        return 0.0, warnings

    ms_runs = runs[runs['Start_Type'].astype(str).str.upper().str.strip() == 'MS'].reset_index(drop=True)
    if ms_runs.empty:
        return 0.0, warnings

    if 'Barrier' not in ms_runs.columns:
        return 0.0, warnings
    barrier_mask = pd.to_numeric(ms_runs['Barrier'], errors='coerce').values == barrier
    matched = ms_runs[barrier_mask]
    n = len(matched)

    if n < MOBILE_BARRIER_MIN_RUNS:
        return 0.0, warnings

    wins_places = (pd.to_numeric(matched['Place'], errors='coerce') <= 3).sum()
    rate = float(wins_places / n)
    conf = _confidence_weight(n)

    # Prior: overall MS place rate
    ms_n = len(ms_runs)
    ms_rate = float((pd.to_numeric(ms_runs['Place'], errors='coerce') <= 3).sum() / ms_n) if ms_n > 0 else 0.0
    return conf * rate + (1.0 - conf) * ms_rate, warnings


def check_driver_penalty(
    runner: Runner,
    stewards_penalties: pd.DataFrame,
    race_date: str,
) -> List[str]:
    """Check if listed driver has a suspension within the lookback window."""
    warnings = []
    if stewards_penalties.empty or not runner.driver:
        return warnings

    driver_lower = runner.driver.lower().strip()
    if 'Person' not in stewards_penalties.columns:
        return warnings

    try:
        ref = datetime.strptime(race_date, '%Y-%m-%d') if race_date else datetime.today()
    except ValueError:
        ref = datetime.today()
    cutoff = ref - timedelta(days=DRIVER_PENALTY_LOOKBACK_DAYS)

    relevant = stewards_penalties[
        stewards_penalties['Date'].notna() &
        (stewards_penalties['Date'] >= cutoff) &
        (stewards_penalties['Date'] <= ref)
    ]

    for _, row in relevant.iterrows():
        person = str(row.get('Person', '')).lower()
        detail = str(row.get('Detail', '')).upper()
        type_ = str(row.get('Type', '')).upper()
        if driver_lower in person and any(kw in detail or kw in type_ for kw in SUSPENSION_KEYWORDS):
            warnings.append(
                f"Driver {runner.driver!r}: active suspension penalty detected — "
                f"substitute driver likely"
            )
            break

    return warnings


# ---------------------------------------------------------------------------
# Main feature extraction
# ---------------------------------------------------------------------------

def extract_features(
    runner: Runner,
    field: List[Runner],
    data: DataLoader,
    track: str,
    start_type: str,
    race_date: str,
    distance_m: int = 0,
    track_condition: str = '',
) -> Tuple[Dict[str, float], List[str]]:
    """Compute the full feature vector for one runner.

    Returns:
        (features_dict, warnings_list)
    """
    warnings: List[str] = []
    feats: Dict[str, float] = {}

    def add(key, fn_result):
        val, w = fn_result
        feats[key] = val
        warnings.extend(w)

    add('gate_speed',           compute_gate_speed(runner, data.stride_results))
    add('finishing_speed',      compute_finishing_speed(runner, data.stride_results))
    add('mile_rate_trend',      compute_mile_rate_trend(runner, data.stride_results))
    add('venue_win_rate',       compute_venue_win_rate(runner, data.stride_profiles, track))
    add('venue_place_rate',     compute_venue_place_rate(runner, data.stride_profiles, track))
    add('barrier_score',        compute_barrier_score(runner, track, start_type))
    add('driver_venue_rate',    compute_driver_venue_rate(runner, data.driver_results, track, data.stride_results))
    add('driver_season_winrate',compute_driver_season_winrate(runner, data.drivers_profile))
    add('driver_horse_combo',   compute_driver_horse_combo(runner, data.stride_results))
    add('last_800m_pos',        compute_last_800m_pos(runner, data.stride_results))
    add('width_penalty',        compute_width_penalty(runner, data.stride_results))
    add('position_800m_margin', compute_position_800m_margin(runner, data.stride_results))
    add('position_400m_margin', compute_position_400m_margin(runner, data.stride_results))
    add('stewards_flag',        compute_stewards_flag(runner, data.stewards_notes, race_date))
    add('injury_return',        compute_injury_return(runner, data.stewards_stand_downs, race_date))
    add('driver_suspended',     compute_driver_suspended(runner, data.stewards_penalties, race_date))

    add('consistency_score',    compute_consistency_score(runner, data.stride_results))
    add('sp_vs_performance',    compute_sp_vs_performance(runner, data.stride_results))
    add('distance_suitability', compute_distance_suitability(runner, data.stride_results, distance_m))
    add('start_type_rate',      compute_start_type_rate(runner, data.stride_results, start_type))
    add('trainer_form',         compute_trainer_form(runner, data.trainer_results, data.stride_results))
    add('track_condition_rate', compute_track_condition_rate(runner, data.stride_results, track_condition))

    # Lower-priority features
    add('class_trajectory',       compute_class_trajectory(runner, data.stride_results))
    add('field_size_adjustment',  compute_field_size_adjustment(runner, data.stride_results, len(field)))
    add('winner_beaten_quality',  compute_winner_beaten_quality(runner, data.stride_results, data.stride_profiles))
    add('days_since_last_win',    compute_days_since_last_win(runner, data.stride_profiles, race_date))
    add('last_win_venue_match',   compute_last_win_venue_match(runner, data.stride_profiles, track))
    add('driver_group_experience',compute_driver_group_experience(runner, data.drivers_profile))
    add('driver_experience_years',compute_driver_experience_years(runner, data.drivers_profile))
    add('distance_optimal_range', compute_distance_optimal_range(runner, data.stride_results, distance_m))
    add('career_class_experience',compute_career_class_experience(runner, data.stride_results, race_date))
    add('mobile_barrier_rate',    compute_mobile_barrier_rate(runner, data.stride_results, runner.barrier))

    # Field strength — same scalar for all runners, stored per-runner
    field_strength = compute_field_strength(field, data.stride_profiles)
    feats['field_strength'] = field_strength

    # Class relativity — needs field context
    field_nrs = [r.nr for r in field if r.nr > 0]
    field_avg_nr = float(np.mean(field_nrs)) if field_nrs else 0.0
    cr, cw = compute_class_relativity(runner, field_avg_nr)
    feats['class_relativity'] = cr
    warnings.extend(cw)

    # Freshness
    fr, fw = compute_freshness(runner, data.stride_results, race_date)
    feats['freshness'] = fr
    warnings.extend(fw)

    # Driver penalty check (warning only, doesn't modify features)
    warnings.extend(check_driver_penalty(runner, data.stewards_penalties, race_date))

    return feats, warnings


def extract_all_features(
    race_info: RaceInfo,
    data: DataLoader,
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """Extract features for every runner in the field.

    Returns:
        all_features: {horse_slug: {feature_name: value}}
        all_warnings: combined warning list
    """
    all_features: Dict[str, Dict[str, float]] = {}
    all_warnings: List[str] = []

    for runner in race_info.runners:
        feats, warns = extract_features(
            runner=runner,
            field=race_info.runners,
            data=data,
            track=race_info.track,
            start_type=race_info.start_type,
            race_date=race_info.date,
            distance_m=race_info.distance_m,
            track_condition=getattr(race_info, 'track_condition', ''),
        )
        all_features[runner.slug] = feats
        all_warnings.extend(warns)

    return all_features, all_warnings
