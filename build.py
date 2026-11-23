"""Build the portfolio from content.toml.

    python build.py            build site/ and out/, then run the checks
    python build.py github     also update each repo's description, homepage and topics (needs `gh auth login`)
    python build.py release    also publish site/ as a zip + checksum on the portfolio repo's latest GitHub release,
                               which the server's deploy script downloads
    python build.py resume     also write out/resume/: a one-page A4 résumé as PDF (printed by Edge) and DOCX

site/                      the website: home page, project pages, robots.txt, sitemap.xml
out/github-profile/        README.md for github.com/kinikarvedant (the profile repo)
out/linkedin.md            paste-ready LinkedIn sections
"""
import csv
import datetime
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
        p["description"] = p.get("description", "").format(**p["facts"])
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


def asset_bytes(name, data, folder):
    """Publish generated bytes under a content-hashed name, like asset()."""
    stem, dot, ext = name.rpartition(".")
    out = f"{stem}.{hashlib.sha256(data).hexdigest()[:10]}.{ext}"
    (SITE / folder).mkdir(parents=True, exist_ok=True)
    (SITE / folder / out).write_bytes(data)
    return f"{folder}/{out}"


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


def ld(data):
    """A JSON-LD block; '</' is escaped so the JSON can never close the script tag."""
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def person_ld(c):
    p, sk = c["person"], c["skills"]
    return {
        "@type": "Person", "@id": p["site"] + "/#person", "name": p["short_name"], "alternateName": p["name"],
        "url": p["site"] + "/", "jobTitle": p["job_title"], "description": c["intro"]["lede"],
        "affiliation": {"@type": "CollegeOrUniversity", "name": "RMIT University", "url": "https://www.rmit.edu.au"},
        "alumniOf": {"@type": "CollegeOrUniversity", "name": "Vellore Institute of Technology", "url": "https://vit.ac.in"},
        "address": {"@type": "PostalAddress", "addressLocality": "Melbourne", "addressRegion": "VIC", "addressCountry": "AU"},
        "knowsAbout": [x for group in sk.values() for x in group],
        "sameAs": [f"https://github.com/{p['github']}", p["linkedin"]],
    }


def head_meta(c, og, up):
    """Search-engine verification, the link-preview image and the favicons, shared by every page."""
    p, out = c["person"], []
    if p.get("google_verification"):
        out.append(f'<meta name="google-site-verification" content="{e(p["google_verification"])}" />')
    if p.get("bing_verification"):
        out.append(f'<meta name="msvalidate.01" content="{e(p["bing_verification"])}" />')
    if og:
        out += [f'<meta property="og:image" content="{og[0]}" />', '<meta property="og:image:width" content="1200" />',
                '<meta property="og:image:height" content="630" />', f'<meta property="og:image:alt" content="{e(og[1])}" />',
                '<meta name="twitter:card" content="summary_large_image" />', f'<meta name="twitter:image" content="{og[0]}" />']
    else:
        out.append('<meta name="twitter:card" content="summary" />')
    out += [f'<link rel="icon" href="{up}favicon.svg" type="image/svg+xml" />', f'<link rel="icon" href="{up}favicon.ico" sizes="32x32" />',
            f'<link rel="apple-touch-icon" href="{up}apple-touch-icon.png" />']
    if p.get("stats"):  # GoatCounter: no cookies, counts page views; served by the site's own server
        out.append(f'<script data-goatcounter="{p["site"]}/stats/count" async src="{p["site"]}/stats/count.js"></script>')
    return "\n".join(out)


