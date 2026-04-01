"""
report_generator.py — Format Monte Carlo simulation output into a structured report.

Sections:
  1. Probability Table    — win/place %, implied odds, confidence rating
  2. Predicted Sectionals — harness.au-style 800m/Q3/400m/Q4/margins
  3. Speed Map            — leader/on-pace/midfield/back % for each runner
  4. Robustness Analysis  — robust vs conditional vs fragile selections
  5. Key Factors Summary  — which features were most predictive
  6. Analyst Narrative    — natural language race preview

Uses `rich` for terminal formatting if installed; falls back to plain text.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from form_parser import Runner, RaceInfo
from sim_config import (
    ROBUST_WIN_THRESHOLD, FRAGILE_VARIANCE_THRESHOLD,
)

# ---------------------------------------------------------------------------
# Rich setup (optional)
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _RICH = True
    console = Console()
except ImportError:
    _RICH = False
    console = None


def _print(msg: str = ""):
    if _RICH:
        console.print(msg)
    else:
        print(msg)


def _rule(title: str = ""):
    if _RICH:
        console.rule(title)
    else:
        width = 72
        if title:
            pad = (width - len(title) - 2) // 2
            print("─" * pad + f" {title} " + "─" * pad)
        else:
            print("─" * width)


def _implied_odds(win_pct: float) -> str:
    """Convert win probability to decimal odds."""
    if win_pct <= 0:
        return "—"
    return f"${1.0 / win_pct:.1f}"


def _confidence_label(slug: str, all_features: Dict) -> str:
    """Return confidence rating for a horse based on data availability."""
    feats = all_features.get(slug, {})
    conf = feats.get('_data_confidence', 1.0)
    is_visitor = feats.get('_mainland_visitor', 0.0)
    tas_starts = feats.get('_tas_starts', 999)

    if is_visitor > 0 or conf <= 0:
        return "MAINLAND — NO DATA"
    if tas_starts < 5:
        return "LOW CONFIDENCE"
    return ""


# ---------------------------------------------------------------------------
# Section 1: Probability Table
# ---------------------------------------------------------------------------

def _section_probability_table(
    results: Dict,
    race_info: RaceInfo,
    all_features: Dict,
):
    runner_map = {r.slug: r for r in race_info.runners}
    win_pct = results['win_pct']
    place_pct = results['place_pct']

    sorted_slugs = sorted(win_pct.keys(), key=lambda s: -win_pct[s])

    _rule("1. PROBABILITY TABLE")

    if _RICH:
        table = Table(box=box.SIMPLE_HEAD, show_footer=False)
        table.add_column("Horse", style="bold", min_width=24)
        table.add_column("Barrier", justify="center")
        table.add_column("Driver", min_width=16)
        table.add_column("Win %", justify="right")
        table.add_column("Place %", justify="right")
        table.add_column("Sim Odds", justify="right")
        table.add_column("Confidence", justify="center")

        for slug in sorted_slugs:
            r = runner_map.get(slug)
            if not r:
                continue
            wp = win_pct[slug]
            pp = place_pct[slug]
            conf = _confidence_label(slug, all_features)
            conf_style = "red bold" if conf else "dim"
            table.add_row(
                r.horse,
                str(r.barrier),
                r.driver or "—",
                f"{wp * 100:.1f}%",
                f"{pp * 100:.1f}%",
                _implied_odds(wp),
                Text(conf or "OK", style=conf_style),
            )
        console.print(table)
    else:
        header = f"{'Horse':<28} {'Bar':>3} {'Driver':<18} {'Win%':>6} {'Plc%':>6} {'Odds':>7} {'Confidence':<20}"
        print(header)
        print("-" * len(header))
        for slug in sorted_slugs:
            r = runner_map.get(slug)
            if not r:
                continue
            wp = win_pct[slug]
            pp = place_pct[slug]
            conf = _confidence_label(slug, all_features)
            print(
                f"{r.horse:<28} {r.barrier:>3} {(r.driver or '—'):<18} "
                f"{wp * 100:>5.1f}% {pp * 100:>5.1f}% "
                f"{_implied_odds(wp):>7} {conf or 'OK':<20}"
            )

    # Standing start win rate callout (only for SS races)
    if race_info.start_type == 'SS':
        _print()
        _print("  STANDING START WIN RATE AT TRACK")
        ss_header = f"  {'Horse':<28} {'SS Win%':>8} {'SS Starts':>9}"
        _print(ss_header)
        _print("  " + "-" * (len(ss_header) - 2))
        for slug in sorted_slugs:
            r = runner_map.get(slug)
            if not r:
                continue
            feats = all_features.get(slug, {})
            ss_rate = feats.get('_ss_win_rate', 0.0)
            ss_n = int(feats.get('_ss_starts', 0))
            if ss_n > 0:
                _print(f"  {r.horse:<28} {ss_rate*100:>7.1f}% {ss_n:>9}")
            else:
                _print(f"  {r.horse:<28} {'—':>8} {'0':>9}")


# ---------------------------------------------------------------------------
# Section 2: Predicted Sectionals (harness.au format)
# ---------------------------------------------------------------------------
# Section 2: Predicted Sectionals (harness.au format)
# ---------------------------------------------------------------------------

def _fmt_margin(val: float) -> str:
    """Format margin value — 'Lead' for leader, else metres with sign."""
    if abs(val) < 0.05:
        return "Lead"
    return f"{val:.2f}"


def _fmt_gained(val: float) -> str:
    """Format metres gained with sign."""
    if abs(val) < 0.05:
        return "0.00"
    return f"{val:+.2f}"


def _section_sectionals(results: Dict, race_info: RaceInfo):
    sectionals = results.get('sectionals', {})
    if not sectionals:
        return

    runner_map = {r.slug: r for r in race_info.runners}
    win_pct = results['win_pct']

    # Sort by predicted finish margin (ascending = winner first)
    sorted_slugs = sorted(sectionals.keys(),
                          key=lambda s: sectionals[s].get('avg_finish_margin', 999))

    _rule("2. PREDICTED SECTIONALS  (harness.au format)")

    header = (
        f"{'Horse':<24} {'800m':>7} {'Q3 Split':>8} {'400m':>7} "
        f"{'Q4 Split':>8} {'Gained':>8} {'Gained':>8} {'Finish':>8} {'Gained':>9}"
    )
    subhdr = (
        f"{'':24} {'Margin':>7} {'(800-400)':>8} {'Margin':>7} "
        f"{'(400-Fin)':>8} {'800-400':>8} {'400-Fin':>8} {'Margin':>8} {'800-Fin':>9}"
    )
    print(header)
    print(subhdr)
    print("-" * len(header))

    for slug in sorted_slugs:
        r = runner_map.get(slug)
        if not r:
            continue
        s = sectionals[slug]
        m800 = s['avg_800m_margin']
        m400 = s['avg_400m_margin']
        mfin = s['avg_finish_margin']
        q3   = s['avg_q3_time']
        q4   = s['avg_q4_time']
        g_800_400 = s['avg_gained_800_400']
        g_400_fin = s['avg_gained_400_finish']
        g_total   = g_800_400 + g_400_fin

        print(
            f"{r.horse:<24} "
            f"{_fmt_margin(m800):>7} "
            f"{q3:>7.2f}s "
            f"{_fmt_margin(m400):>7} "
            f"{q4:>7.2f}s "
            f"{_fmt_gained(g_800_400):>8} "
            f"{_fmt_gained(g_400_fin):>8} "
            f"{_fmt_margin(mfin):>8} "
            f"{_fmt_gained(g_total):>9}"
        )


# ---------------------------------------------------------------------------
# Section 3: Speed Map
# ---------------------------------------------------------------------------

def _fmt_bucket(pp: Dict[str, float]) -> str:
    """Format a position bucket dict into 4 compact percentage columns."""
    return (f"{pp.get('leader',0)*100:>4.0f}% {pp.get('on_pace',0)*100:>4.0f}% "
            f"{pp.get('midfield',0)*100:>4.0f}% {pp.get('back',0)*100:>4.0f}%")


def _section_speed_map(results: Dict, race_info: RaceInfo):
    runner_map = {r.slug: r for r in race_info.runners}
    pace_pct = results['pace_pct']
    mid_pct  = results.get('mid_pct', {})
    win_pct  = results['win_pct']
    place_pct = results['place_pct']

    # Sort by leader probability descending
    sorted_slugs = sorted(pace_pct.keys(), key=lambda s: -pace_pct[s].get('leader', 0))

    _rule("3. RACE POSITIONING  (Phase 1 → 2 → 3)")

    # Header
    print(f"{'':28}  ── Phase 1 (800m) ──  ── Phase 2 (400m) ──  ── Phase 3 (Finish) ──")
    header = (f"{'Horse':<28}  {'Lead':>4} {'Pace':>4} {'Mid':>5} {'Back':>4}"
              f"  {'Lead':>4} {'Pace':>4} {'Mid':>5} {'Back':>4}"
              f"  {'Win%':>6} {'Plc%':>6}")
    print(header)
    print("-" * len(header))

    for slug in sorted_slugs:
        r = runner_map.get(slug)
        if not r:
            continue
        p1 = _fmt_bucket(pace_pct[slug])
        p2 = _fmt_bucket(mid_pct[slug]) if slug in mid_pct else "   —    —    —    —"
        w = win_pct.get(slug, 0)
        p = place_pct.get(slug, 0)
        print(f"{r.horse:<28}  {p1}  {p2}  {w*100:>5.1f}% {p*100:>5.1f}%")

    # Positional Probability Table (top-3 tracking)
    p1t3 = results.get('phase1_top3_pct', {})
    p2t3 = results.get('phase2_top3_pct', {})
    if p1t3:
        print()
        _rule("   POSITIONAL PROBABILITY TABLE")
        sorted_by_win = sorted(win_pct.keys(), key=lambda s: -win_pct.get(s, 0))
        header2 = f"{'Horse':<28} {'800m Lead':>9} {'800m Top3':>9} {'400m Lead':>9} {'400m Top3':>9} {'Win%':>6}"
        print(header2)
        print("-" * len(header2))
        for slug in sorted_by_win:
            r = runner_map.get(slug)
            if not r:
                continue
            lead_800 = pace_pct[slug].get('leader', 0) if slug in pace_pct else 0
            t3_800 = p1t3.get(slug, 0)
            lead_400 = mid_pct[slug].get('leader', 0) if slug in mid_pct else 0
            t3_400 = p2t3.get(slug, 0)
            w = win_pct.get(slug, 0)
            print(f"{r.horse:<28} {lead_800*100:>8.1f}% {t3_800*100:>8.1f}% {lead_400*100:>8.1f}% {t3_400*100:>8.1f}% {w*100:>5.1f}%")


# ---------------------------------------------------------------------------
# Section 3: Robustness Analysis
# ---------------------------------------------------------------------------

def _robustness_label(slug: str, results: Dict) -> str:
    """Classify a runner as Robust / Conditional / Fragile."""
    win_pct = results['win_pct'][slug]
    rates = results['weight_win_rates'].get(slug, {})
    band_rates = [rates.get('low', 0), rates.get('mid', 0), rates.get('high', 0)]

    # Robust: wins >20% regardless of weight band
    if all(r >= ROBUST_WIN_THRESHOLD for r in band_rates if r > 0):
        if win_pct >= ROBUST_WIN_THRESHOLD:
            return "ROBUST"

    # Fragile: high variance across bands
    import numpy as np
    variance = float(np.std(band_rates))
    if variance >= FRAGILE_VARIANCE_THRESHOLD:
        return "FRAGILE"

    return "CONDITIONAL"


def _section_robustness(results: Dict, race_info: RaceInfo):
    runner_map = {r.slug: r for r in race_info.runners}
    win_pct = results['win_pct']
    sorted_slugs = sorted(win_pct.keys(), key=lambda s: -win_pct[s])

    _rule("4. ROBUSTNESS ANALYSIS")
    _print("  Robust = wins >20%+ across all weight scenarios")
    _print("  Conditional = wins depend on a specific race shape")
    _print("  Fragile = win% collapses outside a narrow weight band")
    _print()

    for slug in sorted_slugs:
        r = runner_map.get(slug)
        if not r:
            continue
        if win_pct[slug] < 0.03:
            continue  # skip very unlikely runners
        label = _robustness_label(slug, results)
        rates = results['weight_win_rates'].get(slug, {})
        band_str = (
            f"Low-gate {rates.get('low', 0) * 100:.0f}% / "
            f"Mid-gate {rates.get('mid', 0) * 100:.0f}% / "
            f"High-gate {rates.get('high', 0) * 100:.0f}%"
        )
        label_fmt = label
        if _RICH:
            colour = {'ROBUST': 'green', 'CONDITIONAL': 'yellow', 'FRAGILE': 'red'}.get(label, '')
            _print(f"  [{colour}]{label:<12}[/{colour}] {r.horse:<28}  {band_str}")
        else:
            print(f"  {label:<12}  {r.horse:<28}  {band_str}")


# ---------------------------------------------------------------------------
# Section 4: Key Factors Summary
# ---------------------------------------------------------------------------

def _section_key_factors(results: Dict, all_features: Dict, race_info: RaceInfo):
    _rule("5. KEY FACTORS")

    import numpy as np

    slugs = [r.slug for r in race_info.runners]
    feature_names = [
        'gate_speed', 'finishing_speed', 'mile_rate_trend', 'venue_win_rate',
        'barrier_score', 'driver_venue_rate', 'class_relativity', 'freshness',
        'last_800m_pos', 'width_penalty',
    ]

    # Measure spread of each feature across field (std dev) as a proxy for discriminating power
    feat_spread = {}
    for feat in feature_names:
        vals = [all_features.get(s, {}).get(feat, 0.0) for s in slugs]
        feat_spread[feat] = float(np.std(vals))

    sorted_feats = sorted(feat_spread.items(), key=lambda x: -x[1])

    labels = {
        'gate_speed': 'Gate speed / early position',
        'finishing_speed': 'Finishing speed (last 400m)',
        'mile_rate_trend': 'Form trend (improving/declining)',
        'venue_win_rate': 'Track-specific win rate',
        'barrier_score': 'Barrier / draw advantage',
        'driver_venue_rate': 'Driver venue record',
        'class_relativity': 'Class level vs field',
        'freshness': 'Freshness / days since last run',
        'last_800m_pos': '800m run-home momentum',
        'width_penalty': 'Lane width (wide runner discount)',
    }

    _print("  Features ranked by discriminating power in this race:\n")
    for rank, (feat, spread) in enumerate(sorted_feats[:6], 1):
        bar = "█" * max(1, int(spread * 8))
        label = labels.get(feat, feat)
        if _RICH:
            _print(f"  {rank}. [cyan]{label:<38}[/cyan]  {bar}")
        else:
            print(f"  {rank}. {label:<38}  {bar}")


# ---------------------------------------------------------------------------
# Section 5: Analyst Narrative
# ---------------------------------------------------------------------------

def _section_narrative(
    results: Dict,
    race_info: RaceInfo,
    all_features: Dict,
    warnings: List[str],
):
    _rule("6. ANALYST NARRATIVE")

    runner_map = {r.slug: r for r in race_info.runners}
    win_pct = results['win_pct']
    place_pct = results['place_pct']
    pace_pct = results['pace_pct']

    sorted_slugs = sorted(win_pct.keys(), key=lambda s: -win_pct[s])

    # --- Standout selection ---
    top_slug = sorted_slugs[0]
    top = runner_map[top_slug]
    top_wp = win_pct[top_slug]
    top_rob = _robustness_label(top_slug, results)
    top_pace = max(pace_pct[top_slug], key=pace_pct[top_slug].get).replace('_', ' ')

    lines = []

    race_header = f"{race_info.track} Race {race_info.race_no}"
    if race_info.date:
        race_header += f"  ({race_info.date})"
    lines.append(f"Race overview: {race_header}  |  {len(race_info.runners)} runners  |  "
                 f"{race_info.distance_m}m  |  Start: {race_info.start_type}\n")

    lines.append(
        f"STANDOUT: {top.horse} ({top_wp * 100:.1f}% win probability, {top_rob.lower()} selection). "
        f"Projects as {top_pace} runner at the 800m. "
        f"{'Consistent performer across weight scenarios.' if top_rob == 'ROBUST' else 'Win rate varies with race shape — watch pace setup.'}"
    )

    # --- Main danger ---
    if len(sorted_slugs) > 1:
        danger_slug = sorted_slugs[1]
        danger = runner_map[danger_slug]
        danger_wp = win_pct[danger_slug]
        danger_rob = _robustness_label(danger_slug, results)
        danger_pace = max(pace_pct[danger_slug], key=pace_pct[danger_slug].get).replace('_', ' ')
        lines.append(
            f"\nMAIN DANGER: {danger.horse} ({danger_wp * 100:.1f}%). "
            f"Likely to race {danger_pace}. "
            f"{danger_rob} — "
            + ("most dangerous if race develops into a {top_pace} track.".format(top_pace=top_pace)
               if danger_rob == 'CONDITIONAL'
               else "genuine threat regardless of tempo.")
        )

    # --- Race shape prediction ---
    leaders = [
        runner_map[s].horse for s in sorted_slugs
        if pace_pct[s].get('leader', 0) >= 0.25
    ]
    on_pace = [
        runner_map[s].horse for s in sorted_slugs
        if pace_pct[s].get('on_pace', 0) >= 0.30 and pace_pct[s].get('leader', 0) < 0.25
    ]
    if leaders:
        shape = f"Expected pace scenario: {', '.join(leaders)} likely to lead"
        if on_pace:
            shape += f", with {', '.join(on_pace)} on the pace"
        shape += "."
        lines.append(f"\nRACE SHAPE: {shape}")
    else:
        lines.append("\nRACE SHAPE: Contested pace — no clear leader emerges consistently.")

    # --- Fragile selections warning ---
    fragile = [
        runner_map[s].horse for s in sorted_slugs
        if _robustness_label(s, results) == "FRAGILE" and win_pct[s] > 0.08
    ]
    if fragile:
        lines.append(
            f"\nFRAGILE SELECTIONS: {', '.join(fragile)} show elevated win% only under specific "
            f"weight scenarios. Low confidence even if raw win% appears attractive."
        )

    # --- Data gap flags ---
    data_warnings = [w for w in set(warnings) if 'not found' in w.lower() or 'mainland' in w.lower()
                     or 'no recent' in w.lower() or 'never raced' in w.lower()
                     or 'stand-down' in w.lower() or 'suspension' in w.lower()]
    if data_warnings:
        lines.append("\nDATA FLAGS:")
        for w in data_warnings:
            lines.append(f"  • {w}")

    _print()
    for line in lines:
        _print(line)
    _print()


# ---------------------------------------------------------------------------
# Main report entry point
# ---------------------------------------------------------------------------

def generate_report(
    results: Dict,
    race_info: RaceInfo,
    all_features: Dict,
    warnings: List[str],
):
    """Print the full six-section simulation report."""
    track = race_info.track or "Unknown Track"
    race_label = f"Race {race_info.race_no}" if race_info.race_no else ""
    title = f"  MONTE CARLO RACE SIMULATION — {track.upper()} {race_label}  "
    title += f"({results['n_runs']:,} runs)  "

    if _RICH:
        console.rule(f"[bold]{title}[/bold]", style="blue")
    else:
        print("=" * max(72, len(title)))
        print(title)
        print("=" * max(72, len(title)))

    _print()
    _section_probability_table(results, race_info, all_features)
    _print()
    _section_sectionals(results, race_info)
    _print()
    _section_speed_map(results, race_info)
    _print()
    _section_robustness(results, race_info)
    _print()
    _section_key_factors(results, all_features, race_info)
    _print()
    _section_narrative(results, race_info, all_features, warnings)

    if _RICH:
        console.rule(style="blue")
    else:
        print("=" * 72)
