# Testing samples — via Google Drive

Test files come from **Google Drive only** (not GitHub uploads).

**How to send test files for a pipeline run:**

1. Put CAD renders and/or customer-style query photos into a Google Drive
   folder. Subfolders like `cads/` and `requests/` keep them sorted, but a
   flat folder is fine.
2. Set the folder's sharing to **"Anyone with the link"** (Viewer is enough
   for read-only pulls; Editor if you want people to drop files in).
3. Send the share link and say which files are CADs vs. queries.

The whole catalog can then be ingested and every query run against it in the
cloud session — no installs, no GitHub.

> Production never uses Drive or GitHub: staff drop files into the mini PC's
> `data/inbox/` folder via File Explorer. These channels are for testing only.

`tools/pull_drive.py` downloads a public Drive folder's images by link — see
that file for usage.
