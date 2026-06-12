# Sample drop folders — for testing from a browser only (no installs)

Used to test RingFinder with real files when you can't run anything locally.

**How to upload from a work PC (browser only):**

1. Open this repo on github.com and switch to the working branch.
2. Navigate into `samples/cads/` → **Add file → Upload files**.
3. Drag your CAD files straight from File Explorer into the page, **Commit changes**.
4. Repeat in `samples/queries/` with the customer-style photos
   (finger shots, Pinterest saves — the "ring requests" folder).
5. Tell Claude the files are up — the full pipeline test runs in the cloud.

Notes:
- GitHub's web uploader takes batches of files; keep each file under 25 MB.
- These folders are for testing only; production ingestion uses `data/inbox/`
  on the mini PC.
