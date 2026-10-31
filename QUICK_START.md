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
- Double-click a song to play it and queue the current visible song list in its sorted order, beginning at that song. Enable Shuffle before starting a new list. Crossfade 0 disables crossfading. Speed and equalizer affect playback, not the files.
- **Export playlist** and **Export MP3s** use selected songs; if none are selected, they use the full visible list. To export a whole category, select the category, clear the search, and click **Clear selection**. MP3 file exports go to a destination outside the master music folder and include their own portable playlist.
- Use **Library → Back up category database** to make a consistent SQLite snapshot. This backs up categories and the metadata cache, not the MP3 files themselves.

# Moving from D: to C: and mirroring with FreeFileSync

1. Close the app and any other application modifying the music files.
2. Copy or move the complete master music folder from D: to C:, including `.jbs-mp3-manager` and everything inside it. That companion folder contains all virtual categories. Do not copy only the MP3s.
3. Launch the app and choose **Open library** at the new C: location. Its previous location may still appear until you choose the new one. Relative song references and stable IDs preserve category memberships.
4. Verify the song/category counts and play a few tracks from C:.
5. Configure your FreeFileSync mirror from the C: master folder to the D: backup folder. Include `.jbs-mp3-manager`; check that your filters do not exclude it. Use the C: library as the working copy.
6. Run the mirror with the manager closed. SQLite uses a single main database with a rollback journal, and closing the app gives FreeFileSync a consistent complete folder to copy. Do not run the C: and D: copies as independently edited libraries and expect category changes to merge.

Opening the mirrored folder directly also works if you later need to restore it. The application stores the last opened location in local desktop settings, but the library database contains relative paths.
