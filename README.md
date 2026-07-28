# reMarkable + Zotero + Obsidian Workflow

This repository contains notes on how I sync my reMarkable with Zotero and Obsidian. It also contains some scripts that I use to automate the process.

## Zotero to reMarkable Sync

### `google-drive-ocamlfuse`

Setup `google-drive-ocamlfuse` to mount your Google Drive on your computer. This will allow you to access your Zotero library files directly from your file system.

### Zotero config

Set up Zotero to sync with your Google Drive:

1. Open Edit -> Settings
2. Go to the `Sync` tab
3. Check `Sync automatically` and `Sync full-text content`
4. Go to the `Advanced` tab
5. Set `Linked Attachment Base Directory` to the path of your mounted Google Drive (e.g., `~/google-drive`)
6. Set the `Data Directory Location` to a local path (e.g., `~/Zotero`)

Set up ZotMoov to automatically export your Zotero library to your Google Drive:

1. Install ZotMoov from [ZotMoov GitHub](https://github.com/wileyyugioh/zotmoov)
2. Open Edit -> Settings
3. Go to the `ZotMoov` tab
4. Check `Automatically Move/Copy Files When Added`

## Google Drive to reMarkable Sync

Enable Google Drive sync on your reMarkable device via https://my.remarkable.com/integrations

## Google Drive Config

When exporting files from the reMarkable to Google Drive, the device will create a copy instead of overwriting the existing file. To avoid clutter, you can set up a script to automatically overwrite the existing files in your Google Drive with the new versions from your reMarkable:

1. Go to [Google Apps Script](https://script.google.com/)
2. Create a new project and paste the following code:

   ```javascript
   // --- Configuration ---------------------------------------------------------

   // Your Zotero folder ID, taken from the folder's Google Drive URL.
   var FOLDER_ID = "YOUR_FOLDER_ID_HERE";

   // Report what would happen without touching any files. Run once with this on
   // and read the execution log before letting the script loose on a library.
   var DRY_RUN = true;

   // Only treat a " (n).pdf" file as an annotated export if it showed up this
   // recently. Keeps the script away from files that have been sitting in the
   // library for a while and merely happen to be named "Author (Year).pdf".
   var MAX_COPY_AGE_HOURS = 24;

   // --- Script ----------------------------------------------------------------

   // Captures the base name, the copy number and the extension separately, so the
   // original extension casing survives: "Paper (1).PDF" belongs to "Paper.PDF".
   var COPY_PATTERN = /^(.*)\s\((\d+)\)(\.pdf)$/i;

   function overwriteAnnotatedFiles() {
     processFolder(DriveApp.getFolderById(FOLDER_ID));
   }

   function processFolder(folder) {
     // Group the candidates by the name each one would overwrite, so that
     // "X (1).pdf" and "X (2).pdf" are resolved against each other instead of
     // racing through the folder iterator in an unspecified order.
     var groups = {};
     var files = folder.getFiles();

     while (files.hasNext()) {
       var file = files.next();
       var match = file.getName().match(COPY_PATTERN);

       if (match) {
         var baseName = match[1] + match[3];
         groups[baseName] = groups[baseName] || [];
         groups[baseName].push({ file: file, copyIndex: parseInt(match[2], 10) });
       }
     }

     for (var baseName in groups) {
       replaceOriginal(folder, baseName, groups[baseName]);
     }

     // Recursively process subfolders (if ZotMoov uses subfolder trees)
     var subfolders = folder.getFolders();
     while (subfolders.hasNext()) {
       processFolder(subfolders.next());
     }
   }

   function replaceOriginal(folder, baseName, copies) {
     var originals = folder.getFilesByName(baseName);

     if (!originals.hasNext()) {
       // Nothing to overwrite, so this is a paper whose name happens to end in a
       // parenthesised number rather than a copy the reMarkable exported.
       return;
     }

     var original = originals.next();

     if (originals.hasNext()) {
       // Drive lets several files in one folder share a name, and there is no way
       // to tell which one the copy belongs to. Leave them alone rather than trash
       // the wrong one. A run interrupted between the rename and the trash below
       // also ends up here, and needs sorting out by hand.
       Logger.log("Skipped, several files named " + baseName + " in " + folder.getName());
       return;
     }

     // Newest export wins, with the highest " (n)" suffix breaking ties.
     copies.sort(function (a, b) {
       return (b.file.getDateCreated() - a.file.getDateCreated()) || (b.copyIndex - a.copyIndex);
     });

     var winner = copies[0];
     var ageHours = (new Date() - winner.file.getDateCreated()) / (1000 * 60 * 60);

     if (ageHours > MAX_COPY_AGE_HOURS) {
       Logger.log("Skipped, " + winner.file.getName() + " is " + Math.round(ageHours) + "h old");
       return;
     }

     // An annotated export is always newer than the file it was made from.
     if (winner.file.getDateCreated() <= original.getLastUpdated()) {
       Logger.log("Skipped, " + winner.file.getName() + " is not newer than " + baseName);
       return;
     }

     if (DRY_RUN) {
       Logger.log("Would replace " + baseName + " with " + winner.file.getName() +
                  " and trash " + copies.length + " file(s)");
       return;
     }

     // Rename before trashing. Drive tolerates the momentary duplicate name, so a
     // failure here leaves the folder holding both files rather than neither.
     winner.file.setName(baseName);
     original.setTrashed(true);

     for (var i = 1; i < copies.length; i++) {
       copies[i].file.setTrashed(true);
     }

     Logger.log("Replaced " + baseName + ", trashed " + copies.length + " superseded file(s)");
   }
   ```

3. Set `FOLDER_ID` to your Zotero folder ID, leave `DRY_RUN` on, and run `overwriteAnnotatedFiles` once. Check the log under `Executions` and confirm every replacement it reports is one you want.
4. Set `DRY_RUN` to `false`, then set up a `Trigger` to run the `overwriteAnnotatedFiles` function periodically (e.g., every hour) to ensure that your Google Drive folder is kept up-to-date with the latest annotated files from your reMarkable.

Replaced originals go to the Drive trash rather than being deleted outright, so there is a 30 day window to recover anything the script gets wrong.
