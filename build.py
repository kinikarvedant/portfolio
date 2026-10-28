"""Build the portfolio from content.toml.

    python build.py            build site/ and out/, then run the checks
    python build.py github     also update each repo's description, homepage and topics (needs `gh auth login`)
    python build.py release    also publish site/ as a zip + checksum on the portfolio repo's latest GitHub release,
                               which the server's deploy script downloads

site/                      the website: home page, project pages, robots.txt, sitemap.xml
out/github-profile/        README.md for github.com/kinikarvedant (the profile repo)
out/linkedin.md            paste-ready LinkedIn sections
"""
import csv
import datetime
import hashlib
import html
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).parent
SITE, OUT = ROOT / "site", ROOT / "out"
WORDS = ROOT.parent / ".hooks" / "words.txt"  # the owner's list of words never to publish; kept outside git
BANNED = re.compile(rf"\b({WORDS.read_text(encoding='utf-8').strip()})\b", re.I) if WORDS.exists() else None
CODE_FILES = re.compile(r"(^README\.md$|^requirements\.txt$|^Dockerfile$|\.py$|\.ya?ml$)")
GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
e = html.escape


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(f)]


# ------------------------------------------------------------------ facts read from each repo's results


def facts_melbourne(p):
    m = {r["method"]: r for r in rows(p / "results/forecast_metrics.csv")}
    return {"wape_model": m["gradient boosting"]["wape_pct"], "wape_naive": m["same hour last week"]["wape_pct"], "wape_4w": m["average of last 4 weeks"]["wape_pct"],
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
        p["live"] = [(label, href, urljoin(p["url"], href)) for label, href in p.get("live", [])]  # href is relative to the project page
    return c


# ------------------------------------------------------------------ assets and icons

ASSETS = {}  # (source, folder) -> site-relative path


def shrink(src, max_w=1400):
    """PNG/JPEG wider than max_w are resized for the web; everything else is published as is."""
    data = src.read_bytes()
    if src.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        return data
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(data))
    if im.width <= max_w:
        return data
    im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    out = BytesIO()
    im.save(out, "JPEG" if src.suffix.lower() in (".jpg", ".jpeg") else "PNG", optimize=True, **({"quality": 85} if src.suffix.lower() in (".jpg", ".jpeg") else {}))
    return out.getvalue()


def asset(src, folder):
    """Copy a file into site/<folder>/ under a content-hashed name. nginx caches css, js and images for a year
    as immutable, so a changed file must get a new name to reach returning visitors."""
    src = Path(src)
    key = (str(src), folder)
    if key not in ASSETS:
        data = shrink(src)
        name = f"{src.stem}.{hashlib.sha256(data).hexdigest()[:10]}{src.suffix}"
        (SITE / folder).mkdir(parents=True, exist_ok=True)
        (SITE / folder / name).write_bytes(data)
        ASSETS[key] = f"{folder}/{name}"
    return ASSETS[key]


ICONS = {  # 24 px line icons drawn with currentColor
    "pin": '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    "signal": '<rect x="8" y="2.5" width="8" height="19" rx="3"/><circle cx="12" cy="7.5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="16.5" r="1.5"/>',
    "layout": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M9 9v11"/>',
    "scan": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16M8 12h8"/>',
    "arrow": '<path d="M4 12h15M13 6l6 6-6 6"/>',
    "plane": '<path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/>',
    "code": '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16"/>',
    "doc": '<path d="M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6"/>',
    "cursor": '<path d="M5 3l14 7-6 2-2 6z"/>',
}


def icon(name, cls=""):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


def chips(items):
    return '<ul class="chips">' + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>"


# ------------------------------------------------------------------ html


