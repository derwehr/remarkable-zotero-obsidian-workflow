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
        default=os.environ.get("REMARKS_CMD", "python -m remarks"),
        help="How to invoke remarks (default: %(default)s)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Actually replace the PDFs in your library. Without this, only report.",
    )
    return parser.parse_args()


def run(cmd, cwd=None):
    """Run a command, echoing it first so a cron log shows what happened."""
    print(f"  $ {shlex.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(
            f"command failed ({result.returncode}): {shlex.join(cmd)}\n"
            f"{result.stderr.strip()}"
        )
    return result.stdout


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
    bundles = sorted(dest.rglob("*.rmdoc"))
    print(f"  {len(bundles)} document(s)")
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
        return None, f"no file named {stem}.pdf under {zotero_dir}"
    if len(matches) > 1:
        # Same posture as the Drive script: ambiguity means do nothing rather
        # than overwrite whichever copy happened to be found first.
        return None, f"{len(matches)} files named {stem}.pdf, cannot tell which"
    return matches[0], None


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

    changed, skipped, unresolved = [], [], []

    for pdf in converted:
        stem = pdf.name[: -len(SUFFIX)]

        if state.get(stem, {}).get("bundle_sha256") == fingerprints.get(stem):
            skipped.append(stem)
            continue

        target, problem = find_target(zotero_dir, stem)
        if target is None:
            unresolved.append((stem, problem))
            continue

        changed.append((stem, pdf, target))

    print()
    if skipped:
        print(f"Unchanged since last sync: {len(skipped)}")
    for stem, problem in unresolved:
        print(f"  ? {stem}: {problem}")

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
