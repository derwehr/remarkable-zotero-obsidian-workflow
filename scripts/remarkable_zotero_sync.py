#!/usr/bin/env python3
"""Pull annotated documents out of the reMarkable cloud and hand their
highlights to Zotero as real PDF annotations.

    rmapi -> .rmdoc bundles -> remarks -> Zotero's linked attachment

The reMarkable Google Drive export flattens annotations into the page, so no
amount of post-processing recovers the highlighted text from it. The cloud
bundles still carry the v6 .rm scene data that remarks reads, which is why
this takes the long way round instead of reusing the Drive copy.

Prerequisites, both already set up and authenticated:

  rmapi     https://github.com/ddvk/rmapi  (the ddvk fork; juruen's is archived
            and no longer speaks to the current cloud)
  remarks   https://github.com/Scrybbling-together/remarks

Nothing is written into your library unless you pass --install. Without it the
script downloads, converts, and reports what it would replace.

  ./remarkable_zotero_sync.py --rm-folder /Zotero --zotero-dir ~/google-drive
  ./remarkable_zotero_sync.py --rm-folder /Zotero --zotero-dir ~/google-drive --install
"""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SUFFIX = " _remarks.pdf"  # what remarks appends to each converted document


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--rm-folder",
        default=os.environ.get("RM_FOLDER", "/"),
        help="Folder on the reMarkable to sync, e.g. /Zotero (default: the whole tree)",
    )
    parser.add_argument(
        "--zotero-dir",
        default=os.environ.get("ZOTERO_LINKED_DIR"),
        required="ZOTERO_LINKED_DIR" not in os.environ,
        help="Zotero's Linked Attachment Base Directory, i.e. the Google Drive mount",
    )
    parser.add_argument(
        "--work-dir",
        default=os.environ.get("RM_WORK_DIR", "~/.cache/remarkable-zotero-sync"),
        help="Scratch space for downloads and conversions (default: %(default)s)",
    )
    parser.add_argument(
        "--remarks-cmd",
        default=os.environ.get("REMARKS_CMD", "remarks"),
        help="How to invoke remarks (default: %(default)s). Use a full path if "
        "you installed it into a venv that is not on PATH.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Actually replace the PDFs in your library. Without this, only report.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Name every document that has no matching file in the library",
    )
    return parser.parse_args()