def layout(c, title, desc, path, body, depth, active=""):
    up = "../" * depth
    base = up if depth else ""
    p = c["person"]
    url = p["site"] + "/" + path
    nav = "".join(f'<li><a href="{base}#{k}" data-sec="{k}"{" class=\"on\"" if k == active else ""}>{v}</a></li>'
                  for k, v in (("hey", "Hey"), ("work", "Work"), ("story", "Story"), ("chat", "Chat")))
    return f"""<!doctype html>
<html lang="en">
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
<meta name="theme-color" content="#ffffff" />
<link rel="canonical" href="{url}" />
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='50' fill='%23111'/><text x='50' y='64' font-size='42' text-anchor='middle' fill='%23f5c518' font-family='Arial'>VK</text></svg>" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Caveat:wght@500;600&family=Inter+Tight:wght@400;500;600&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="{up}{c['_css']}" />
<script>document.documentElement.classList.add("js")</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="topblur" aria-hidden="true"></div>
<nav class="nav" aria-label="Main">
  <ul>
    <li class="pill" aria-hidden="true"></li>
    <li><a class="mark" href="{base}#hey" aria-label="Back to the top">VK</a></li>
    {nav}
  </ul>
</nav>
<main id="main">
{body}
</main>
<footer>&copy; <span id="yr">2026</span> {e(p['name'])} &middot; {e(p['location'])}</footer>
<script src="{up}{c['_js']}" defer></script>
</body>
</html>
"""


DOODLE = """<svg class="doodle" viewBox="0 0 420 240" aria-hidden="true">
  <path class="d1" d="M12 206 C 38 196, 52 158, 78 166 S 118 196, 140 158 S 172 96, 198 116 S 238 156, 262 104 S 298 46, 328 56"/>
  <path class="d2" d="M354 52 C 354 30, 308 26, 304 52 C 300 78, 354 82, 356 54 C 357 42, 343 31, 327 34"/>
  <path class="d3" d="M286 168 C 306 150, 318 124, 322 92 M311 103 L322 89 L334 103"/>
  <text x="6" y="44">{note}</text>
</svg>"""

# where the skill stickers sit in their tile: (left %, top %, rotation)
STICKER_SPOTS = [(4, 6, -8), (46, 2, 6), (18, 34, 4), (58, 38, -7), (2, 66, 7), (40, 70, -4)]

# collage tile placement on wide screens: (css position, width px, rotation, parallax speed)
COLLAGE_SPOTS = [
    ("left:-3%;top:4%", 400, -6, .6), ("left:30%;top:-2%", 250, -4, -.3), ("right:-4%;top:3%", 380, 5, -.5),
    ("left:-2%;top:40%", 270, 4, -.6), ("right:-3%;top:36%", 360, 7, .8), ("left:2%;top:62%", 420, -5, .5),
    ("right:1%;top:66%", 270, -6, -.4), ("left:36%;top:74%", 330, 3, .35), ("left:62%;top:-1%", 240, 6, .4),
    ("left:58%;top:70%", 250, -3, -.35),
]


def hero(c):
    p, intro, d = c["person"], c["intro"], c["design"]
    letters = "".join('<span class="sp"></span>' if ch == " " else f'<span class="ch" style="--i:{i}">{e(ch)}</span>'
                      for i, ch in enumerate(p["short_name"]))
    return f"""<section class="hero" id="hey" data-spy="hey">
  <div class="wrap">
    <p class="status"><span class="dot" aria-hidden="true"></span>{e(p['open_to'])}</p>
    <p class="intro"><b>{e(intro['hero_lead'])}</b> {e(intro['hero'])}</p>
    <div class="name">
      {DOODLE.format(note=e(d['doodle_note']))}
      <h1 aria-label="{e(p['name'])}"><span aria-hidden="true">{letters}</span></h1>
    </div>
  </div>
</section>"""


def project_card(p):
    href = p["page"] or p["source"]
    imgs = [asset(Path(p["path"]) / s, f"projects/{p['slug']}/img") for s in p.get("cards", [])]
    if imgs:
        shots = f'<div class="shots{" one" if len(imgs) == 1 else ""}">' + "".join(
            f'<figure><img src="{u}" alt="" loading="lazy" /></figure>' for u in imgs) + "</div>"
    elif p.get("tiles"):
        shots = '<div class="shots tiles">' + "".join(
            f'<figure><span class="t-label">Demo site</span><span class="t-name">{e(t)}</span></figure>' for t in p["tiles"]) + "</div>"
    else:
        shots = ""
    fact = f'<p class="fact">{e(p["highlights"][0])}</p>' if p["highlights"] else ""
    return f"""<a class="pcard" href="{href}"{'' if p['page'] else ' rel="noopener"'} style="--tint:{p['glow']}" data-reveal>
  <div class="top"><span class="logo">{icon(p['icon'])}</span><div><h3>{e(p['short'])}</h3><p class="kick">{e(p['kicker'])}</p></div>{icon('arrow', 'arrow')}</div>
  <p class="desc">{e(p['summary'])}</p>
  {fact}
  {shots}
</a>"""


