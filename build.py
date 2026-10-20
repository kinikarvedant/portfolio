"""Build the portfolio from content.toml.

    python build.py            build site/ and out/, then run the checks
    python build.py github     also update each repo's description, homepage and topics (needs `gh auth login`)

site/                      the website: home page, project pages, robots.txt, sitemap.xml
out/github-profile/        README.md for github.com/kinikarvedant (the profile repo)
out/linkedin.md            paste-ready LinkedIn sections
"""
import csv
import html
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
SITE, OUT = ROOT / "site", ROOT / "out"
WORDS = ROOT.parent / ".hooks" / "words.txt"  # the owner's list of words never to publish; kept outside git
BANNED = re.compile(rf"\b({WORDS.read_text(encoding='utf-8').strip()})\b", re.I) if WORDS.exists() else None
CODE_FILES = re.compile(r"(^README\.md$|^requirements\.txt$|^Dockerfile$|\.py$|\.ya?ml$)")
e = html.escape


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(f)]


# ------------------------------------------------------------------ facts read from each repo's results


def facts_melbourne(p):
    m = {r["method"]: r for r in rows(p / "results/forecast_metrics.csv")}
    return {"wape_model": m["gradient boosting"]["wape_pct"], "wape_naive": m["same hour last week"]["wape_pct"],
            "test_sensors": len(rows(p / "results/forecast_by_sensor.csv"))}


def facts_traffic(p):
    r = rows(p / "results/eval_summary.csv")
    wait = {(x["demand"], x["controller"]): float(x["mean_wait_mean"]) for x in r}
    f = {"ppo_regular": f"{wait['regular', 'PPO']:.2f}", "fixed10_regular": f"{wait['regular', 'Fixed-time 10 s']:.2f}",
         "ppo_random": f"{wait['random', 'PPO']:.2f}", "fixed10_random": f"{wait['random', 'Fixed-time 10 s']:.2f}"}
    head = ["Controller", "Wait, trained pattern (s)", "Wait, random arrivals (s)", "Vehicles through (trained)"]
    body = []
    for c in dict.fromkeys(x["controller"] for x in r):
        reg, ran = next(x for x in r if x["controller"] == c and x["demand"] == "regular"), next(x for x in r if x["controller"] == c and x["demand"] == "random")
        body.append([c, f"{float(reg['mean_wait_mean']):.2f} ± {float(reg['mean_wait_std']):.2f}",
                     f"{float(ran['mean_wait_mean']):.2f} ± {float(ran['mean_wait_std']):.2f}", f"{float(reg['throughput_mean']):.0f}"])
    f["table"] = (head, body, "Mean ± standard deviation over seeds 1-5. Source: results/eval_summary.csv")
    return f


def facts_factory(p):
    last = rows(p / "runs/detect/train3/results.csv")[-1]
    return {"map50": f"{float(last['metrics/mAP50(B)']):.3f}", "map50_95": f"{float(last['metrics/mAP50-95(B)']):.3f}",
            "val_images": len(list((p / "dataset/labels/val").glob("*.txt")))}


FACTS = {"melbourne-footfall": facts_melbourne, "smart-traffic-drl": facts_traffic, "factory-vision": facts_factory}


def load():
    c = tomllib.loads((ROOT / "content.toml").read_text(encoding="utf-8"))
    c["projects"] = sorted((p for p in c["projects"] if p["show"]), key=lambda p: p["order"])
    gh = c["person"]["github"]
    for p in c["projects"]:
        path = Path(p["path"]) if p["path"] else None
        p["facts"] = FACTS[p["slug"]](path) if p["slug"] in FACTS else {}
        p["highlights"] = [h.format(**p["facts"]) for h in p["highlights"]]  # KeyError = a number with no source
        p["source"] = f"https://github.com/{gh}/{p['repo']}"
        p["branch"] = subprocess.run(["git", "-C", str(path), "branch", "--show-current"], capture_output=True, text=True).stdout.strip() if path else "main"
        p["page"] = f"projects/{p['slug']}/" if p["demo"] in ("page", "copy") else ""
        p["url"] = c["person"]["site"] + "/" + p["page"] if p["page"] else p["source"]
    return c


# ------------------------------------------------------------------ html


