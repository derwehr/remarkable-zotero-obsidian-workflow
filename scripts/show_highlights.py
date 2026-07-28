#!/usr/bin/env python3
"""Report the highlight annotations a PDF carries, and the text under each.

This is the check that says whether the round trip actually worked. remarks
writes real PDF highlight annotations, which is what Zotero's
File -> Import Annotations reads; a PDF that merely looks highlighted has
nothing for Zotero to import.

Run it against the converted PDFs before they are installed:

    ./scripts/show_highlights.py ~/.cache/remarkable-zotero-sync/out

or against anything else, including a whole directory:

    ./scripts/show_highlights.py ~/papers/some-paper.pdf

Needs PyMuPDF, which remarks already depends on, so the simplest way to run it
is with remarks' own interpreter:

    "$(dirname "$(readlink -f "$(command -v remarks)")")/python" scripts/show_highlights.py ...
"""

import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    sys.exit(
        "PyMuPDF not importable. Run this with remarks' interpreter:\n"
        '  "$(dirname "$(readlink -f "$(command -v remarks)")")/python" '
        + " ".join(sys.argv)
    )


def report(path):
    doc = fitz.open(path)
    found = 0

    for number, page in enumerate(doc, start=1):
        # Extracted per page rather than per annotation: pulling every word on
        # the page once and testing it against each highlight is far cheaper
        # than re-extracting for every highlight.
        words = page.get_text("words")

        for annot in page.annots(types=(fitz.PDF_ANNOT_HIGHLIGHT,)):
            found += 1
            covered = " ".join(
                word[4] for word in words
                if fitz.Rect(word[:4]).intersects(annot.rect)
            )
            # Empty text under a highlight means the annotation landed in the
            # wrong coordinate space: visible in a reader, useless to Zotero.
            print(f"  p{number}: {covered or '(no text under this highlight)'}")

    print(f"{path.name}: {found} highlight annotation(s)\n")
    return found


def main():
    if len(sys.argv) < 2:
        sys.exit(f"usage: {Path(sys.argv[0]).name} <pdf-or-directory> ...")

    pdfs = []
    for arg in sys.argv[1:]:
        path = Path(arg).expanduser()
        pdfs.extend(sorted(path.rglob("*.pdf")) if path.is_dir() else [path])

    if not pdfs:
        sys.exit("no PDFs found")

    total = sum(report(pdf) for pdf in pdfs)
    print(f"{total} highlight annotation(s) across {len(pdfs)} file(s)")

    if not total:
        print(
            "\nNo highlights found. Either nothing was highlighted with the "
            "text highlighter,\nor remarks rendered them without emitting "
            "annotation objects. Handwritten\nmarks are always rendered flat "
            "and never appear here."
        )


if __name__ == "__main__":
    main()