def story(c):
    intro, d = c["intro"], c["design"]
    text = "".join(f"<p>{e(t)}</p>" for t in intro["about"])
    pol = "".join(f"""<figure class="polaroid" style="--r:{r}deg;--bgc:{m['color']}"><div class="ph"><span class="yr">{e(m['when'])}</span><span class="big">{e(m['title'])}</span></div><figcaption class="cap">{e(m['caption'])}</figcaption></figure>"""
                  for m, r in zip(c["milestones"], [-4, 3, -2, 4, -3, 2]))
    stickers = "".join(f'<span class="sticker" style="left:{x}%;top:{y}%;--r:{r}deg">{e(s)}</span>'
                       for s, (x, y, r) in zip(d["stickers"], STICKER_SPOTS))
    exp = "".join(f"""<li><p class="when">{e(x['when'])}</p><p class="role">{e(x['role'])}</p><p class="org">{e(x['org'])} · {e(x['place'])}</p><ul>{''.join(f'<li>{e(t)}</li>' for t in x['points'][:2])}</ul></li>"""
                  for x in c["experience"])
    edu = "".join(f"<li><b>{e(x['degree'])}</b><br />{e(x['school'])} · {e(x['when'])}{' · ' + e(x['note']) if x.get('note') else ''}</li>" for x in c["education"])
    extras = "".join(f"<li>{e(x)}</li>" for x in c["extras"]["certifications"] + c["extras"]["leadership"])
    return f"""<section id="story" data-spy="story">
  <div class="wrap">
    <div class="sec-head" data-reveal><span class="hand">{e(d['story_note'])}</span><h2>My story so far</h2></div>
    <div class="bento">
      <div class="tile text" data-reveal>{text}</div>
      <div class="tile reel" data-reveal style="--d:80ms" role="group" aria-label="Milestones"><div class="track"><div class="set">{pol}</div><div class="set" aria-hidden="true">{pol}</div></div></div>
      <div class="tile stickers" data-reveal role="img" aria-label="Tools I use most: {e(', '.join(d['stickers']))}"><div class="stack">{stickers}{icon('cursor', 'cursor')}<span class="sig">{e(c['person']['short_name'].split()[0])}</span></div></div>
      <div class="tile" data-reveal style="--d:80ms"><p class="label">Experience</p><ul class="tl">{exp}</ul></div>
      <div class="tile" data-reveal><p class="label">Education</p><ul class="plain">{edu}</ul></div>
      <div class="tile" data-reveal style="--d:80ms"><p class="label">Certifications, courses and leadership</p><ul class="plain">{extras}</ul></div>
    </div>
  </div>
</section>"""


def collage(c):
    d = c["design"]
    pool = [(p, s) for rank in (0, 1) for p in c["projects"] for s in p.get("cards", [])[rank:rank + 1]]  # first image of each project, then second ones
    imgs = [(asset(Path(p["path"]) / s, f"projects/{p['slug']}/img"), p["title"]) for p, s in pool[:6]]
    words = list(c["skills"].items())[:4]
    items = []
    for i, (img, title) in enumerate(imgs):
        items.append(f'<figure class="ctile"><img src="{img}" alt="" loading="lazy" /></figure>')
        if i < len(words):
            k, v = words[i]
            items.append(f'<div class="ctile words"><b>{e(k)}</b><span>{e(", ".join(v))}</span></div>')
    tiles = []
    for t, (pos, w, r, speed) in zip(items, COLLAGE_SPOTS):
        tag_end = t.index(">")
        tiles.append(t[:tag_end] + f' style="{pos};--w:{w}px;--r:{r}deg" data-speed="{speed}"' + t[tag_end:])
    return f"""<section class="collage" aria-labelledby="quest" data-spy="story">
  <div class="center" data-reveal><h2 id="quest">{e(d['collage_title'])}</h2><span class="hand sub">{e(d['collage_note'])}</span></div>
  <div class="field">{''.join(tiles)}</div>
</section>"""


