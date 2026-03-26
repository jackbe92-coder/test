"""
sim_config.py — Weight ranges, track overrides, and feature flags for the Monte Carlo race simulator.
"""

# ---------------------------------------------------------------------------
# Monte Carlo weight ranges
# Each entry: (min, max) — sampled uniformly per simulation run
# ---------------------------------------------------------------------------
WEIGHT_RANGES = {
    'w_gate_speed':      (0.5, 2.0),  # Early position: does leading lock in the result?
    'w_finishing_speed': (0.5, 2.0),  # Last 400m: does a strong finish beat leaders?
    'w_barrier':         (0.3, 1.8),  # Draw determinism: how much does gate matter?
    'w_venue_rate':      (0.5, 1.5),  # Track-specific form: signal or sample noise? (#11, #12)
    'w_driver':          (0.3, 1.2),  # Driver venue rate (#6)
    'w_driver_quality':  (0.3, 1.2),  # Driver career + season win rate (#27, #28)
    'w_driver_combo':    (0.2, 1.0),  # Driver-horse combination rate (#31)
    'w_class':           (0.5, 1.5),  # Is class the dominant factor?
    'w_trend':           (0.3, 1.2),  # Improving/declining form momentum
    'w_luck':            (0.2, 0.8),  # Racing luck — interference, wide runs, splits
    'w_value':           (0.2, 1.0),   # SP vs performance outperformance (#17)
    'w_distance':        (0.3, 1.2),   # distance suitability (#18, #35)
    'w_trainer':         (0.3, 1.2),   # trainer stable form (#26)
    'w_track_condition': (0.2, 1.2),   # track condition match (#33)
    'w_driver_class':    (0.2, 1.0),   # driver group/black-type experience (#29)
}

# ---------------------------------------------------------------------------
# Track-specific weight overrides (applied as absolute range adjustments)
# ---------------------------------------------------------------------------
TRACK_OVERRIDES = {
    'Burnie': {
        # 607m track, 95m straight — gate speed and draw highly determinative
        'w_barrier':         (+0.3, +0.3),
        'w_finishing_speed': (-0.2, -0.2),
    },
    'Hobart': {
        # 970m track with sprint lane — draw less determinative, finishers favoured
        'w_barrier':         (-0.2, -0.2),
        'w_finishing_speed': (+0.2, +0.2),
    },
}

# Standing start: subtract from both bounds of w_barrier (uneven breaks reduce draw determinism)
STANDING_START_BARRIER_ADJUSTMENT = -0.3

# ---------------------------------------------------------------------------
# Freshness (days since last run) scoring
# ---------------------------------------------------------------------------
FRESHNESS_OPTIMAL_MIN = 14   # days — below this = too fresh / backing up
FRESHNESS_OPTIMAL_MAX = 28   # days — above this = rustiness creeping in
FRESHNESS_SCORE_OPTIMAL = 1.0
FRESHNESS_SHORT_CUTOFF = 7   # <7 days = strong penalty
FRESHNESS_LONG_CUTOFF = 60   # >60 days = rust penalty
FRESHNESS_SCORE_SHORT = -0.5   # <7 days
FRESHNESS_SCORE_OK_LOW = 0.5   # 7–13 days
FRESHNESS_SCORE_OK_HIGH = 0.5  # 29–60 days
FRESHNESS_SCORE_LONG = -0.3    # >60 days

# ---------------------------------------------------------------------------
# Stewards note keyword classification
# ---------------------------------------------------------------------------
# Full phrase patterns — order matters, longer phrases matched first
# 'galloped' alone is ambiguous; use 'galloped when' for excuses and 'galloped out' for concerns
EXCUSE_KEYWORDS = [
    'severely checked', 'checked', 'inconvenienced', 'held up', 'tightened',
    'galloped when', 'knocked', 'wide', 'blocked',
    'interference', 'bumped', 'crowded',
]
CONCERN_KEYWORDS = [
    'galloped out', 'pulled hard', 'hung in', 'hung out',
    'fractious', 'broke', 'refused', 'reared', 'last chance',
    'unruly', 'began badly', 'out of position at the start',
]