def layout(c, title, desc, path, body, depth, active="", og=None, data=None):
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
<meta property="og:site_name" content="{e(p['short_name'])}" />
<meta property="og:locale" content="en_AU" />
<meta name="author" content="{e(p['short_name'])}" />
<meta name="theme-color" content="#ffffff" />
<link rel="canonical" href="{url}" />
{head_meta(c, og, up)}
<link rel="preload" href="{up}{c['_font']}" as="font" type="font/woff2" crossorigin />
<link rel="stylesheet" href="{up}{c['_css']}" />
{ld(data) if data else ''}
<script>document.documentElement.classList.add("js")</script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="topblur" aria-hidden="true"></div>
<nav class="nav" aria-label="Main">
  <ul>
    <li class="pill" aria-hidden="true"></li>
    <li><a class="mark" href="{base}#hey" aria-label="VK, back to the top">VK</a></li>
    {nav}
  </ul>
</nav>
<main id="main">
{body}
</main>
<footer>
  <ul class="reach">
    <li><a href="mailto:{p['email']}">Email</a></li>
    <li><a href="{p['linkedin']}" rel="me noopener">LinkedIn</a></li>
    <li><a href="https://github.com/{p['github']}" rel="me noopener">GitHub</a></li>
    <li><a href="{p['resume']}">Résumé (PDF)</a></li>
  </ul>
  &copy; <span id="yr">2026</span> {e(p['name'])} &middot; {e(p['location'])}
</footer>
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
    letters = "".join('<span class="sp"> </span>' if ch == " " else f'<span class="ch" style="--i:{i}">{e(ch)}</span>'
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
    data = {"@context": "https://schema.org", "@graph": [
        {"@type": "ProfilePage", "@id": p["site"] + "/#page", "url": p["site"] + "/", "name": p["seo_title"],
         "dateModified": datetime.date.today().isoformat(), "mainEntity": {"@id": p["site"] + "/#person"}},
        person_ld(c),
        {"@type": "WebSite", "@id": p["site"] + "/#site", "url": p["site"] + "/", "name": p["short_name"], "publisher": {"@id": p["site"] + "/#person"}},
    ]}
    return layout(c, p["seo_title"], p["seo_description"], "", body, 0, active="hey", og=c["_og"]["home"], data=data)


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
    me = c["person"]
    (dest / "index.html").write_text(layout(c, f"{p['title']} | {me['short_name']}", p["summary"], p["page"], body, 2, active="work",
                                            og=c["_og"][p["slug"]], data=project_ld(c, p)), encoding="utf-8")


def project_ld(c, p):
    me = c["person"]
    return {"@context": "https://schema.org", "@type": "SoftwareSourceCode", "name": p["title"], "description": p["summary"],
            "url": p["url"], "codeRepository": p["source"], "keywords": ", ".join(p["stack"]),
            "author": {"@type": "Person", "@id": me["site"] + "/#person", "name": me["short_name"], "url": me["site"] + "/"}}


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


# ------------------------------------------------------------------ résumé: one A4 page, PDF (for people) and DOCX (for job portals)

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
RESUME = OUT / "resume"
RESUME_FILE = "Vedant-Kinikar-Resume"


def nobreak(text):
    """No line break inside '150 m', '3.06 s' or 'Jul 2026': join them with a no-break space."""
    text = re.sub(r"(\d) (m|s|h)\b", "\\1\u00a0\\2", text)
    return re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})\b", "\\1\u00a0\\2", text)


def resume_model(c):
    """What the résumé shows, in page order. Project numbers come from the results files, the phone from private.toml."""
    r, p = c["resume"], c["person"]
    private = ROOT / "private.toml"  # never committed: the phone number stays out of the public repo
    if not private.exists():
        sys.exit("private.toml is missing: add  phone = \"04xx xxx xxx\"")
    phone = tomllib.loads(private.read_text(encoding="utf-8"))["phone"]
    shown = {x["slug"]: x for x in c["projects"]}
    contact = [(r["location"], ""), (phone, "tel:+61" + phone.replace(" ", "")[1:]), (p["email"], "mailto:" + p["email"]),
               (p["site"].removeprefix("https://"), p["site"]), (r["linkedin"], "https://www." + r["linkedin"]),
               (f"github.com/{p['github']}", f"https://github.com/{p['github']}")]
    return {
        **r, "contact": contact,
        "education": [{"degree": x["degree"], "school": x["short"], "note": x.get("note", "").replace(" / ", "/"), "coursework": x.get("coursework", ""), "project": x.get("project", ""),
                       "when": x["when"] + (" (expected)" if x.get("expected") else "")} for x in c["education"]],
        "projects": [{**x, "url": shown[x["slug"]]["url"], "bullets": [b.format(**shown[x["slug"]]["facts"]) for b in x["bullets"]]} for x in r["projects"]],
        "also": [{**x, "url": shown[x["slug"]]["url"]} for x in r["also"]],
        "experience": [{**x, "points": x["points"][:2]} for x in c["experience"]],
    }