def chat(c):
    p, d = c["person"], c["design"]
    return f"""<section class="chat" id="chat" data-spy="chat" aria-labelledby="chat-h">
  <div class="wrap">
    <h2 class="sr" id="chat-h">Get in touch</h2>
    <span class="hand">{e(d['chat_note'])}</span><br />
    <a class="connect" href="mailto:{p['email']}">Connect</a>
    <div class="contactbar" data-reveal>
      <a class="mail" href="mailto:{p['email']}">{icon('plane')}{e(p['email'])}</a>
      <ul class="socials">
        <li><a href="https://github.com/{p['github']}" rel="me noopener" aria-label="GitHub" data-tip="GitHub">{icon('code')}</a></li>
        <li><a href="{p['linkedin']}" rel="me noopener" aria-label="LinkedIn" data-tip="LinkedIn"><b class="in">in</b></a></li>
        <li><a href="{p['resume']}" aria-label="Résumé (PDF)" data-tip="Résumé">{icon('doc')}</a></li>
      </ul>
    </div>
  </div>
</section>"""


def home(c):
    p, intro = c["person"], c["intro"]
    work = f"""<section id="work" data-spy="work">
  <div class="wrap">
    <div class="sec-head" data-reveal><span class="hand">{e(c['design']['work_note'])}</span><h2>Things I've built</h2></div>
    <div class="projects">{''.join(project_card(x) for x in c['projects'])}</div>
  </div>
</section>"""
    body = hero(c) + work + story(c) + collage(c) + chat(c)
    desc = f"{p['name']}: {intro['lede']}"
    return layout(c, f"{p['name']} | {p['title'].capitalize()}", desc[:300], "", body, 0, active="hey")