# Scores applied per note — #23 spec: +1/-1 per note, summed over last 5
EXCUSE_CREDIT = +1.0    # per excuse note
CONCERN_PENALTY = -1.0  # per concern note
STEWARDS_FLAG_CAP = 3.0   # max absolute value (5 notes × ±1)
STEWARDS_LOOKBACK_NOTES = 5  # last N notes to classify (#23: last 5)

# ---------------------------------------------------------------------------
# Injury return risk multiplier applied to final score
# ---------------------------------------------------------------------------
INJURY_RETURN_PENALTY = -0.6  # applied once if horse is returning from stand-down

# ---------------------------------------------------------------------------
# Driver penalty lookback window (days)
# ---------------------------------------------------------------------------
DRIVER_PENALTY_LOOKBACK_DAYS = 30
DRIVER_SUSPENSION_LOOKBACK_DAYS = 60  # #32: broader window for suspension check

# Suspension keyword in stewards_penalties to detect driver substitutions
SUSPENSION_KEYWORDS = ['SUSPENSION', 'DISQUALIFIED', 'STAND DOWN', 'STAND-DOWN']

# Driver career/season win rate — minimum starts to trust the rate (#27, #28)
DRIVER_SEASON_MIN_STARTS = 20   # use season rate only if >= this many starts

# ---------------------------------------------------------------------------
# Confidence weighting for small sample sizes (#11, #12, #31)
# Scales from 0.0 at n=0 → 0.5 at n=min_n → 1.0 at n=full_n
# ---------------------------------------------------------------------------
CONFIDENCE_MIN_N = 5
CONFIDENCE_FULL_N = 15

# Driver-horse combination minimum runs to apply the metric (#31)
DRIVER_COMBO_MIN_RUNS = 3

# ---------------------------------------------------------------------------
# Medium-priority feature constants
# ---------------------------------------------------------------------------
CONSISTENCY_LOOKBACK = 10         # last N runs for stdev of finish positions
SP_PERFORMANCE_LOOKBACK = 10      # last N runs for SP vs actual performance
DISTANCE_MATCH_WINDOW_M = 100     # ±metres from today's distance for match
DISTANCE_MIN_RUNS = 3             # min runs at that distance to apply rate
START_TYPE_MIN_RUNS = 3           # min runs in same start type
TRACK_CONDITION_MIN_RUNS = 3      # min runs in same condition
TRAINER_FORM_LOOKBACK = 10        # last N trainer runs for stable form
FIELD_STRENGTH_BASELINE = 0.10    # baseline avg career win rate (10%) for scaling

# ---------------------------------------------------------------------------
# Lower-priority feature constants
# ---------------------------------------------------------------------------
CLASS_TRAJECTORY_LOOKBACK = 5    # runs to compute class slope (#20)
WINNER_QUALITY_LOOKBACK = 10     # runs to avg winner career win rate (#22)
WIN_DROUGHT_WARN_DAYS = 365      # days without a win = extended drought (#24)
DISTANCE_OPTIMAL_BIN_M = 200     # bin width for optimal distance range (#35)
MOBILE_BARRIER_MIN_RUNS = 3      # min MS runs at this barrier to apply (#37)
CAREER_CLASS_MIN_RUNS = 5        # min career runs to compute class experience (#36)
DRIVER_JUNIOR_YEARS = 3          # years since debut = junior driver (#30)
DRIVER_VETERAN_YEARS = 15        # years since debut = veteran driver (#30)

# ---------------------------------------------------------------------------
# Minimum runs required for reliable feature estimates
# ---------------------------------------------------------------------------
MIN_RUNS_RELIABLE = 3   # fewer than this → warn and use available data
GATE_SPEED_LOOKBACK = 5
FINISHING_SPEED_LOOKBACK = 5
TREND_LOOKBACK = 5
WIDTH_LOOKBACK = 5
LAST_800_LOOKBACK = 5

# ---------------------------------------------------------------------------
# Simulation defaults
# ---------------------------------------------------------------------------
DEFAULT_RUNS = 50000  # Run overnight — stability over speed

