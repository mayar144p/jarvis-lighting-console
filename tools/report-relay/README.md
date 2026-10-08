# Report relay: bug reports without a GitHub account

The desk's **Report a bug** saves a zip and opens a GitHub issue to drag it
into - which needs a GitHub account.  With a relay, the desk shows **Send
report** too: the report goes to the relay, which files the issue on this
repository with its own token and keeps the zip.  The reporter needs nothing.

It is a small Cloudflare Worker (`worker.mjs`, free plan is plenty).

## Set it up once (about 10 minutes)

1. **A token for the relay** - GitHub → Settings → Developer settings →
   Fine-grained tokens → Generate: *Only select repositories* → this one;
   *Repository permissions* → **Issues: Read and write** (nothing else).
2. **The worker** - with [Node.js](https://nodejs.org) installed, in this folder:
   ```
   copy wrangler.toml.example wrangler.toml        (Mac/Linux: cp)
   npx wrangler login
   npx wrangler r2 bucket create jarvis-reports
   npx wrangler secret put GITHUB_TOKEN             (paste the token)
   npx wrangler secret put DOWNLOAD_KEY             (any long random text: maintainers use it to download zips)
   npx wrangler secret put RELAY_KEY                (optional: a key every desk must send)
   npx wrangler deploy
   ```
   It prints the relay's address, e.g. `https://jarvis-report-relay.<you>.workers.dev`.
3. **The desks** - in each desk's `.env` (or the desktop app's settings
   folder):
   ```
   REPORT_RELAY=https://jarvis-report-relay.<you>.workers.dev
   REPORT_RELAY_KEY=...                             (only if you set RELAY_KEY)
   ```
   Restart the desk: the report window now has **Send report**.

## What it does, and doesn't

- Files the issue on the right form's fields (light, patch, what happened,
  the desk version), labelled like the forms (`light-bug`, `brand:…`, …).
  Nobody can be @-mentioned from a report's text.
- Keeps the zip in the R2 bucket. The issue links to it; the link needs
  `?key=<DOWNLOAD_KEY>` to download, so a public issue never exposes a
  show file.
- At most 6 reports an hour from one address, 8 MB of zip each (the desk
  says "untick Attach the whole show" when it is bigger).
- Never sees `.env`, keys or passwords: the desk leaves them out of every
  report, as before.