def project_page(c, p, dest):
    src, up = Path(p["path"]), "../../"
    alts = p.get("image_alts", [])
    figs = "".join(f'<figure><img src="{up}{asset(src / rel, "projects/" + p["slug"] + "/img")}" alt="{e(alts[i] if i < len(alts) else Path(rel).stem.replace("_", " "))}" loading="lazy" /><figcaption>{e(rel)}</figcaption></figure>'
                   for i, rel in enumerate(p.get("images", [])))
    table = ""
    if "table" in p["facts"]:
        head, body, note = p["facts"]["table"]
        th = "".join(f'<th{" class=\"num\"" if i else ""}>{e(h)}</th>' for i, h in enumerate(head))
        tr = "".join("<tr>" + "".join(f'<td{" class=\"num\"" if i else ""}>{e(v)}</td>' for i, v in enumerate(r)) + "</tr>" for r in body)
        table = f'<h2 class="small">Results</h2><div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div><p class="note">{e(note)}</p>'
    tracked = subprocess.run(["git", "-C", str(src), "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True).stdout.split("\n")
    files = sorted(f for f in tracked if f and Path(f).name != "__init__.py" and (CODE_FILES.search(Path(f).name) and f.count("/") <= 1 or f.startswith("results/") and f.endswith(".csv")))
    links = "".join(f'<li><a href="{p["source"]}/blob/{p["branch"]}/{f}" rel="noopener">{e(f)}</a></li>' for f in files[:16])
    pts = "".join(f"<li>{e(h)}</li>" for h in p["highlights"])
    live = "".join(f'<a class="btn dark" href="{e(href)}">{e(label)}</a>' for label, href, _ in p["live"])
    body = f"""<header class="phero" style="--tint:{p['glow']}">
  <div class="wrap">
    <p class="crumbs"><a href="{up}#work">&larr; All projects</a></p>
    <p class="kicker">{e(p['kicker'])}</p>
    <h1>{e(p['title'])}</h1>
    <p class="lede">{e(p['summary'])}</p>
    <div class="btns">{live}<a class="btn{'' if live else ' dark'}" href="{p['source']}" rel="noopener">Source on GitHub</a><a class="btn" href="{p['source']}#readme" rel="noopener">Full write-up</a></div>
  </div>
</header>
<section class="pbody">
  <div class="wrap">
    <div class="pgrid">
      <div class="tile" data-reveal><h2>What it shows</h2><ul class="body">{pts}</ul>{chips(p['stack'])}</div>
      <div class="tile" data-reveal style="--d:80ms"><h2>Files</h2><ul class="files">{links}</ul></div>
    </div>
    <div class="presults">{table}<div class="figs">{figs}</div></div>
  </div>
</section>"""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(layout(c, f"{p['title']} | {c['person']['short_name']}", p["summary"], p["page"], body, 2, active="work"), encoding="utf-8")


# ------------------------------------------------------------------ other platforms


def github_profile(c):
    p = c["person"]
    rows_ = "\n".join(f"| [{x['title']}]({x['url']}) | {x['summary']} | [source]({x['source']})" + "".join(f" · [{l.lower()}]({u})" for l, _, u in x["live"]) + " |" for x in c["projects"])
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
    proj = "\n\n".join(f"**{x['title']}**\nLink: {x['url']}\n" + "".join(f"{l}: {u}\n" for l, _, u in x["live"]) + f"{x['summary']}\n" + "\n".join(f"- {h}" for h in x["highlights"]) for x in c["projects"])
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
    SITE.mkdir(exist_ok=True)
    for old in SITE.iterdir():  # empty it rather than delete it: a local preview server may be running inside
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    ASSETS.clear()
    c["_css"], c["_js"] = asset(ROOT / "static" / "style.css", "static"), asset(ROOT / "static" / "site.js", "static")
    (SITE / "index.html").write_text(home(c), encoding="utf-8")
    for p in c["projects"]:
        dest = SITE / "projects" / p["slug"]
        if p["demo"] == "copy":
            shutil.copytree(Path(p["path"]) / "demo", dest, ignore=shutil.ignore_patterns("*.py", "__pycache__"), dirs_exist_ok=True)
        elif p["demo"] == "page":
            project_page(c, p, dest)
            if (Path(p["path"]) / "demo").is_dir():  # an interactive demo shipped by the repo, served next to the write-up
                shutil.copytree(Path(p["path"]) / "demo", dest / "demo", ignore=shutil.ignore_patterns("*.py", "__pycache__"), dirs_exist_ok=True)
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
        cmd = [GH, "repo", "edit", f"{c['person']['github']}/{p['repo']}", "--description", p["description"], "--homepage", p["url"]]
        for t in p["topics"]:
            cmd += ["--add-topic", t]
        subprocess.run(cmd, check=True)
        print("updated", p["repo"])


def release(c):
    """Zip site/, write its SHA-256, and publish both as the portfolio repo's latest release."""
    z, sha = OUT / "portfolio-site.zip", OUT / "portfolio-site.zip.sha256"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for p in sorted(SITE.rglob("*")):
            if p.is_file():
                f.write(p, p.relative_to(SITE).as_posix())
    sha.write_text(f"{hashlib.sha256(z.read_bytes()).hexdigest()}  portfolio-site.zip\n", encoding="utf-8", newline="\n")
    tag = "site-" + datetime.datetime.now().strftime("%Y%m%d-%H%M")
    subprocess.run([GH, "release", "create", tag, str(z), str(sha), "--repo", f"{c['person']['github']}/portfolio",
                    "--title", tag, "--notes", f"Built site for {c['person']['site']}", "--latest"], check=True)
    print("released", tag, sha.read_text().split()[0])


if __name__ == "__main__":
    content = build()
    issues = check(content)
    print("\n".join(issues) or f"ok: {sum(1 for _ in SITE.rglob('*.html'))} pages, {len(content['projects'])} projects")
    if issues:
        sys.exit(1)
    if sys.argv[1:] == ["github"]:
        sync_github(content)
    if sys.argv[1:] == ["release"]:
        release(content)