def run(cmd, cwd=None):
    """Run a command, echoing it first so a cron log shows what happened.

    Output is deliberately not captured. rmapi reports progress per document
    as it downloads, and prompts for a one-time code once its token expires.
    Capturing either turns a long download into an unreadable pause, and an
    auth prompt into a silent hang on a question you never saw.
    """
    print(f"  $ {shlex.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"command failed ({result.returncode}): {shlex.join(cmd)}")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fresh_dir(path):
    """Rebuild a scratch directory from empty, so a document deleted on the
    tablet does not linger and get re-installed on the next run."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def download_bundles(rm_folder, dest):
    """Fetch .rmdoc bundles from the cloud. `get` (not `geta`) is deliberate:
    geta renders an annotated PDF, which throws away the scene data."""
    print(f"Downloading {rm_folder} from the reMarkable cloud")
    run(["rmapi", "mget", rm_folder], cwd=dest)

    kept, trashed = [], []
    for bundle in sorted(dest.rglob("*.rmdoc")):
        parts = bundle.relative_to(dest).parts
        # Deleted documents come down too, and a paper you threw away can
        # still share its name with a live library file. Annotations from a
        # document you deleted must never land on the copy you kept.
        if len(parts) > 1 and parts[0] == "trash":
            trashed.append(bundle)
        else:
            kept.append(bundle)

    by_name = {}
    for bundle in kept:
        by_name.setdefault(bundle.stem, []).append(bundle)

    # Everything downstream is keyed by document name: remarks names its
    # output after it, and the library lookup matches on it. Two documents
    # sharing a name cannot be told apart, so neither one is safe to use.
    bundles = [group[0] for group in by_name.values() if len(group) == 1]
    duplicates = [name for name, group in by_name.items() if len(group) > 1]

    print(f"  {len(bundles)} document(s)")
    if trashed:
        print(f"  {len(trashed)} in the trash, ignored")
    for name in sorted(duplicates):
        print(f"  ? {name}: {len(by_name[name])} documents share this name, ignored")

    return bundles


def unpack(bundles, xochitl_dir):
    """Unzip every bundle into one directory. remarks globs for *.metadata, so
    a shared directory means a single conversion pass over the whole library."""
    for bundle in bundles:
        with zipfile.ZipFile(bundle) as archive:
            for member in archive.infolist():
                target = (xochitl_dir / member.filename).resolve()
                if not target.is_relative_to(xochitl_dir.resolve()):
                    sys.exit(f"unsafe path in {bundle.name}: {member.filename}")
            archive.extractall(xochitl_dir)


def convert(remarks_cmd, xochitl_dir, out_dir):
    print("Converting annotations")
    run(shlex.split(remarks_cmd) + [str(xochitl_dir), str(out_dir)])
    return sorted(out_dir.rglob(f"*{SUFFIX}"))


def find_target(zotero_dir, stem):
    """Locate the library file a converted document belongs to.

    The reMarkable document name is the filename ZotMoov created, minus the
    extension, which is what makes this match possible at all.
    """
    matches = [
        path
        for path in zotero_dir.rglob(f"{stem}.pdf")
        if path.is_file() and not path.name.startswith(".")
    ]
    if not matches:
        return None, "missing", f"no file named {stem}.pdf under {zotero_dir}"
    if len(matches) > 1:
        # Same posture as the Drive script: ambiguity means do nothing rather
        # than overwrite whichever copy happened to be found first.
        return None, "ambiguous", f"{len(matches)} files named {stem}.pdf"
    return matches[0], None, None


def preserve_original(target, originals_dir):
    """Keep one pristine copy of each file before it is first overwritten.

    remarks always rebuilds from the tablet's stored original, so this is
    belt-and-braces, but it costs one copy and saves a restore from backup.
    """
    keep = originals_dir / target.name
    if not keep.exists():
        originals_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, keep)


def main():
    args = parse_args()

    for tool in ("rmapi",):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} not found on PATH")

    zotero_dir = Path(args.zotero_dir).expanduser().resolve()
    if not zotero_dir.is_dir():
        sys.exit(f"Zotero linked-attachment directory not found: {zotero_dir}")

    work_dir = Path(args.work_dir).expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)
    state_path = work_dir / "state.json"
    originals_dir = work_dir / "originals"

    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    downloads = fresh_dir(work_dir / "downloads")
    xochitl_dir = fresh_dir(work_dir / "xochitl")
    out_dir = fresh_dir(work_dir / "out")

    bundles = download_bundles(args.rm_folder, downloads)
    if not bundles:
        print("Nothing to do.")
        return

    # Hash the source bundle rather than the converted PDF: the question is
    # whether the annotations changed, not whether remarks renders identically
    # between versions.
    fingerprints = {bundle.stem: sha256(bundle) for bundle in bundles}

    unpack(bundles, xochitl_dir)
    converted = convert(args.remarks_cmd, xochitl_dir, out_dir)

    changed, skipped, missing, ambiguous = [], [], [], []

    for pdf in converted:
        stem = pdf.name[: -len(SUFFIX)]

        if state.get(stem, {}).get("bundle_sha256") == fingerprints.get(stem):
            skipped.append(stem)
            continue

        target, kind, problem = find_target(zotero_dir, stem)
        if target is None:
            (ambiguous if kind == "ambiguous" else missing).append((stem, problem))
            continue

        changed.append((stem, pdf, target))

    print()
    if skipped:
        print(f"Unchanged since last sync: {len(skipped)}")
    if missing:
        # Syncing the whole device sweeps up notebooks and ebooks that were
        # never Zotero items, so an unmatched document is the normal case and
        # not worth a line each. Ambiguity below still is: it means a real
        # library file could be overwritten by the wrong document.
        print(f"Not in the library, ignored: {len(missing)}")
        if args.verbose:
            for stem, _ in missing:
                print(f"  - {stem}")
    for stem, problem in ambiguous:
        print(f"  ? {stem}: {problem}, cannot tell which")

    if not changed:
        print("No new annotations to install.")
        return

    verb = "Installing" if args.install else "Would install"
    print(f"\n{verb} {len(changed)} document(s):")
    for stem, pdf, target in changed:
        print(f"  -> {target}")
        if not args.install:
            continue
        preserve_original(target, originals_dir)
        shutil.copy2(pdf, target)
        state[stem] = {
            "bundle_sha256": fingerprints[stem],
            "installed_to": str(target),
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    if not args.install:
        print("\nRe-run with --install to apply.")
        return

    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    print(
        "\nIn Zotero, open each item above and use File -> Import Annotations.\n"
        "Zotero strips annotations from the PDF as it imports them, so a document\n"
        "you have already imported and then annotated again will arrive carrying\n"
        "its full history. Delete that item's existing Zotero annotations before\n"
        "re-importing, or you will get duplicates of everything you kept."
    )


if __name__ == "__main__":
    main()