RESUME_CSS = """
@font-face { font-family: "Inter Tight"; font-weight: 400; src: url(fonts/InterTight-Regular.ttf) format("truetype"); }
@font-face { font-family: "Inter Tight"; font-weight: 600; src: url(fonts/InterTight-SemiBold.ttf) format("truetype"); }
@page { size: A4; margin: 11mm 15mm 10mm; }
html { font: 400 10pt/1.25 "Inter Tight", Arial, sans-serif; color: #111; font-variant-ligatures: none; }
body { margin: 0; }
a { color: inherit; text-decoration: none; }
h1 { display: inline-block; margin: 0; font-size: 24pt; font-weight: 600; letter-spacing: -.025em; line-height: 1.05;
     background: linear-gradient(transparent 64%, #f5c518 64%, #f5c518 90%, transparent 90%); }
.contact, .rights { margin: 3pt 0 0; font-size: 9.5pt; color: #222; }
h2 { display: flex; align-items: center; gap: 5pt; margin: 6.5pt 0 2.5pt; padding-bottom: 1.5pt; border-bottom: .75pt solid #111;
     font-size: 11.5pt; font-weight: 600; letter-spacing: .01em; break-after: avoid; }
h2::before { content: ""; width: 6pt; height: 6pt; border-radius: 1.5pt; background: #f5c518; }
p { margin: 0; }
b, strong { font-weight: 600; }
.entry { margin-top: 2.5pt; break-inside: avoid; }
.entry:first-of-type { margin-top: 0; }
.line { display: flex; justify-content: space-between; gap: 12pt; }
.when { flex: none; color: #444; }
.sub { color: #444; }
ul { margin: 1pt 0 0; padding-left: 11pt; }
li { margin: .6pt 0; padding-left: 1pt; }
.kv { margin-top: 1.2pt; }
.note { margin-bottom: 2pt; color: #444; }
.nw { white-space: nowrap; }
"""