# Robustness thresholds (as fraction of total runs)
ROBUST_WIN_THRESHOLD = 0.20     # wins >20% of simulations = robust
FRAGILE_VARIANCE_THRESHOLD = 0.15  # win% std-dev across weight bands > this = fragile

# Value flag thresholds
VALUE_EDGE_THRESHOLD = 0.05    # sim win% at least 5pp above implied SP% = VALUE
OVERBET_EDGE_THRESHOLD = -0.05  # sim win% at least 5pp below implied SP% = OVERBET

# ---------------------------------------------------------------------------
# Agent-based model constants
# ---------------------------------------------------------------------------

# Baseline harness pace (metres per second)
BASE_PACE_MS = 14.0

# Recency decay half-life for time-series features
RECENCY_HALF_LIFE_DAYS = 60

# Data quality
MIN_DATA_CONFIDENCE = 0.4   # warn below this
MAX_WIN_PCT_SINGLE_HORSE = 0.75  # flag if any horse exceeds 75% in a field of 8+

# DLW (Days since Last Win) decay multiplier table
# Interpolate between breakpoints. Beyond 400 → 0.30.
DLW_DECAY = {
    0:   1.00,   # Won recently
    60:  0.95,
    120: 0.85,
    200: 0.70,   # Meaningful discount begins
    300: 0.50,   # Strong discount
    400: 0.30,   # Last chance territory
}

# Phase 1 (Start → 800m) energy costs per position bucket
# These are fractional energy depleted through the first 800m segment.
# Burnie (short circuit, more laps early) has higher costs than Launceston.
# Track profiles may override these per track.
PHASE1_LEADER_ENERGY_COST   = 0.15
PHASE1_ON_PACE_ENERGY_COST  = 0.10
PHASE1_MIDFIELD_ENERGY_COST = 0.06
PHASE1_BACK_ENERGY_COST     = 0.03

# Phase 2 (800m → 400m) energy costs — shorter segment, ~50% of phase 1
PHASE2_LEADER_ENERGY_COST   = 0.08
PHASE2_ON_PACE_ENERGY_COST  = 0.05
PHASE2_MIDFIELD_ENERGY_COST = 0.03
PHASE2_BACK_ENERGY_COST     = 0.015

# Gate speed noise — scaled by w_luck per run
GATE_NOISE_STD = 0.6

# Gap between successive positions at 800m (metres)
PHASE1_GAP_PER_POSITION_M = 4.0

# Convergence check parameters
CONVERGENCE_BATCH_SIZE    = 5000   # runs per batch before checking convergence
CONVERGENCE_THRESHOLD     = 0.005  # max shift in top-3 win% between batches
CONVERGENCE_STABLE_BATCHES = 2     # stop if stable for this many consecutive batches

# ODM (Out of Draw in Mobiles) penalty multipliers on position-dependent features
ODM_MOBILE_PENALTY_SHORT = 0.30   # <2000m: severe (almost no time to recover)
ODM_MOBILE_PENALTY_LONG  = 0.60   # >=2200m: less punishing

# Width penalty in Phase 2 — extra metres per lane outside rail per 400m segment
WIDTH_PENALTY_M_PER_LANE = 0.5

# Interference probability per horse per phase segment
INTERFERENCE_PROB = 0.05
INTERFERENCE_ENERGY_COST = 0.05
INTERFERENCE_GAP_PENALTY_M = 5.0

# Tactical move probability parameters
TACTICAL_ENERGY_THRESHOLD = 0.75   # horse must have >75% energy to attempt move
TACTICAL_MOVE_BASE_PROB   = 0.25   # base probability when threshold met
TACTICAL_GAP_GAIN_M       = 3.0    # metres gained by successful tactical move

# Speed map position categories — relative to field std-dev of pace scores each run.
# Using field-relative thresholds prevents one dominant horse from collapsing all
# others to 'back' (which happened with fixed absolute gaps).
PACE_GAP_ON_PACE_STDEV   = 1.5   # within 1.5 × field_std of leader = leader/on_pace
PACE_GAP_MIDFIELD_STDEV  = 3.0   # within 3.0 × field_std = midfield; beyond = back

