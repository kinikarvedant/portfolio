# Portfolio

Source for [vedantkinikar.duckdns.org](https://vedantkinikar.duckdns.org): one content file that keeps the website, the GitHub profile and LinkedIn in step.

## How it works

- **`content.toml`** is the only file to edit. It holds the profile, experience, education, skills and projects.
- **`build.py`** turns it into:
  - `site/`: the website. It has the home page, a page per project (or the project's own interactive demo), `robots.txt` and `sitemap.xml`.
  - `out/github-profile/README.md`: the profile README for github.com/kinikarvedant.
  - `out/linkedin.md`: paste-ready LinkedIn sections.
- **Project numbers are never typed by hand.** Highlights use `{placeholders}` that `build.py` fills from each project's results files. A placeholder with no source stops the build.
- **Checks run on every build:** broken local links, and wording the owner doesn't want published.

```bash
python build.py          # build site/ and out/, then check them
python build.py github   # also update repo descriptions, homepages and topics (needs gh auth login)
```

## Updating

- **Add a project:** add a `[[projects]]` block to `content.toml`.
  - Set `demo = "page"` for a generated page, `"copy"` if the repo ships its own `demo/` folder, or `"none"` to link to GitHub only.
  - If the card quotes numbers, add a `facts_*` function in `build.py` that reads them from the repo's results.
- **Hide a project:** set `show = false`.
- **Change wording anywhere:** edit `content.toml` and rebuild. The site, the GitHub profile and the LinkedIn text all pick it up.

The résumé PDF and photo live on the server under `/downloads/` and `/media/`, and are not part of this repo.
