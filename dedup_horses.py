#!/usr/bin/env python3
"""
dedup_horses.py — Clean tassie_horses.txt by:

1. Removing duplicate slugs that resolve to the same horse
   e.g. 'denny' and 'denny-nz' when 'denny-nz' already has data

2. Removing slugs that returned no data in stride_results.csv
   (trials-only horses, name mismatches, inactive horses)

3. Keeping all slugs that DID return data (the 329 successful ones)

Usage:
    python dedup_horses.py
    python dedup_horses.py --dry-run        # preview only, don't write
    python dedup_horses.py --keep-no-data   # keep slugs even with no results
"""

import argparse
import re
from pathlib import Path

import pandas as pd


HORSES_FILE    = "tassie_horses.txt"
RESULTS_FILE   = "output/claude_data/stride_results.csv"
PROFILES_FILE  = "output/claude_data/stride_profiles.csv"


def normalise_slug(slug: str) -> str:
    """
    Normalise a slug for dedup comparison.
    Strips country suffixes and lowercases.
    e.g. 'denny-nz' → 'denny', 'can-feel-the-fury-nz' → 'can-feel-the-fury'
    """
    slug = slug.strip().lower()
    for suffix in ("-nz", "-gb", "-ire", "-ir", "-us", "-fr", "-de", "-au"):
        if slug.endswith(suffix):
            return slug[: -len(suffix)]
    return slug


def slug_to_display(slug: str) -> str:
    """Convert slug to title-case display name for comparison."""
    parts = slug.split("-")
    if parts and parts[-1].upper() in ("NZ", "GB", "IRE", "IR", "US", "FR", "DE"):
        return " ".join(p.title() for p in parts[:-1]) + " " + parts[-1].upper()
    return " ".join(p.title() for p in parts)


def main():
    ap = argparse.ArgumentParser(description="Deduplicate tassie_horses.txt")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print changes without writing")
    ap.add_argument("--keep-no-data", action="store_true",
                    help="Keep slugs that returned no Stride data (default: remove)")
    args = ap.parse_args()

    horses_path = Path(HORSES_FILE)
    if not horses_path.exists():
        print(f"⚠ {HORSES_FILE} not found")
        return

    # ── Load stride_results to find which slugs returned data ──
    slugs_with_data = set()
    results_path = Path(RESULTS_FILE)
    if results_path.exists():
        df_r = pd.read_csv(results_path, usecols=["Slug"])
        slugs_with_data = set(df_r["Slug"].dropna().unique())
        print(f"  stride_results.csv: {len(slugs_with_data)} unique slugs with data")
    else:
        print(f"  ⚠ {RESULTS_FILE} not found — will only dedup by slug normalisation")

    # Also check profiles for horses with 0 runs (still valid, just inactive)
    slugs_in_profiles = set()
    profiles_path = Path(PROFILES_FILE)
    if profiles_path.exists():
        df_p = pd.read_csv(profiles_path, usecols=["Slug"])
        slugs_in_profiles = set(df_p["Slug"].dropna().unique())
        print(f"  stride_profiles.csv: {len(slugs_in_profiles)} unique slugs")

    slugs_confirmed = slugs_with_data | slugs_in_profiles

    # ── Parse horses file ──
    raw_lines = horses_path.read_text(encoding="utf-8").splitlines()

    kept        = []   # lines to keep
    removed     = []   # lines removed with reason
    seen_norm   = {}   # normalised_slug → first slug seen (for dedup)

    for line in raw_lines:
        stripped = line.strip()

        # Always keep blank lines and comments
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue

        slug = stripped
        norm = normalise_slug(slug)

        # ── Rule 1: duplicate normalised slug ──
        if norm in seen_norm:
            winner = seen_norm[norm]
            # Keep whichever has data; if both or neither, keep the first
            slug_has_data    = slug in slugs_confirmed
            winner_has_data  = winner in slugs_confirmed

            if slug_has_data and not winner_has_data:
                # Replace winner with current slug
                kept = [
                    line if l.strip() == winner else l
                    for l in kept
                ]
                removed.append((winner, f"duplicate of {slug} (swapped — {slug} has data)"))
                seen_norm[norm] = slug
                kept.append(line)
            else:
                removed.append((slug, f"duplicate of {winner} (same horse, different slug format)"))
            continue

        # ── Rule 2: no data returned and not keeping no-data ──
        if not args.keep_no_data and slugs_confirmed and slug not in slugs_confirmed:
            removed.append((slug, "no Stride data (trials-only, inactive, or name mismatch)"))
            continue

        seen_norm[norm] = slug
        kept.append(line)

    # ── Summary ──
    kept_slugs    = [l.strip() for l in kept if l.strip() and not l.strip().startswith("#")]
    removed_slugs = [r[0] for r in removed]

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Results:")
    print(f"  Original slugs:  {sum(1 for l in raw_lines if l.strip() and not l.strip().startswith('#'))}")
    print(f"  Keeping:         {len(kept_slugs)}")
    print(f"  Removing:        {len(removed_slugs)}")

    if removed:
        print(f"\nRemoving {len(removed)} slugs:")
        # Group by reason
        dupes    = [(s, r) for s, r in removed if "duplicate" in r]
        no_data  = [(s, r) for s, r in removed if "no Stride" in r]

        if dupes:
            print(f"\n  Duplicates ({len(dupes)}):")
            for slug, reason in sorted(dupes):
                print(f"    - {slug}  ({reason})")

        if no_data:
            print(f"\n  No data / inactive ({len(no_data)}):")
            for slug, reason in sorted(no_data):
                print(f"    - {slug}")

    if not args.dry_run:
        # Backup original
        backup = horses_path.with_suffix(".txt.bak")
        backup.write_text(horses_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n  Backup saved: {backup}")

        # Write cleaned file
        # Clean up multiple consecutive blank lines
        output_lines = []
        prev_blank = False
        for line in kept:
            is_blank = not line.strip()
            if is_blank and prev_blank:
                continue
            output_lines.append(line)
            prev_blank = is_blank

        horses_path.write_text("\n".join(output_lines), encoding="utf-8")
        print(f"  ✅ {HORSES_FILE} updated — {len(kept_slugs)} slugs")
        print(f"\n  Next step:")
        print(f"    python harness_scraper.py --stride-horses {HORSES_FILE} --out output/claude_data")
    else:
        print(f"\n  Run without --dry-run to apply changes")


if __name__ == "__main__":
    main()