# ---------------------------------------------------------------------------
# Venue name normalisation map (handle abbreviations / alternate spellings)
# ---------------------------------------------------------------------------
VENUE_NORMALISE = {
    'launceston': 'Launceston',
    'hobart':     'Hobart',
    'burnie':     'Burnie',
    'carrick':    'Carrick',
    'scottsdale': 'Scottsdale',
    'st marys':   'St Marys',
    'st. marys':  'St Marys',
    'st. marys (tas)': 'St Marys',
    'king island': 'King Island',
    'devonport':  'Devonport',
}

# Map track name → stride_profiles column suffixes for wins, places, starts (#11, #12)
VENUE_WINS_COLUMNS = {
    'Launceston': 'Launceston_Wins',
    'Hobart':     'Hobart_Wins',
    'Burnie':     'Burnie_Wins',
    'Carrick':    'Carrick_Wins',
    'Scottsdale': 'Scottsdale_Wins',
    'St Marys':   'St Marys_Wins',
    'King Island': 'King Island_Wins',
    'Devonport':  'Devonport_Wins',
}
VENUE_PLACES_COLUMNS = {
    'Launceston': 'Launceston_Places',
    'Hobart':     'Hobart_Places',
    'Burnie':     'Burnie_Places',
    'Carrick':    'Carrick_Places',
    'Scottsdale': 'Scottsdale_Places',
    'St Marys':   'St Marys_Places',
    'King Island': 'King Island_Places',
    'Devonport':  'Devonport_Places',
}
VENUE_STARTS_COLUMNS = {
    'Launceston': 'Launceston_Starts',
    'Hobart':     'Hobart_Starts',
    'Burnie':     'Burnie_Starts',
    'Carrick':    'Carrick_Starts',
    'Scottsdale': 'Scottsdale_Starts',
    'St Marys':   'St Marys_Starts',
    'King Island': 'King Island_Starts',
    'Devonport':  'Devonport_Starts',
}

# Map track name → stride_profiles column suffix
VENUE_WIN_PCT_COLUMNS = {
    'Launceston': 'Launceston_Win_Pct',
    'Hobart':     'Hobart_Win_Pct',
    'Burnie':     'Burnie_Win_Pct',
    'Carrick':    'Carrick_Win_Pct',
    'Scottsdale': 'Scottsdale_Win_Pct',
    'St Marys':   'St Marys_Win_Pct',
    'King Island': 'King Island_Win_Pct',
    'Devonport':  'Devonport_Win_Pct',
}