def layout(c, title, desc, path, body, depth):
    up = "../" * depth
    person = c["person"]
    nav = "".join(f'<li><a href="{"" if depth == 0 else up}#{k}">{k.title()}</a></li>' for k in ("about", "experience", "projects", "skills", "education", "contact"))
    url = person["site"] + "/" + path
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}" />
<meta property="og:title" content="{e(title)}" />
<meta property="og:description" content="{e(desc)}" />
<meta property="og:type" content="website" />
<meta property="og:url" content="{url}" />
<meta name="twitter:card" content="summary" />
<link rel="canonical" href="{url}" />
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>V</text></svg>" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="{up}static/style.css" />
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header>
  <nav class="wrap nav" aria-label="Main">
    <a class="brand" href="{up or './'}#top">{e(person['short_name'].split()[0])} <span>{e(person['short_name'].split()[-1])}</span></a>
    <ul>{nav}</ul>
    <button class="themebtn" id="theme" type="button" aria-label="Toggle colour theme">&#9681;</button>
  </nav>
</header>
<main id="main">
{body}
</main>
<footer class="wrap">
  <span>&copy; <span id="yr">2026</span> {e(person['name'])} &middot; {e(person['location'])}</span>
  <span><a href="https://github.com/{person['github']}" rel="me noopener">GitHub</a> &middot; <a href="{person['linkedin']}" rel="me noopener">LinkedIn</a></span>
</footer>
<script src="{up}static/site.js"></script>
</body>
</html>
"""


def chips(items):
    return '<ul class="chips">' + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>"


def project_card(p):
    btns = []
    if p["page"]:
        btns.append(f'<a class="btn primary" href="{p["page"]}">{"Live demo" if p["demo"] == "copy" else "Project page"}</a>')
    btns.append(f'<a class="btn" href="{p["source"]}" rel="noopener">Source</a>')
    pts = "".join(f"<li>{e(h)}</li>" for h in p["highlights"])
    return f"""<article class="card">
  <p class="kicker">{e(p['kicker'])}</p>
  <h3>{e(p['title'])}</h3>
  <p>{e(p['summary'])}</p>
  {f'<ul class="body">{pts}</ul>' if pts else ''}
  {chips(p['stack'])}
  <div class="cta">{''.join(btns)}</div>
</article>"""


def timeline(items, title_key, where_key):
    out = []
    for i in items:
        pts = "".join(f"<li>{e(x)}</li>" for x in i.get("points", []) + ([i["note"]] if i.get("note") else []))
        out.append(f"""<li><p class="when">{e(i['when'])}</p><h3>{e(i[title_key])}</h3><p class="where">{e(i[where_key])}</p>{f'<ul>{pts}</ul>' if pts else ''}</li>""")
    return '<ul class="tl">' + "".join(out) + "</ul>"


def section(n, sid, title, inner):
    return f'<section id="{sid}"><div class="wrap"><div class="sec-h"><span class="num">{n:02d}</span><h2>{title}</h2></div>{inner}</div></section>'


def home(c):
    p, intro = c["person"], c["intro"]
    for x in c["experience"]:
        x["where"] = f"{x['org']} · {x['place']}"
    portrait = f'<img src="{e(p["photo"])}" alt="{e(p["name"])}" />' if p["photo"] else '<span class="initials">VK</span>'
    hero = f"""<div class="wrap hero" id="top">
  <div>
    <p class="eyebrow"><span class="dot" aria-hidden="true"></span> {e(p['open_to'])}</p>
    <h1>{e(p['name'])}</h1>
    <p class="lede">{e(intro['lede'])}</p>
    <div class="cta">
      <a class="btn primary" href="mailto:{p['email']}">Email me</a>
      <a class="btn" href="{p['resume']}">R&eacute;sum&eacute; (PDF)</a>
      <a class="btn" href="https://github.com/{p['github']}" rel="me noopener">GitHub</a>
      <a class="btn" href="{p['linkedin']}" rel="me noopener">LinkedIn</a>
    </div>
  </div>
  <div class="portrait">{portrait}</div>
</div>"""
    about = '<div class="grid cols-2"><div>' + "".join(f'<p class="muted">{e(t)}</p>' for t in intro["about"]) + '</div><div class="card glance"><h3>At a glance</h3>' + "".join(f"<p><strong>{e(k)}</strong> {e(v)}</p>" for k, v in intro["glance"]) + "</div></div>"
    projects = '<div class="grid cols-2">' + "".join(project_card(x) for x in c["projects"]) + f'<article class="card"><h3>More on GitHub</h3><p>Source for everything above, plus smaller experiments.</p><div class="cta"><a class="btn" href="https://github.com/{p["github"]}" rel="me noopener">github.com/{p["github"]}</a></div></article></div>'
    skills = '<div class="grid cols-2">' + "".join(f'<div class="card"><h3>{e(k)}</h3>{chips(v)}</div>' for k, v in c["skills"].items()) + "</div>"
    ex = c["extras"]
    extras = f'<div class="grid cols-2"><div class="card"><h3>Certifications</h3><ul class="body">{"".join(f"<li>{e(x)}</li>" for x in ex["certifications"])}</ul></div><div class="card"><h3>Leadership</h3><ul class="body">{"".join(f"<li>{e(x)}</li>" for x in ex["leadership"])}</ul></div></div>'
    contact = f"""<div class="contact"><div><h3>Let's talk</h3><p class="muted">{e(p['open_to'])}, in Melbourne. Email is the fastest way to reach me.</p>