def resume_html(m):
    def link(text, href, bold=False):
        t = f"<b>{e(text)}</b>" if bold else e(text)
        return f'<a href="{e(href)}">{t}</a>' if href else t

    def entry(left, when, below=""):
        return f'<div class="entry"><div class="line"><span>{left}</span><span class="when">{e(when)}</span></div>{below}</div>'

    def bullets(items):
        return "<ul>" + "".join(f"<li>{e(t)}</li>" for t in items) + "</ul>" if items else ""

    def keep(page):  # numbers stay with their units (150 m, 3.06 s); hyphenated words never break, which garbles copied text
        head, body = page.split("<body>", 1)
        body = nobreak(body)
        body = re.sub(r"(?<![\w/.@:-])(\w+(?:[-–]\w+)+)(?![\w/@-])", r'<span class="nw">\1</span>', body)
        return head + "<body>" + body

    def kv(label, text):
        return f'<p class="kv"><b>{e(label)}:</b> {e(text)}</p>'

    edu = "".join(entry(f"<b>{e(x['degree'])}</b> | {e(x['school'])}" + (f" | {e(x['note'])}" if x["note"] else ""), x["when"],
                        (f'<p class="sub">Coursework: {e(x["coursework"])}</p>' if x["coursework"] else "")
                        + (f'<p class="sub">Final-year project: {e(x["project"])}</p>' if x["project"] else "")) for x in m["education"])
    proj = "".join(entry(f"{link(x['name'], x['url'], True)} | {e(x['tech'])}", x["when"], bullets(x["bullets"])) for x in m["projects"])
    also = "".join(f'<p class="kv">{link(x["name"], x["url"], True)} ({e(x["when"])}): {e(x["text"])}</p>' for x in m["also"])
    exp = "".join(entry(f"<b>{e(x['role'])}</b> | {e(x['org'])}, {e(x['place'])}", x["when"], bullets(x["points"])) for x in m["experience"])
    contact = " | ".join(link(t, h) for t, h in m["contact"][:3]) + "<br />" + " | ".join(link(t, h) for t, h in m["contact"][3:])
    return keep(f"""<!doctype html>
<html lang="en-AU">
<head>
<meta charset="utf-8" />
<title>{e(m['name'])} – Résumé</title>
<style>{RESUME_CSS}</style>
</head>
<body>
<h1>{e(m['name'])}</h1>
<p class="contact">{contact}</p>
<p class="rights"><b>Work rights:</b> {e(m['work_rights'])}</p>
<h2>Summary</h2>
<p>{e(m['summary'])}</p>
<h2>Education</h2>
{edu}
<h2>Skills</h2>
{"".join(kv(k, v) for k, v in m['skills'])}
<h2>Projects</h2>
<p class="note">All self-initiated. Code, write-ups and live demos: {link(m['contact'][3][0], m['contact'][3][1])}</p>
{proj}
{also}
<h2>Experience</h2>
{exp}
{kv("Other work", m['other_work'])}
<h2>Certifications, leadership and languages</h2>
{kv("Certifications", m['certifications'])}
{kv("Udemy courses", m['courses'])}
{kv("Leadership at VIT", m['leadership'])}
{kv("Languages", m['languages'])}
</body>
</html>
""")


