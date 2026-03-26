"""
backtest.py — Backtesting against known race results.

Known results from Launceston 22 Mar 2026 and other sessions.
Requires real race form data accessible via launceston_22march2026_sim.py.

Targets (spec Section 6.2):
  Winner in top 3:       >60%
  Winner is top pick:    >35%
  All placers in top 5:  >50%

Run from project root:
    python Sim/backtest.py --data output/claude_data
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SIM_DIR)
if SIM_DIR not in sys.path:
    sys.path.insert(0, SIM_DIR)

# ---------------------------------------------------------------------------
# Known results (add more as sessions accumulate)
# ---------------------------------------------------------------------------

KNOWN_RESULTS = [
    # From Launceston 22 Mar 2026
    {
        "track": "Launceston", "date": "2026-03-22", "race": 1,
        "winner": "enjoy-life",
        "placed": ["another-nien", "magic-joe"],
        "form_text": None,  # Will be populated from launceston_22march2026_sim data
    },
    {
        "track": "Launceston", "date": "2026-03-22", "race": 3,
        "winner": "my-mate-luke",
        "placed": ["debt-till-we-part", "miki-sanz"],
        "form_text": None,
    },
    {
        "track": "Launceston", "date": "2026-03-22", "race": 5,
        "winner": "lougle",
        "placed": ["rocky-stride", "mcmayhem"],
        "form_text": None,
    },
    {
        "track": "Launceston", "date": "2026-03-22", "race": 9,
        "winner": "mary-mourne",
        "placed": ["hay-miki", "moveslikealady"],
        "form_text": None,
    },
]

# Target performance thresholds
TARGET_WINNER_IN_TOP3   = 0.60   # >60% of races: winner was in sim's top-3
TARGET_WINNER_TOP_PICK  = 0.35   # >35% of races: winner was sim's top pick
TARGET_PLACERS_IN_TOP5  = 0.50   # >50% of races: all placed horses in sim's top-5


def _load_launceston_form(data_dir: str) -> dict:
    """Attempt to load Launceston 22 Mar 2026 form data from file.

    Returns {race_no: form_text} if the file exists.
    """
    form_file = os.path.join(ROOT_DIR, 'Sim', 'launceston_22march2026_sim.txt')
    if not os.path.exists(form_file):
        return {}

    # Parse by race sections — look for "R<N>" markers
    try:
        with open(form_file, encoding='utf-8', errors='replace') as fh:
            content = fh.read()
        import re
        sections = re.split(r'\n(?=R\d+\s)', content)
        result = {}
        for section in sections:
            m = re.match(r'R(\d+)', section.strip())
            if m:
                result[int(m.group(1))] = section.strip()
        return result
    except Exception:
        return {}


def run_backtest(data_dir: str, n_runs: int = 5000) -> dict:
    """Run backtest against KNOWN_RESULTS.

    Args:
        data_dir: Path to output/claude_data
        n_runs:   Simulation runs per race (smaller for backtest speed)

    Returns dict with:
        winner_in_top3:     fraction
        winner_top_pick:    fraction
        all_placers_in_top5: fraction
        n_tested:           races successfully simulated
        n_skipped:          races skipped (no form data)
        worst_misses:       list of dicts for races where winner not in top-5
    """
    from form_parser import parse_form, RaceInfo, Runner
    from feature_extractor import DataLoader, extract_all_features
    from race_sim import run_simulation

    data = DataLoader(data_dir)
    lnct_forms = _load_launceston_form(data_dir)

    winner_in_top3   = 0
    winner_top_pick  = 0
    all_placers_top5 = 0
    n_tested  = 0
    n_skipped = 0
    worst_misses = []

    print(f"\n{'='*68}")
    print(f"  BACKTEST — {len(KNOWN_RESULTS)} known results")
    print(f"  {n_runs:,} simulation runs per race")
    print(f"{'='*68}\n")

    for result in KNOWN_RESULTS:
        track    = result['track']
        date     = result['date']
        race_no  = result['race']
        winner   = result['winner']
        placed   = result['placed']

        print(f"  Race: {track} {date} R{race_no} | Winner: {winner}")

        # Attempt to get form text
        form_text = result.get('form_text')
        if form_text is None and track == 'Launceston' and race_no in lnct_forms:
            form_text = lnct_forms[race_no]

        if not form_text:
            print(f"    → SKIPPED — no form text available\n")
            n_skipped += 1
            continue

        try:
            race_info = parse_form(form_str=form_text, data_dir=data_dir)
            if not race_info.runners:
                print(f"    → SKIPPED — parse returned no runners\n")
                n_skipped += 1
                continue

            all_features, _ = extract_all_features(race_info, data)
            sim = run_simulation(race_info, all_features, n_runs=n_runs)

            # Rank horses by sim win%
            slugs_by_rank = sorted(sim['win_pct'], key=sim['win_pct'].__getitem__, reverse=True)
            top3 = slugs_by_rank[:3]
            top5 = slugs_by_rank[:5]

            # Check winner
            w_top3  = winner in top3
            w_top1  = slugs_by_rank[0] == winner
            all_p5  = all(p in top5 for p in placed)

            if w_top3:
                winner_in_top3 += 1
            if w_top1:
                winner_top_pick += 1
            if all_p5:
                all_placers_top5 += 1

            n_tested += 1

            # Print row
            winner_rank = slugs_by_rank.index(winner) + 1 if winner in slugs_by_rank else 99
            print(f"    Winner rank: #{winner_rank}  |  Top3: {'✓' if w_top3 else '✗'}  "
                  f"Top1: {'✓' if w_top1 else '✗'}  AllPlacersTop5: {'✓' if all_p5 else '✗'}")
            print(f"    Sim top 3: {top3}")

            if not w_top3:
                winner_rank_pct = sim['win_pct'].get(winner, 0) * 100
                worst_misses.append({
                    'race':        f"{track} {date} R{race_no}",
                    'winner':      winner,
                    'winner_rank': winner_rank,
                    'winner_pct':  f"{winner_rank_pct:.1f}%",
                    'sim_top3':    top3,
                    'likely_cause': _guess_miss_cause(winner, race_info, all_features),
                })

        except Exception as e:
            print(f"    → ERROR: {e}\n")
            n_skipped += 1
            continue

        print()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*68}")
    print(f"  BACKTEST RESULTS  ({n_tested} tested, {n_skipped} skipped)")
    print(f"{'='*68}")

    if n_tested == 0:
        print("  No races successfully tested.")
        print(f"  Hint: Ensure form text is available in launceston_22march2026_sim.txt")
        print(f"  or add form_text entries to KNOWN_RESULTS in backtest.py")
        return {
            'winner_in_top3': 0.0, 'winner_top_pick': 0.0,
            'all_placers_in_top5': 0.0, 'n_tested': 0, 'n_skipped': n_skipped,
            'worst_misses': [],
        }

    w3_rate  = winner_in_top3   / n_tested
    w1_rate  = winner_top_pick  / n_tested
    p5_rate  = all_placers_top5 / n_tested

    def _fmt(val, target):
        mark = '✓' if val >= target else '✗'
        return f"[{mark}] {val*100:.0f}% (target: >{target*100:.0f}%)"

    print(f"  Winner in top 3:       {winner_in_top3}/{n_tested}  {_fmt(w3_rate, TARGET_WINNER_IN_TOP3)}")
    print(f"  Winner is top pick:    {winner_top_pick}/{n_tested}  {_fmt(w1_rate, TARGET_WINNER_TOP_PICK)}")
    print(f"  All placers in top 5:  {all_placers_top5}/{n_tested}  {_fmt(p5_rate, TARGET_PLACERS_IN_TOP5)}")

    if worst_misses:
        print(f"\n  Worst misses (winner not in top 3):")
        for m in worst_misses:
            print(f"    {m['race']}: {m['winner']} was #{m['winner_rank']} "
                  f"({m['winner_pct']}) — {m['likely_cause']}")

    print(f"{'='*68}\n")

    return {
        'winner_in_top3':      w3_rate,
        'winner_top_pick':     w1_rate,
        'all_placers_in_top5': p5_rate,
        'n_tested':            n_tested,
        'n_skipped':           n_skipped,
        'worst_misses':        worst_misses,
    }


def _guess_miss_cause(winner: str, race_info, all_features: dict) -> str:
    """Attempt to diagnose why the model missed this winner."""
    feats = all_features.get(winner, {})
    conf  = feats.get('_data_confidence', 1.0)
    visitor = feats.get('_mainland_visitor', 0.0)

    if visitor:
        return "mainland visitor with thin Tasmanian data"
    if conf < 0.4:
        return f"thin data (confidence {conf:.0%})"
    if feats.get('barrier_score', 0.5) < 0.2:
        return "wide barrier — gate clear may not have fired"
    if feats.get('days_since_last_win', 0) < -0.5:
        return "long win drought (DLW decay applied)"
    return "unclear — check feature values manually"


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=os.path.join(ROOT_DIR, 'output', 'claude_data'))
    parser.add_argument('--runs', type=int, default=5000)
    args = parser.parse_args()

    os.chdir(ROOT_DIR)
    run_backtest(args.data, n_runs=args.runs)