<ul class="links"><li><a class="btn" href="mailto:{p['email']}">{e(p['email'])}</a></li><li><a class="btn" href="https://github.com/{p['github']}" rel="me noopener">GitHub</a></li><li><a class="btn" href="{p['linkedin']}" rel="me noopener">LinkedIn</a></li></ul></div>
<a class="btn primary" href="{p['resume']}">Download r&eacute;sum&eacute;</a></div>"""
    body = hero + section(1, "about", "About", about) + section(2, "experience", "Experience", timeline(c["experience"], "role", "where")) \
        + section(3, "projects", "Selected projects", projects) + section(4, "skills", "Skills", skills) \
        + section(5, "education", "Education", timeline(c["education"], "degree", "school")) \
        + section(6, "certifications", "Certifications &amp; leadership", extras) + section(7, "contact", "Contact", contact)
    desc = f"{p['name']}: {intro['lede']}"
    return layout(c, f"{p['name']} | {p['title'].capitalize()}", desc[:300], "", body, 0)


def project_page(c, p, dest):
    src = Path(p["path"])
    (dest / "img").mkdir(parents=True, exist_ok=True)
    figs = []
    for rel in p.get("images", []):
        shutil.copy2(src / rel, dest / "img" / Path(rel).name)
        figs.append(f'<figure><img src="img/{Path(rel).name}" alt="{e(Path(rel).stem.replace("_", " "))}" loading="lazy" /><figcaption>{e(rel)}</figcaption></figure>')
    table = ""
    if "table" in p["facts"]:
        head, body, note = p["facts"]["table"]
        th = "".join(f'<th{" class=\"num\"" if i else ""}>{e(h)}</th>' for i, h in enumerate(head))
        tr = "".join("<tr>" + "".join(f'<td{" class=\"num\"" if i else ""}>{e(v)}</td>' for i, v in enumerate(r)) + "</tr>" for r in body)
        table = f'<h2>Results</h2><div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div><p class="muted">{e(note)}</p>'
    tracked = subprocess.run(["git", "-C", str(src), "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n")
    files = sorted(f for f in tracked if f and Path(f).name != "__init__.py" and (CODE_FILES.search(Path(f).name) and f.count("/") <= 1 or f.startswith("results/") and f.endswith(".csv")))
    links = "".join(f'<li><a href="{p["source"]}/blob/{p["branch"]}/{f}" rel="noopener">{e(f)}</a></li>' for f in files[:16])
    pts = "".join(f"<li>{e(h)}</li>" for h in p["highlights"])
    body = f"""<div class="wrap">
  <p class="crumbs"><a href="../../#projects">&larr; All projects</a></p>
  <div class="phead"><p class="kicker">{e(p['kicker'])}</p><h1>{e(p['title'])}</h1><p class="lede muted">{e(p['summary'])}</p>
    <div class="cta"><a class="btn primary" href="{p['source']}" rel="noopener">Source on GitHub</a><a class="btn" href="{p['source']}#readme" rel="noopener">Full write-up</a></div></div>
  <div class="grid cols-2"><div class="card"><h3>What it shows</h3><ul class="body">{pts}</ul>{chips(p['stack'])}</div><div class="card"><h3>Files</h3><ul class="files">{links}</ul></div></div>
  <section>{table}<div class="grid cols-2">{''.join(figs)}</div></section>
</div>"""
    (dest / "index.html").write_text(layout(c, f"{p['title']} | {c['person']['short_name']}", p["summary"], p["page"], body, 2), encoding="utf-8")


# ------------------------------------------------------------------ other platforms


def github_profile(c):
    p = c["person"]
    rows_ = "\n".join(f"| [{x['title']}]({x['url']}) | {x['summary']} | [source]({x['source']}) |" for x in c["projects"])
    skills = "\n".join(f"- **{k}:** {', '.join(v)}" for k, v in c["skills"].items())
    return f"""## Hi, I'm {p['short_name'].split()[0]}