def resume_docx(m, path):
    """The same content as a plain Word file: one column, real headings, right tab stops for dates, no tables."""
    from docx import Document
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    m = {k: nobreak(v) if isinstance(v, str) else v for k, v in m.items()}
    for key in ("projects", "experience"):
        m[key] = [{**x, **{f: [nobreak(b) for b in x[f]] for f in ("bullets", "points") if f in x}} for x in m[key]]
    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Cm(21), Cm(29.7)
    s.top_margin, s.bottom_margin, s.left_margin, s.right_margin = Cm(1.3), Cm(1.2), Cm(1.6), Cm(1.6)
    width = s.page_width - s.left_margin - s.right_margin
    for name, size in (("Normal", 10), ("Heading 1", 11.5), ("List Bullet", 10)):
        st = d.styles[name]
        st.font.name, st.font.size, st.font.color.rgb = "Calibri", Pt(size), RGBColor(0x11, 0x11, 0x11)
        st.element.rPr.rFonts.set(qn("w:asciiTheme"), "")  # the theme font would override Calibri in headings
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            st.element.rPr.rFonts.set(qn(attr), "Calibri")
        st.paragraph_format.space_before, st.paragraph_format.space_after, st.paragraph_format.line_spacing = Pt(0), Pt(0), 1.0
        lang = st.element.rPr.find(qn("w:lang"))
        if lang is None:
            lang = OxmlElement("w:lang")
            st.element.rPr.append(lang)
        lang.set(qn("w:val"), "en-AU")  # spell-check in Australian English
    h1 = d.styles["Heading 1"]
    h1.font.bold, h1.paragraph_format.space_before, h1.paragraph_format.space_after = True, Pt(7), Pt(2)
    bullet = d.styles["List Bullet"].paragraph_format
    bullet.left_indent, bullet.first_line_indent = Cm(.55), Cm(-.35)  # bullet inside the margin, text hanging
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)  # the file format stores UTC
    cp = d.core_properties
    cp.title, cp.author, cp.last_modified_by, cp.language, cp.comments, cp.created, cp.modified, cp.revision = (
        f"{m['name']} – Résumé", m["name"], m["name"], "en-AU", "", now, now, 1)

    def run(p, text, bold=False, size=None):
        r = p.add_run(text)
        r.bold = bold or None
        if size:
            r.font.size = Pt(size)
        return r

    def link(p, text, url, bold=False):
        rel = p.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
        h, r, rpr, t = OxmlElement("w:hyperlink"), OxmlElement("w:r"), OxmlElement("w:rPr"), OxmlElement("w:t")
        h.set(qn("r:id"), rel)
        if bold:
            rpr.append(OxmlElement("w:b"))
        r.append(rpr)
        t.text = text
        t.set(qn("xml:space"), "preserve")
        r.append(t)
        h.append(r)
        p._p.append(h)

    def heading(text):
        p = d.add_paragraph(text, style="Heading 1")
        border, bottom = OxmlElement("w:pBdr"), OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "111111")):
            bottom.set(qn(k), v)
        border.append(bottom)
        p._p.get_or_add_pPr().append(border)

    def entry_line(parts, when):
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
        for text, bold, url in parts:
            link(p, text, url, bold) if url else run(p, text, bold)
        run(p, "\t" + when)

    def kv(label, text):
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(1.5)
        run(p, label + ": ", True)
        run(p, text)

    p = d.add_paragraph()
    run(p, m["name"], True, 20)
    p = d.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    for i, (text, url) in enumerate(m["contact"]):
        if i == 3:
            p = d.add_paragraph()
        elif i:
            run(p, " | ")
        link(p, text, url) if url else run(p, text)
    p = d.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    run(p, "Work rights: ", True)
    run(p, m["work_rights"])
    heading("Summary")
    d.add_paragraph(m["summary"])
    heading("Education")
    for x in m["education"]:
        entry_line([(x["degree"], True, ""), (" | " + x["school"] + (" | " + x["note"] if x["note"] else ""), False, "")], x["when"])
        if x["coursework"]:
            d.add_paragraph("Coursework: " + x["coursework"])
        if x["project"]:
            d.add_paragraph("Final-year project: " + x["project"])
    heading("Skills")
    for k, v in m["skills"]:
        kv(k, v)
    heading("Projects")
    p = d.add_paragraph()
    run(p, "All self-initiated. Code, write-ups and live demos: ")
    link(p, m["contact"][3][0], m["contact"][3][1])
    for x in m["projects"]:
        entry_line([(x["name"], True, x["url"]), (" | " + x["tech"], False, "")], x["when"])
        for b in x["bullets"]:
            d.add_paragraph(b, style="List Bullet")
    for x in m["also"]:
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(3)
        link(p, x["name"], x["url"], True)
        run(p, f" ({x['when']}): {x['text']}")
    heading("Experience")
    for x in m["experience"]:
        entry_line([(x["role"], True, ""), (f" | {x['org']}, {x['place']}", False, "")], x["when"])
        for b in x["points"]:
            d.add_paragraph(b, style="List Bullet")
    kv("Other work", m["other_work"])
    heading("Certifications, leadership and languages")
    kv("Certifications", m["certifications"])
    kv("Udemy courses", m["courses"])
    kv("Leadership at VIT", m["leadership"])
    kv("Languages", m["languages"])
    d.save(path)
    drop = ("docProps/thumbnail.jpeg", "docProps/app.xml")  # the template's blank preview and its old app details
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist() if n not in drop}
    for n in drop:
        parts["_rels/.rels"] = re.sub(rb'<Relationship [^>]*Target="' + n.encode() + rb'"[^>]*/>', b"", parts["_rels/.rels"])
        parts["[Content_Types].xml"] = re.sub(rb'<Override [^>]*PartName="/' + n.encode() + rb'"[^>]*/>', b"", parts["[Content_Types].xml"])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in parts.items():
            z.writestr(n, data)


