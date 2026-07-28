# Coordn8 Examples Gallery

This folder is the **demo gallery** — the boast asset. Drop the deliverables
Coordn8 generates for real repositories here, and the
[`pages.yml`](../.github/workflows/pages.yml) workflow publishes them to GitHub
Pages so your README can link to a live, clickable demo.

## How to populate it

1. Run Coordn8 on a repo you want to show off (yours, or a well-known OSS project).
2. Copy the generated deliverables here, one subfolder per project:

   ```
   examples/
   ├── index.html            # a simple landing page linking each demo
   ├── <project-a>/
   │   ├── <project-a>.html
   │   └── <project-a>.mp4   # optional; large files → consider a link
   └── <project-b>/
       └── <project-b>.html
   ```

   (Coordn8 auto-names each deliverable after the project, so the files already
   carry the project's slug — just move the `Coordn8/` output here.)

3. Enable Pages: repo **Settings → Pages → Source: GitHub Actions**. Push, and
   the gallery deploys automatically.

## For the LinkedIn / GitHub post

- Record a short screen capture scrolling through a generated page
  (the animated flows read great in motion) and use it as the README hero GIF.
- Link the live Pages URL so people can click into a real interactive page, not a screenshot.

> Tip: video files are large. If you don't want them in git history, host the mp4
> elsewhere and link it, keeping only the self-contained HTML in the repo.
