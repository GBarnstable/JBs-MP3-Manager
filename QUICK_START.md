# First run

1. Keep your existing folders intact. For your first trial, copy a few master MP3s and corresponding category folders to a test location.
2. Launch the app and choose **Create library**. Select the folder containing the master MP3s. Leave **Also scan subfolders** unchecked when those subfolders contain category copies.
3. Let the scan finish. All songs initially appear in **Untagged**. Review any duplicate or unreadable-file report.
4. Choose **Import category folders**. Use **Find folders in** to list immediate category folders under a parent, or **Add category folder** for individual folders. Remove any folders that are not categories. The master folder itself is excluded.
5. Choose **Preview matches**. `exact` means a byte-for-byte match. `audio` means the encoded audio matches but the file tags differ. Both can import. `ambiguous`, `unmatched`, and `error` entries need your attention.
6. Choose **Import matched memberships**. The application adds the virtual categories and memberships without modifying the source MP3s. It saves JSON and CSV reports under `.jbs-mp3-manager/reports` in the master folder.
7. Inspect each category and Untagged. For unmatched copies missing from the master folder, copy the desired originals into the master folder, rescan, and preview/import again. For ambiguous duplicate masters, decide which master to keep, remove redundant copies yourself, rescan, and import again. An unrelated re-encoded version can be categorized manually.
8. Only delete old category folders after every desired song and membership is accounted for. The app never deletes them for you.

# Everyday use

- Drop new MP3s into the master folder. They appear in Untagged after detection or a manual **Rescan**. Choose one or more songs and **Categorize**. Check categories and choose Add or Remove. A song with no memberships returns to Untagged automatically.
- Create categories with **+ Category**. Rename/delete the category selected in the sidebar. Deleting a category leaves the songs in place.
- Select songs and choose **Edit metadata**. Only checked fields are written. A checked blank value clears a field. A batch edit starts with no fields checked.
- Highlight one or more songs and click **Play selection** to queue only those songs, including when Shuffle is enabled. With no highlights, **Play view** queues the visible list. **Play category** (or double-clicking a song) queues the whole category, beginning at the highlighted song. Enable Shuffle before starting a new list. Crossfade 0 disables overlap; other values set the overlap in seconds. The next decoder is prepared before the outgoing song fades. Speed and equalizer affect playback, not the files. **Reset to flat** bypasses the equalizer and restores the original level.
- **Export playlist** and **Export MP3s** first ask what to export. Choose **A category**, **Choose songs from the current category** (check individual songs), or **Multiple categories**. The category option defaults to the current category and always exports the whole category, regardless of the currently playing or highlighted song. Multiple categories can be combined with each song once, or exported separately with the correct category names. A song shared by two categories is intentionally copied into both when exporting separate folders.
- Enter a playlist or **New folder name** in the dialog. Playlists go under `<music folder>/Exports/Playlists`; MP3 copies go under `<music folder>/Exports/<new folder>`. Folders are created automatically. Separate categories get their own named subfolders/playlists inside the named export. Repeated exports get a new name rather than overwriting old ones. MP3 exports include a playlist of the exported copies. Exports is excluded from all library scans, even recursive scans, so copies never become new Untagged songs.
- Use **Library → Back up category database** to make a consistent SQLite snapshot. This backs up categories and the metadata cache, not the MP3 files themselves.

# Resolve ambiguous imports and duplicate masters (0.3)

1. Open your existing library and click **Review issues…** beside Rescan, or **Library → Review import issues / duplicates**. After a new import, **Review unresolved issues** opens the same screen.
2. The latest completed import report loads automatically. If needed, click **Choose another import report** and select its `import-….json` file. The **Import issues** tab lists each unresolved copy, all candidate filenames, and any previous resolution.
3. In **Duplicates**, choose **File to keep** for each group you want to merge. Leave a group on **Skip** to leave it alone. Click a row to compare metadata and see the complete combined category list. The chosen file's filename and tags remain unchanged.
4. Check the summary, then click **Back up & merge selected groups**. The app verifies the selected files' encoded audio, backs up the database, archives extra MP3s, transfers all existing memberships, and applies pending categories from the saved report. It does not need to read the original category copies or repeat the full import.
5. The refreshed review shows resolved counts and remaining groups. Redundant library records are removed, so archived duplicates do not appear in Missing files. The original import report remains a historical snapshot; current resolution status lives in the library database.

The merge creates `.jbs-mp3-manager/duplicate-backups/<batch>/library-before.sqlite3`, `manifest.json`, and `files/0001.mp3` etc. **Open duplicate backups** opens that location. The manifest maps the numbered files to their original relative filenames and records the kept copy and categories. Keep the backups until you have checked the results; they consume disk space until you remove them. Do not restore an old database over newer library work without reviewing what changed afterward. If an operation is interrupted before its database commit, the app restores its moved files on the next open before scanning.

Unmatched or unreadable report entries remain visible with their error details. Add/correct the relevant master file and rescan; if its audio matches the report, a single available master can be selected to apply the pending category without reimporting the folders.

# Moving from D: to C: and mirroring with FreeFileSync

1. Close the app and any other application modifying the music files.
2. Copy or move the complete master music folder from D: to C:, including `.jbs-mp3-manager` and everything inside it. That companion folder contains all virtual categories. Do not copy only the MP3s.
3. Launch the app and choose **Open library** at the new C: location. Its previous location may still appear until you choose the new one. Relative song references and stable IDs preserve category memberships.
4. Verify the song/category counts and play a few tracks from C:.
5. Configure your FreeFileSync mirror from the C: master folder to the D: backup folder. Include `.jbs-mp3-manager`; check that your filters do not exclude it. Use the C: library as the working copy.
6. Run the mirror with the manager closed. SQLite uses a single main database with a rollback journal, and closing the app gives FreeFileSync a consistent complete folder to copy. Do not run the C: and D: copies as independently edited libraries and expect category changes to merge.

Opening the mirrored folder directly also works if you later need to restore it. The application stores the last opened location in local desktop settings, but the library database contains relative paths.

# Updating from 0.1 or 0.2

Close the old app, extract the complete 0.3 download into a new application folder, and launch its EXE. It preserves the existing songs and categories and normally opens your last library automatically. If needed, choose **Open library** and select the existing master music folder. For unresolved duplicate imports, use **Review issues**; you do not need another folder import. Keep the whole `_internal` folder with the new EXE.

If Windows SmartScreen appears for this unsigned new build, click **More info** to reveal **Run anyway** when you intend to run your downloaded build.
