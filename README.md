# reMarkable + Zotero + Obsidian Workflow

Notes on how I move papers between Zotero and my reMarkable, and how the
highlights I make while reading get back into Zotero as real annotations.

Everything runs over reMarkable's own cloud via [rmapi](https://github.com/ddvk/rmapi).
There is no Google Drive in this workflow. The device's Drive export flattens
annotations into the page, so the highlighted text is gone before the file
leaves the tablet; the cloud bundles still carry the scene data it came from.

```
Zotero + ZotMoov  ->  a local folder
      |  rmapi put
      v
reMarkable cloud  ->  tablet, read and annotate
      |  rmapi get
      v
     remarks  ->  PDF with real highlight annotations
      |
      v
same local folder  ->  Zotero: File -> Import Annotations
```

## Coming from the Google Drive setup

Delete the Apps Script trigger first, at <https://script.google.com> under
Triggers. That script trashed the unannotated original before renaming the
annotated copy over it, so any run that failed between those two steps left the
file in Drive's trash with nothing live carrying its name, and Zotero reporting
the attachment as missing. On an hourly trigger it keeps getting chances.

Then restore what it took, from Drive's own trash at <https://drive.google.com>
rather than through the mount. To find the casualties, list trashed PDFs that
have no live counterpart:

```sh
comm -23 <(basename -a ~/google-drive/.Trash/*.pdf | sort -u) \
         <(basename -a ~/google-drive/zotero/*.pdf | sort -u)
```

Once the library is a local folder, none of this applies any more.

## Setup

### Zotero

1. Open Edit -> Settings
2. Go to the `Sync` tab and check `Sync automatically`
3. Go to the `Advanced` tab
4. Set `Data Directory Location` to a local path (e.g. `~/Zotero`)
5. Set `Linked Attachment Base Directory` to the folder your papers will live
   in (e.g. `~/papers`)

### ZotMoov

[ZotMoov](https://github.com/wileyyugioh/zotmoov) is what gives you a flat
folder of predictably named PDFs, which is what makes matching a document on
the tablet back to a library item possible at all.

1. Install ZotMoov
2. Open Edit -> Settings, go to the `ZotMoov` tab
3. Set the destination directory to the same folder as the
   `Linked Attachment Base Directory` above
4. Check `Automatically Move/Copy Files When Added`

### rmapi

Use the [ddvk fork](https://github.com/ddvk/rmapi). The original `juruen/rmapi`
is archived and no longer works with the current cloud.

Pair it once. Generate a code at
<https://my.remarkable.com/device/browser/connect> — it must be exactly 8
characters — and run `rmapi ls`, which prompts for it. Tokens are saved to
`~/.config/rmapi/rmapi.conf` and refresh on their own after that.

### remarks

Use the [Scrybbling-together fork](https://github.com/Scrybbling-together/remarks).
Upstream `lucasrla/remarks` stops at reMarkable software 2.15 and cannot read
anything annotated on 3.0 or later.

It cannot be installed with pip: it pins `rmscene` to a commit while its own
dependency `rmc` asks for the branch, and pip rejects two direct references to
one package. Use poetry, as the project documents:

```sh
git clone https://github.com/Scrybbling-together/remarks.git ~/src/remarks
cd ~/src/remarks
poetry config virtualenvs.in-project true --local
poetry install
ln -s ~/src/remarks/.venv/bin/remarks ~/.local/bin/remarks
```

The in-project virtualenv keeps the path stable, so the symlink survives the
venv being rebuilt. Check it with `remarks --version`.

## Syncing

```sh
./scripts/remarkable_zotero_sync.py --zotero-dir ~/papers            # report only
./scripts/remarkable_zotero_sync.py --zotero-dir ~/papers --install  # apply
```

Add `--push` to also send papers the tablet does not have yet. Every option has
an environment variable (`ZOTERO_LINKED_DIR`, `RM_FOLDER`, `RM_DEST`,
`RM_WORK_DIR`, `REMARKS_CMD`), so a cron entry needs no arguments.

Point `--zotero-dir` at the folder ZotMoov writes to, not at a parent of it.
The whole tree is walked to build the name index, and a large tree on a network
filesystem makes that slow.

Nothing is written without `--install`. A plain run downloads, converts, and
reports what it would change.

After a sync, open each listed item in Zotero and use
`File -> Import Annotations`.

To check that the round trip actually produced annotations Zotero can read,
before or after installing them:

```sh
"$(dirname "$(readlink -f "$(command -v remarks)")")/python" \
  scripts/show_highlights.py ~/.cache/remarkable-zotero-sync/out
```

It lists every highlight annotation and the text underneath it. A file that
looks highlighted but reports zero has nothing for Zotero to import.

## Known limitations

**Importing is manual, and re-importing duplicates.** Both follow from the
annotations travelling inside the PDF. A PDF carries every highlight it has
with no identity per highlight, so Zotero cannot tell which ones it imported
before and re-imports all of them; and the import itself is a reader menu
action, so it cannot be scripted. Delete an item's existing Zotero annotations
before re-importing it.

Writing the highlights into Zotero directly would fix both, and drop the need
to overwrite library files at all — the rectangles and text are already in hand
by the time remarks renders them. There is no supported way to do it today.
Annotations appear nowhere in the Zotero Web API v3 documentation, and the
local API is read-only ("Write requests are currently unsupported. Only `GET`
is accepted."), with write support listed as coming in a future version. The
annotation fields are known from community sources and creating them may well
work against undocumented behaviour, but that is a different proposition from
a supported route. Worth revisiting when local API writes ship.

**Handwriting stays flat.** remarks renders scribbles onto the page rather than
as annotation objects. They are visible in the PDF but Zotero cannot do
anything with them. Only text highlights become real annotations.

**Matching is by filename.** A document is matched to a library item by name.
Two library files sharing a name, or a name that changed on one side, are
reported and skipped rather than guessed at.

**rmapi is unofficial.** It speaks a private API, and `juruen/rmapi` is archived
precisely because reMarkable moved that API. The ddvk fork is maintained, but a
firmware update can break this in a way the vendor-supported paths would not.

## Obsidian

remarks also writes Obsidian-compatible markdown of the extracted highlights
next to each converted PDF, under the sync script's work directory
(`~/.cache/remarkable-zotero-sync/out` by default). Nothing currently moves
those into a vault — that part of the workflow is not built yet.
