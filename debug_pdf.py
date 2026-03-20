"""Quick debug: print first 80 lines of what pymupdf extracts from the PDF."""
import sys
import fitz

path = sys.argv[1] if len(sys.argv) > 1 else "Hobart Harness 15-03-2026.pdf"
all_lines = []
with fitz.open(path) as doc:
    for i, page in enumerate(doc):
        text = page.get_text()
        lines = text.splitlines()
        print(f"\n--- PAGE {i+1} ({len(lines)} lines) ---")
        for line in lines:
            print(repr(line))
        all_lines.extend(lines)

print(f"\n\nTotal lines: {len(all_lines)}")
