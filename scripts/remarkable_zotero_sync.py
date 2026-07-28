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
        "--push",
        action="store_true",
        help="Also send library PDFs the tablet does not have yet",
    )
    parser.add_argument(
        "--rm-dest",
        default=os.environ.get("RM_DEST", "/"),
        help="Folder on the reMarkable to push new PDFs into (default: %(default)s)",
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

    # Every name the tablet holds, including the ones ignored above, so that
    # pushing does not re-upload a paper that is merely ambiguous or deleted.
    on_device = {bundle.stem for bundle in kept + trashed}

    return bundles, on_device


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


def index_library(zotero_dir):
    """Index every PDF in the library by name, in one pass.

    Walked rather than globbed per document, for two reasons. Hidden
    directories have to be pruned as the walk descends: Google Drive keeps
    deleted files in .Trash, where a paper you threw away still carries the
    exact name of the one you kept, and writing annotations into the trashed
    copy would look like success while the real attachment went untouched.

    The other reason is cost. A library on a FUSE-mounted cloud drive turns
    every directory into a network round trip, so the walk happens once and
    is answered from memory after that.

    The reMarkable document name is the filename ZotMoov created, minus the
    extension, which is what makes matching on the name possible at all.
    """
    print(f"Indexing {zotero_dir}", flush=True)
    index = {}
    for root, dirs, files in os.walk(zotero_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith(".pdf") and not name.startswith("."):
                index.setdefault(name[: -len(".pdf")], []).append(Path(root) / name)
    print(f"  {len(index)} PDF(s)")
    return index


def push_new(library, on_device, dest, install):
    """Send library PDFs the tablet does not already hold.

    Only ever uploads what is missing. A document already on the tablet is
    left alone even when the local file differs, because after a sync it
    differs precisely because the annotations were written into it: pushing
    that back would make the annotated copy the tablet's source PDF, and the
    next sync would render the same annotations onto it a second time.

    Names already in the tablet's trash count as present. Deleting a paper on
    the device is a decision, and re-uploading it every run would undo it.
    """
    outstanding = sorted(
        paths[0] for name, paths in library.items()
        if name not in on_device and len(paths) == 1
    )
    if not outstanding:
        print("\nTablet already has every paper in the library.")
        return

    print(f"\n{'Sending' if install else 'Would send'} {len(outstanding)} new paper(s) to {dest}:")
    for path in outstanding:
        print(f"  <- {path.name}")
        if install:
            run(["rmapi", "put", str(path), dest])


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

    # Check before the download rather than after it: the conversion runs at
    # the end of a transfer that can take minutes, and finding out then that
    # the converter is missing wastes all of it.
    if shutil.which("rmapi") is None:
        sys.exit("rmapi not found on PATH: https://github.com/ddvk/rmapi")

    remarks_bin = shlex.split(args.remarks_cmd)[0]
    if shutil.which(remarks_bin) is None:
        sys.exit(
            f"{remarks_bin} not found.\n"
            "remarks cannot be installed with pip: it pins rmscene to a commit "
            "while its own dependency rmc asks for the branch, and pip refuses "
            "two direct references to one package. Use poetry or nix, per\n"
            "https://github.com/Scrybbling-together/remarks, then point this at "
            "the result with --remarks-cmd or $REMARKS_CMD."
        )

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

    bundles, on_device = download_bundles(args.rm_folder, downloads)

    # Hash the source bundle rather than the converted PDF: the question is
    # whether the annotations changed, not whether remarks renders identically
    # between versions.
    fingerprints = {bundle.stem: sha256(bundle) for bundle in bundles}
    library = index_library(zotero_dir)

    # Decide what is worth converting before converting it. Notebooks and the
    # guides reMarkable ships with can never match a library file, and each one
    # still costs a full render, which for a notebook holding typed text means
    # driving headless Chrome a page at a time.
    pending, skipped, missing, ambiguous = [], [], [], []

    for bundle in bundles:
        stem = bundle.stem
        if state.get(stem, {}).get("bundle_sha256") == fingerprints[stem]:
            skipped.append(stem)
            continue
        targets = library.get(stem, [])
        if not targets:
            missing.append(stem)
        elif len(targets) > 1:
            ambiguous.append((stem, len(targets)))
        else:
            pending.append((bundle, targets[0]))

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
            for stem in missing:
                print(f"  - {stem}")
    for stem, count in ambiguous:
        print(f"  ? {stem}: {count} files named {stem}.pdf, cannot tell which")

    changed = []
    if pending:
        unpack([bundle for bundle, _ in pending], xochitl_dir)
        produced = {
            pdf.name[: -len(SUFFIX)]: pdf
            for pdf in convert(args.remarks_cmd, xochitl_dir, out_dir)
        }
        for bundle, target in pending:
            pdf = produced.get(bundle.stem)
            if pdf is None:
                # remarks names its output from the document's visibleName,
                # which should equal the name rmapi gave the bundle. Say so if
                # it did not, rather than reporting a document as synced that
                # never converted.
                print(f"  ? {bundle.stem}: remarks produced no output")
                continue
            changed.append((bundle.stem, pdf, target))

    if changed:
        print(f"\n{'Installing' if args.install else 'Would install'} {len(changed)} document(s):")
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
        if args.install:
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    else:
        print("No new annotations to install.")

    if args.push:
        push_new(library, on_device, args.rm_dest, args.install)

    if not args.install:
        print("\nNothing was changed. Re-run with --install to apply.")
    elif changed:
        print(
            "\nIn Zotero, open each item above and use File -> Import Annotations.\n"
            "Zotero strips annotations from the PDF as it imports them, so a document\n"
            "you have already imported and then annotated again will arrive carrying\n"
            "its full history. Delete that item's existing Zotero annotations before\n"
            "re-importing, or you will get duplicates of everything you kept."
        )


if __name__ == "__main__":
    main()