def resume(c):
    """Write out/resume/: the HTML, the PDF printed from it by Edge, and the DOCX. Returns the problems found."""
    m = resume_model(c)
    RESUME.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "resume" / "fonts", RESUME / "fonts", dirs_exist_ok=True)
    html_path, pdf, docx = RESUME / "resume.html", RESUME / f"{RESUME_FILE}.pdf", RESUME / f"{RESUME_FILE}.docx"
    html_path.write_text(resume_html(m), encoding="utf-8")
    pdf.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:  # a throwaway profile, so a running Edge window doesn't swallow the job
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
                        f"--print-to-pdf={pdf.resolve()}", html_path.resolve().as_uri()], check=True, capture_output=True, timeout=120)
        for _ in range(240):  # Edge hands the job to a background process and returns early: wait for a finished file
            size = pdf.stat().st_size if pdf.exists() else 0
            time.sleep(.25)
            if size and size == (pdf.stat().st_size if pdf.exists() else 0):
                break
    resume_docx(m, docx)
    problems = [f"{html_path.name}: banned word '{x.group(0)}'" for x in BANNED.finditer(html_path.read_text(encoding="utf-8"))] if BANNED else []
    if not pdf.exists():
        problems.append("Edge did not write the PDF")
    return problems


def last_commit(path):
    return subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%cs"], capture_output=True, text=True).stdout.strip()


def sitemap(c):
    site = c["person"]["site"]
    urls = [(site + "/", last_commit(ROOT))]
    for p in c["projects"]:
        if p["page"]:
            when = last_commit(p["path"]) if p["path"] else last_commit(ROOT)
            urls.append((site + "/" + p["page"], when))
            if p["path"] and p["demo"] == "page" and (Path(p["path"]) / "demo").is_dir():
                urls.append((site + "/" + p["page"] + "demo/", when))
    rows = "".join(f"  <url><loc>{u}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>\n" for u, d in urls)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n"


# ------------------------------------------------------------------ previews, favicons, demo pages

FONT_DIR = ROOT / "resume" / "fonts"