# ---------------------------------------------------------------------------
# Track profiles — data-validated position multipliers (2025-2026 season)
# These are FIXED constants derived from sectionals analysis.
# Do NOT place inside the Monte Carlo weight sampler.
# Applied as a post-processing multiplier after the base score is computed.
# ---------------------------------------------------------------------------
TRACK_PROFILES = {
    'Burnie': {
        # ── Physical layout ──────────────────────────────────────────────────
        'length_m': 607, 'straight_m': 95, 'sprint_lane': False,
        'circuit_length_m': 607,
        'run_in_metres': 80,          # Very short — almost no time to clear outside
        'gate_clear_threshold': 2,    # Only B1-B2 can realistically clear to rail
        'standing_start_equalisation': 0.5,  # Short run still rewards draw somewhat
        # ── Pace physics ─────────────────────────────────────────────────────
        # Burnie: short circuit = more circuits = more pace work = higher energy cost
        'leader_energy_cost': 0.22,
        'on_pace_cost':        0.15,
        'midfield_cost':       0.10,
        'back_cost':           0.05,
        # ── Historical data ──────────────────────────────────────────────────
        'leader_win_pct': 0.68,   # Data: 19/28
        # Fixed multipliers — leader 2.8 = 68%/12.5% baseline
        'position_multiplier': {
            'leader':   2.8,
            'on_pace':  0.5,
            'midfield': 0.1,
            'back':     0.05,
        },
        # Cap back-marker pre-multiplier score to field average before applying 0.05x.
        # Prevents dominant class scores from overriding track reality at a 607m circuit.
        'cap_back_score': True,
        # Tighter on-pace threshold: at 607m horses bunch early, gap between
        # leader and 2nd is small. Override global 1.5× with 1.0×.
        'pace_gap_on_pace_stdev': 1.8,
        'gate_speed_weight_boost':    0.5,
        'barrier_weight_boost':       0.4,
        'finishing_weight_penalty':  -0.3,
    },
    'Hobart': {
        # ── Physical layout ──────────────────────────────────────────────────
        'length_m': 970, 'straight_m': 200, 'sprint_lane': True,
        'circuit_length_m': 850,
        'run_in_metres': 150,         # Medium run-in — moderate gate clear opportunity
        'gate_clear_threshold': 4,    # Up to B4 can reasonably clear to sprint lane
        'standing_start_equalisation': 0.4,
        # ── Pace physics ─────────────────────────────────────────────────────
        'leader_energy_cost': 0.17,
        'on_pace_cost':        0.12,
        'midfield_cost':       0.08,
        'back_cost':           0.04,
        # Sprint lane: only horses on rail at 400m get the bonus
        'sprint_lane_bonus': 0.3,
        # ── Historical data ──────────────────────────────────────────────────
        'leader_win_pct': 0.38,   # Data: 25/65
        'garden_seat_win_pct': 0.152,
        'position_multiplier': {
            'leader':          1.6,
            'garden_seat':     1.5,
            'midfield_runner': 1.0,
            'midfield':        0.5,
            'back':            0.3,
        },
        'gate_speed_weight_boost':   0.0,
        'barrier_weight_boost':     -0.2,
        'finishing_weight_boost':    0.3,
    },
    'Launceston': {
        # ── Physical layout ──────────────────────────────────────────────────
        'length_m': 1000, 'straight_m': 220, 'sprint_lane': False,
        'circuit_length_m': 1018,
        'run_in_metres': 180,         # Long run-in — gate speed CAN overcome wide draw
        'gate_clear_threshold': 5,    # Up to B5-B6 can clear on a fast jump
        'standing_start_equalisation': 0.3,  # Long run-in = draw matters least in SS
        # ── Pace physics ─────────────────────────────────────────────────────
        # Launceston: long circuit = fewer laps = lower energy cost for leaders
        'leader_energy_cost': 0.15,
        'on_pace_cost':        0.10,
        'midfield_cost':       0.06,
        'back_cost':           0.03,
        # ── Historical data ──────────────────────────────────────────────────
        'leader_win_pct': 0.12,   # Data: closing speed dominates
        'position_multiplier': {
            'leader':   1.0,
            'on_pace':  1.1,
            'midfield': 1.0,
            'back':     0.4,
        },
        'gate_speed_weight_boost':   -0.4,
        'barrier_weight_boost':      -0.5,
        'class_weight_boost':         0.3,
        'finishing_weight_boost':     0.3,
    },
    'Carrick': {
        # ── Physical layout ──────────────────────────────────────────────────
        'length_m': 900, 'straight_m': 150, 'sprint_lane': False,
        'circuit_length_m': 900,
        'run_in_metres': 120,
        'gate_clear_threshold': 3,
        'standing_start_equalisation': 0.45,
        # ── Pace physics ─────────────────────────────────────────────────────
        'leader_energy_cost': 0.18,
        'on_pace_cost':        0.12,
        'midfield_cost':       0.07,
        'back_cost':           0.04,
        # ── Historical data ──────────────────────────────────────────────────
        'leader_win_pct': 0.33,
        'position_multiplier': {
            'leader':   1.8,
            'on_pace':  1.4,
            'midfield': 0.6,
            'back':     0.2,
        },
        # Standing start outer barrier bonus (applied when start_type == 'SS')
        'ss_barrier_adj': {
            1: +0.05, 2: 0.0, 3: -0.05, 4: -0.05,
            5: +0.10, 6: +0.05, 7: +0.15,
        },
    },
    'Scottsdale': {
        'length_m': 800, 'straight_m': 120, 'sprint_lane': False,
        'use_proxy': 'Burnie',  # Insufficient 2025-2026 data — use Burnie as proxy
    },
}