{c['intro']['lede']}

**{p['open_to']}** in Melbourne.

### Projects

| Project | What it is | Code |
| --- | --- | --- |
{rows_}

### Toolbox

{skills}

### Elsewhere

[Portfolio]({p['site']}) · [LinkedIn]({p['linkedin']}) · [Email](mailto:{p['email']})
"""


def linkedin(c):
    li, p = c["linkedin"], c["person"]
    about = Path(li["about_file"]).read_text(encoding="utf-8").strip()
    exp = "\n\n".join(f"**{x['role']}**, {x['org']} · {x['place']} · {x['when']}\n" + "\n".join(f"- {t}" for t in x["points"]) for x in c["experience"])
    edu = "\n".join(f"- {x['degree']}, {x['school']} · {x['when']}" + (f" ({x['note']})" if x.get("note") else "") for x in c["education"])
    proj = "\n\n".join(f"**{x['title']}**\nLink: {x['url']}\n{x['summary']}\n" + "\n".join(f"- {h}" for h in x["highlights"]) for x in c["projects"])
    feat = "\n".join(f"- {x['title']}: {x['url']}" for x in c["projects"][:2])
    return f"""# LinkedIn: paste-ready (generated from content.toml)

## Headline ({len(li['headline'])} / 220 characters)
{li['headline']}

## About ({len(about)} / 2,600 characters)
{about}

## Experience
{exp}

## Education
{edu}

## Projects section
{proj}

## Featured
- Portfolio: {p['site']}
{feat}

## Contact info
- Website: {p['site']}
- GitHub: https://github.com/{p['github']}
"""


def sitemap(c):
    urls = [c["person"]["site"] + "/"] + [c["person"]["site"] + "/" + p["page"] for p in c["projects"] if p["page"]]
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n"


# ------------------------------------------------------------------ build, check, publish


def build():
    c = load()
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.copytree(ROOT / "static", SITE / "static")
    (SITE / "index.html").write_text(home(c), encoding="utf-8")
    for p in c["projects"]:
        dest = SITE / "projects" / p["slug"]
        if p["demo"] == "copy":
            shutil.copytree(Path(p["path"]) / "demo", dest, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
        elif p["demo"] == "page":
            project_page(c, p, dest)
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {c['person']['site']}/sitemap.xml\n", encoding="utf-8")
    (SITE / "sitemap.xml").write_text(sitemap(c), encoding="utf-8")
    (OUT / "github-profile").mkdir(parents=True, exist_ok=True)
    (OUT / "github-profile" / "README.md").write_text(github_profile(c), encoding="utf-8")
    (OUT / "linkedin.md").write_text(linkedin(c), encoding="utf-8")
    return c


def check(c):
    """Fail on banned words, broken local links or a resume link that the server must provide."""
    problems = []
    for f in [*SITE.rglob("*"), *OUT.rglob("*")]:
        if f.suffix in (".html", ".md", ".txt", ".xml", ".json", ".css", ".js"):
            text = f.read_text(encoding="utf-8")
            problems += [f"{f.relative_to(ROOT)}: banned word '{m.group(0)}'" for m in BANNED.finditer(text)] if BANNED else []
            if f.suffix == ".html":
                for ref in re.findall(r'(?:href|src)="([^"]*)"', text):
                    ref = ref.split("#")[0].split("?")[0]
                    if not ref or re.match(r"[a-z]+:|//|/downloads/|/media/", ref):
                        continue  # external links, and files kept on the server only
                    target = (f.parent / ref).resolve()
                    if not (target.exists() and (target.is_file() or (target / "index.html").exists())):
                        problems.append(f"{f.relative_to(ROOT)}: broken link {ref}")
    return problems


def sync_github(c):
    for p in c["projects"]:
        if not p.get("description"):
            continue
        cmd = ["gh", "repo", "edit", f"{c['person']['github']}/{p['repo']}", "--description", p["description"], "--homepage", p["url"]]
        for t in p["topics"]:
            cmd += ["--add-topic", t]
        subprocess.run(cmd, check=True)
        print("updated", p["repo"])


if __name__ == "__main__":
    content = build()
    issues = check(content)
    print("\n".join(issues) or f"ok: {sum(1 for _ in SITE.rglob('*.html'))} pages, {len(content['projects'])} projects")
    if issues:
        sys.exit(1)
    if sys.argv[1:] == ["github"]:
        sync_github(content)
