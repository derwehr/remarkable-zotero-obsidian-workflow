This Repository contains notes on how I sync my ReMarakle with Zotero and Obsidian. It also contains some scripts that I use to automate the process.

# Zotero to ReMarkable Sync

## `google-drive-ocaml-fuse`

Setup `google-drive-ocaml-fuse` to mount your Google Drive on your computer. This will allow you to access your Zotero library files directly from your file system.

## Zotero config

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

 ## Google Drive to ReMarkable Sync

 Enable Google Drive sync on your ReMarkable device via https://my.remarkable.com/integrations

## Google Drive Config

When exporting files from the Remarkable to Google Drive, the device will create a copy instead of overwriting the existing file. To avoid clutter, you can set up a script to automatically overwrite the existing files in your Google Drive with the new versions from your ReMarkable:

 1. Go to (Google Scripts)[https://script.google.com/]
 2. Create a new project and paste the following code:
 
 ```javascript
 function processFolder(folder) {
  var files = folder.getFiles();
  
  while (files.hasNext()) {
    var copyFile = files.next();
    var copyName = copyFile.getName();
    
    // Look for files ending with " (1).pdf", " (2).pdf", etc.
    if (copyName.match(/\s\(\d+\)\.pdf$/i)) {
      var originalName = copyName.replace(/\s\(\d+\)\.pdf$/i, ".pdf");
      var originalFiles = folder.getFilesByName(originalName);
      
      if (originalFiles.hasNext()) {
        var originalFile = originalFiles.next();
        
        // Trash the unannotated original file
        originalFile.setTrashed(true);
        
        // Rename the annotated copy to the original file name
        copyFile.setName(originalName);
        
        Logger.log("Successfully replaced: " + originalName);
      }
    }
  }
  
  // Recursively process subfolders (if zotemoov uses subfolder trees)
  var subfolders = folder.getFolders();
  while (subfolders.hasNext()) {
    processFolder(subfolders.next());
  }
}

function overwriteAnnotatedFiles() {
  // Replace with your Zotero folder ID (from the folder's Google Drive URL)
  var FOLDER_ID = "19eLACeSD_2iQmwlhJmHLuRpKdFNTX0KY"; 
  var rootFolder = DriveApp.getFolderById(FOLDER_ID);
  
  processFolder(rootFolder);
}
```
3. Save the script and set up a `Trigger` to run the `overwriteAnnotatedFiles` function periodically (e.g., every hour) to ensure that your Google Drive folder is kept up-to-date with the latest annotated files from your ReMarkable.