def card_image(c, title, subtitle, picture=None, tint="#f5c518", size=(1200, 630), chips_=()):
    """A link-preview card: soft glow, title and subtitle on the left, a real figure (or stack chips) on the right."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    W, H = size
    k = W / 1200
    im = Image.new("RGB", (W, H), "#ffffff")
    glow = Image.new("RGB", (W, H), "#ffffff")
    ImageDraw.Draw(glow).ellipse((int(W * .45), -int(H * .9), int(W * 1.35), int(H * .55)), fill=tint)
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(int(120 * k))), .55)
    d = ImageDraw.Draw(im)
    bold = lambda s: ImageFont.truetype(str(FONT_DIR / "InterTight-SemiBold.ttf"), int(s * k))
    reg = lambda s: ImageFont.truetype(str(FONT_DIR / "InterTight-Regular.ttf"), int(s * k))

    def wrap(text, font, width):
        lines, line = [], ""
        for word in text.split():
            trial = (line + " " + word).strip()
            if d.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        return lines + [line] if line else lines

    left, text_w = int(64 * k), int((560 if picture or chips_ else 1000) * k)
    y = int(70 * k)
    f_title = bold(64 if len(title) < 40 else 52)
    def clip(lines, n):
        return lines if len(lines) <= n else lines[:n - 1] + [lines[n - 1].rstrip(",.;: ") + "…"]

    for line in clip(wrap(title, f_title, text_w), 3):
        d.text((left, y), line, font=f_title, fill="#111111")
        y += int(f_title.size * 1.12)
    y += int(18 * k)
    f_sub = reg(28)
    for line in clip(wrap(subtitle, f_sub, text_w), 4):
        d.text((left, y), line, font=f_sub, fill="#4a4a4a")
        y += int(f_sub.size * 1.35)
    me = c["person"]
    d.ellipse((left, H - int(78 * k), left + int(16 * k), H - int(62 * k)), fill="#22c55e")
    d.text((left + int(28 * k), H - int(86 * k)), f"{me['short_name']}  ·  Data science, RMIT Melbourne", font=bold(26), fill="#111111")
    d.text((left + int(28 * k), H - int(52 * k)), me["site"].removeprefix("https://"), font=reg(22), fill="#5f5f5f")

    box = (int(660 * k), int(70 * k), W - int(56 * k), H - int(120 * k))
    bw, bh = box[2] - box[0], box[3] - box[1]
    if picture:
        pic = Image.open(picture).convert("RGB")
        pic.thumbnail((bw - int(20 * k), bh - int(20 * k)), Image.LANCZOS)
        card = Image.new("RGB", (pic.width + int(20 * k), pic.height + int(20 * k)), "#ffffff")
        card.paste(pic, (int(10 * k), int(10 * k)))
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cx, cy = box[0] + (bw - card.width) // 2, box[1] + (bh - card.height) // 2
        ImageDraw.Draw(shadow).rounded_rectangle((cx + 6, cy + 14, cx + card.width + 6, cy + card.height + 14), int(18 * k), fill=(0, 0, 0, 60))
        im = Image.alpha_composite(im.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(int(14 * k)))).convert("RGB")
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.width - 1, card.height - 1), int(18 * k), fill=255)
        im.paste(card, (cx, cy), mask)
    elif chips_:
        d = ImageDraw.Draw(im)
        d.rounded_rectangle(box, int(28 * k), fill="#111111")
        y2 = box[1] + int(44 * k)
        for chip in list(chips_)[:6]:
            if y2 + int(54 * k) > box[3] - int(30 * k):
                break
            f = bold(30)
            tw = d.textlength(chip, font=f)
            d.rounded_rectangle((box[0] + int(40 * k), y2, box[0] + int(40 * k) + tw + int(40 * k), y2 + int(54 * k)), int(27 * k), fill=tint)
            d.text((box[0] + int(60 * k), y2 + int(9 * k)), chip, font=f, fill="#111111")
            y2 += int(70 * k)
    out = BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def previews(c):
    """One 1200x630 preview image per page: {key: (absolute url, alt text)}."""
    site, me = c["person"]["site"], c["person"]
    home_pic = next((Path(p["path"]) / p["cards"][0] for p in c["projects"] if p.get("cards")), None)
    out = {"home": (site + "/" + asset_bytes("home.png", card_image(c, me["short_name"], me["seo_description"].split(". ", 1)[-1], home_pic), "og"),
                    f"{me['short_name']}, data science portfolio")}
    for p in c["projects"]:
        pic = Path(p["path"]) / p["cards"][0] if p.get("cards") else None
        data = card_image(c, p["short"], p.get("tagline") or p["summary"], pic, p["glow"], chips_=p["stack"])
        out[p["slug"]] = (site + "/" + asset_bytes(f"{p['slug']}.png", data, "og"), p["title"])
    return out


def social_previews(c):
    """1280x640 images for each repo's GitHub social preview (uploaded by hand in the repo settings)."""
    folder = OUT / "social-previews"
    folder.mkdir(parents=True, exist_ok=True)
    for p in c["projects"]:
        pic = Path(p["path"]) / p["cards"][0] if p.get("cards") else None
        data = card_image(c, p["short"], p.get("tagline") or p["summary"], pic, p["glow"], size=(1280, 640), chips_=p["stack"])
        (folder / f"{p['repo']}.png").write_bytes(data)
    return folder


def favicons():
    """favicon.svg / .ico / apple-touch-icon.png at the site root: the VK monogram."""
    from PIL import Image, ImageDraw, ImageFont
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='50' fill='#111'/>"
           "<text x='50' y='64' font-size='42' text-anchor='middle' fill='#f5c518' font-family='Arial, sans-serif' font-weight='700'>VK</text></svg>")
    (SITE / "favicon.svg").write_text(svg, encoding="utf-8")

    def draw(n, round_=True):
        im = Image.new("RGBA", (n, n), (0, 0, 0, 0) if round_ else "#111111")
        dr = ImageDraw.Draw(im)
        if round_:
            dr.ellipse((0, 0, n - 1, n - 1), fill="#111111")
        f = ImageFont.truetype(str(FONT_DIR / "InterTight-SemiBold.ttf"), int(n * .42))
        dr.text((n / 2, n / 2), "VK", font=f, fill="#f5c518", anchor="mm")
        return im
    draw(256).save(SITE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    draw(180, round_=False).convert("RGB").save(SITE / "apple-touch-icon.png", optimize=True)


def demo_head(c, page, p, depth):
    """A demo page shipped by a project repo gets the preview image, favicons and the owner's name in its title."""
    html_ = page.read_text(encoding="utf-8")
    name = c["person"]["short_name"]
    m = re.search(r"<title>(.*?)</title>", html_, re.S)
    if m and name not in m.group(1):
        html_ = html_.replace(m.group(0), f"<title>{m.group(1).strip()} | {name}</title>", 1)
    if "og:image" not in html_:
        tags = head_meta(c, c["_og"][p["slug"]], "../" * depth)
        html_ = re.sub(r'<meta name="twitter:card"[^>]*>\s*', "", html_)
        html_ = html_.replace("</head>", tags + "\n</head>", 1)
    if p["demo"] == "copy" and "application/ld+json" not in html_:  # the repo's page is the project page itself
        html_ = html_.replace("</head>", ld(project_ld(c, p)) + "\n</head>", 1)
    page.write_text(html_, encoding="utf-8")


# ------------------------------------------------------------------ build, check, publish


def build():
    c = load()
    SITE.mkdir(exist_ok=True)
    for old in SITE.iterdir():  # empty it rather than delete it: a local preview server may be running inside
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    ASSETS.clear()
    fonts = {f.name: asset(f, "static/fonts") for f in sorted((ROOT / "static" / "fonts").glob("*.woff2"))}
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    for name, path in fonts.items():
        css = css.replace(f"url(fonts/{name})", f"url({path.removeprefix('static/')})")
    c["_css"], c["_js"] = asset_bytes("style.css", css.encode("utf-8"), "static"), asset(ROOT / "static" / "site.js", "static")
    c["_font"] = fonts["inter-tight-latin.woff2"]
    c["_og"] = previews(c)
    favicons()
    (SITE / "index.html").write_text(home(c), encoding="utf-8")
    for p in c["projects"]:
        dest = SITE / "projects" / p["slug"]
        if p["demo"] == "copy":
            shutil.copytree(Path(p["path"]) / "demo", dest, ignore=shutil.ignore_patterns("*.py", "__pycache__"), dirs_exist_ok=True)
        elif p["demo"] == "page":
            project_page(c, p, dest)
            if (Path(p["path"]) / "demo").is_dir():  # an interactive demo shipped by the repo, served next to the write-up
                shutil.copytree(Path(p["path"]) / "demo", dest / "demo", ignore=shutil.ignore_patterns("*.py", "__pycache__"), dirs_exist_ok=True)
                demo_head(c, dest / "demo" / "index.html", p, 3)
        if p["demo"] == "copy":
            demo_head(c, dest / "index.html", p, 2)
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {c['person']['site']}/sitemap.xml\n", encoding="utf-8")
    (SITE / "sitemap.xml").write_text(sitemap(c), encoding="utf-8")
    (OUT / "github-profile").mkdir(parents=True, exist_ok=True)
    (OUT / "github-profile" / "README.md").write_text(github_profile(c), encoding="utf-8")
    (OUT / "linkedin.md").write_text(linkedin(c), encoding="utf-8")
    social_previews(c)
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
                    if not ref or re.match(r"[a-z]+:|//|/downloads/|/media/|/demos/", ref):
                        continue  # external links, files kept on the server only, and the demo containers
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
    if sys.argv[1:] == ["resume"]:
        issues = resume(content)
        print("\n".join(issues) or f"ok: {RESUME / RESUME_FILE}.pdf and .docx")
        sys.exit(1 if issues else 0)
