#!/usr/bin/env python3
"""Generuje docs/roadmap.svg: wykres Gantta z GitHub Projects."""
import html
import json
import os
import subprocess
from datetime import date, timedelta

REPO = os.environ.get("GITHUB_REPOSITORY", "")
OWNER = os.environ.get("PROJECT_OWNER") or REPO.split("/")[0]
OWNER_TYPE = os.environ.get("PROJECT_OWNER_TYPE", "user")  # user | organization
PROJECT_NUMBER = int(os.environ.get("PROJECT_NUMBER", "1"))
OUT = os.environ.get("GANTT_OUT", "docs/roadmap.svg")
SHOW_TODAY = True  # False = brak linii "dzis"

MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]
LABEL_W, TL_W, PAD = 320, 720, 16
ROW_H, TOP, MONTH_H = 30, 44, 26
C = dict(text="#1f2328", muted="#656d76", grid="#d0d7de", week="#eaeef2", band="#f6f8fa",
         open="#0969da", done="#1a7f37", group="#424a53", dep="#6e7781", bad="#cf222e")

QUERY = """
query($owner:String!, $number:Int!, $cursor:String){
  OWNERTYPE(login:$owner){
    projectV2(number:$number){
      items(first:100, after:$cursor){
        pageInfo{ hasNextPage endCursor }
        nodes{
          content{
            __typename
            ... on Issue{ number title state repository{ nameWithOwner } }
          }
          fieldValues(first:30){
            nodes{
              __typename
              ... on ProjectV2ItemFieldDateValue{ date field{ ... on ProjectV2FieldCommon{ name } } }
              ... on ProjectV2ItemFieldIterationValue{ startDate duration field{ ... on ProjectV2FieldCommon{ name } } }
            }
          }
        }
      }
    }
  }
}
""".replace("OWNERTYPE", OWNER_TYPE)


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return json.loads(r.stdout)


def gh_try(path, default):
    try:
        return gh("api", path)
    except RuntimeError:
        return default


def fetch_items():
    cursor, out = None, []
    while True:
        args = ["api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={OWNER}",
                "-F", f"number={PROJECT_NUMBER}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        data = gh(*args)
        if data.get("errors"):
            raise SystemExit(f"Błąd GraphQL: {data['errors']}")
        proj = (data["data"].get(OWNER_TYPE) or {}).get("projectV2")
        if proj is None:
            raise SystemExit("Nie znaleziono projektu. Sprawdź PROJECT_OWNER, PROJECT_OWNER_TYPE, "
                             "PROJECT_NUMBER oraz uprawnienia tokena (read:project).")
        page = proj["items"]
        out += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def parse_dates(field_values):
    start = end = it_start = it_end = None
    for fv in field_values:
        kind = fv.get("__typename")
        name = ((fv.get("field") or {}).get("name") or "").lower()
        if kind == "ProjectV2ItemFieldDateValue" and fv.get("date"):
            d = date.fromisoformat(fv["date"])
            if any(k in name for k in ("start", "pocz")):
                start = d
            elif any(k in name for k in ("target", "end", "due", "deadline", "kon", "termin")):
                end = d
        elif kind == "ProjectV2ItemFieldIterationValue" and fv.get("startDate"):
            it_start = date.fromisoformat(fv["startDate"])
            it_end = it_start + timedelta(days=fv["duration"] - 1)
    if start is None and end is None:
        start, end = it_start, it_end
    if start and not end:
        end = start + timedelta(days=6)
    if end and not start:
        start = end - timedelta(days=6)
    if start and end and end < start:
        end = start
    return start, end


def load_tasks():
    tasks = {}
    for node in fetch_items():
        c = node.get("content") or {}
        if c.get("__typename") != "Issue":
            continue
        if c["repository"]["nameWithOwner"].lower() != REPO.lower():
            continue
        s, e = parse_dates(node["fieldValues"]["nodes"])
        tasks[c["number"]] = dict(n=c["number"], title=c["title"], closed=c["state"] == "CLOSED",
                                  start=s, end=e, children=[], parent=None, blockers=[])

    def same_repo(issue):
        return (issue.get("repository_url") or "").lower().endswith("/" + REPO.lower())

    for n, t in tasks.items():
        for ch in gh_try(f"repos/{REPO}/issues/{n}/sub_issues?per_page=100", []):
            if same_repo(ch) and ch["number"] in tasks and ch["number"] != n:
                tasks[ch["number"]]["parent"] = n
                t["children"].append(ch["number"])
        for b in gh_try(f"repos/{REPO}/issues/{n}/dependencies/blocked_by?per_page=100", []):
            if same_repo(b) and b["number"] in tasks:
                t["blockers"].append(b["number"])
    return tasks


def next_month(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def trunc(text, max_chars):
    return text if len(text) <= max_chars else text[: max(max_chars - 1, 1)] + "…"


def render(tasks, title="Roadmap", today=None):
    today = today or date.today()

    def span(t):
        s, e = t["start"], t["end"]
        for c in t["children"]:
            cs, ce = span(tasks[c])
            if cs:
                s = min(s, cs) if s else cs
            if ce:
                e = max(e, ce) if e else ce
        return s, e

    def sort_key(n):
        return (span(tasks[n])[0] or date.max, n)

    rows = []

    def add(n, depth):
        t = tasks[n]
        kids = sorted(t["children"], key=sort_key)
        if kids:
            s, e = span(t)
            done = sum(1 for c in kids if tasks[c]["closed"])
            rows.append(dict(kind="group", t=t, depth=depth, start=s, end=e, prog=(done, len(kids))))
            for c in kids:
                add(c, depth + 1)
        else:
            rows.append(dict(kind="task", t=t, depth=depth, start=t["start"], end=t["end"]))

    for n in sorted((n for n, t in tasks.items() if t["parent"] is None), key=sort_key):
        add(n, 0)

    dated = [r for r in rows if r["start"]]
    if not dated:
        raise SystemExit("Żadne zadanie w projekcie nie ma dat. Uzupełnij pola Start date / Target date.")

    t0 = min(r["start"] for r in dated).replace(day=1)
    t1 = next_month(max(r["end"] for r in dated))
    days = (t1 - t0).days
    ppd = TL_W / days
    X0 = PAD + LABEL_W
    W = PAD * 2 + LABEL_W + TL_W
    y0 = TOP + MONTH_H
    y_end = y0 + len(rows) * ROW_H
    H = y_end + 70

    def x(d):
        return X0 + (d - t0).days * ppd

    esc = html.escape
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="-apple-system,\'Segoe UI\',Helvetica,Arial,sans-serif" font-size="13">',
         '<defs>'
         f'<marker id="ag" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" refX="8" refY="4" '
         f'orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{C["dep"]}"/></marker>'
         f'<marker id="ar" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" refX="8" refY="4" '
         f'orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="{C["bad"]}"/></marker>'
         '</defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<text x="{PAD}" y="26" font-size="17" font-weight="700" fill="{C["text"]}">{esc(title)}</text>',
         f'<rect x="{PAD}" y="{TOP}" width="{W - 2 * PAD}" height="{MONTH_H}" fill="{C["band"]}"/>',
         f'<text x="{PAD + 8}" y="{TOP + 17}" font-weight="600" fill="{C["muted"]}">Zadanie</text>']

    for i, r in enumerate(rows):
        top = y0 + i * ROW_H
        if r["kind"] == "group":
            s.append(f'<rect x="{PAD}" y="{top}" width="{W - 2 * PAD}" height="{ROW_H}" fill="{C["band"]}"/>')
        s.append(f'<line x1="{PAD}" x2="{W - PAD}" y1="{top + ROW_H}" y2="{top + ROW_H}" stroke="{C["week"]}"/>')

    if days <= 240:
        d = t0 + timedelta(days=(7 - t0.weekday()) % 7)
        while d < t1:
            s.append(f'<line x1="{x(d):.1f}" x2="{x(d):.1f}" y1="{y0}" y2="{y_end}" stroke="{C["week"]}"/>')
            d += timedelta(days=7)
    m = t0
    while m < t1:
        nm = next_month(m)
        xa, xb = x(m), x(nm)
        s.append(f'<line x1="{xa:.1f}" x2="{xa:.1f}" y1="{TOP}" y2="{y_end}" stroke="{C["grid"]}"/>')
        label = f"{MONTHS[m.month - 1]} {m.year}" if xb - xa > 64 else MONTHS[m.month - 1]
        s.append(f'<text x="{(xa + xb) / 2:.1f}" y="{TOP + 17}" text-anchor="middle" '
                 f'font-weight="600" fill="{C["muted"]}">{label}</text>')
        m = nm
    s.append(f'<line x1="{X0}" x2="{X0}" y1="{TOP}" y2="{y_end}" stroke="{C["grid"]}" stroke-width="1.5"/>')
    s.append(f'<line x1="{PAD}" x2="{W - PAD}" y1="{y0}" y2="{y0}" stroke="{C["grid"]}"/>')
    s.append(f'<line x1="{PAD}" x2="{W - PAD}" y1="{y_end}" y2="{y_end}" stroke="{C["grid"]}"/>')

    pos = {}
    for i, r in enumerate(rows):
        t = r["t"]
        top = y0 + i * ROW_H
        yc = top + ROW_H / 2
        group = r["kind"] == "group"
        indent = 8 + r["depth"] * 18
        label = f'#{t["n"]} {t["title"]}'
        maxc = int((LABEL_W - indent - 12) / (7.3 if group else 6.9))
        if group:
            suffix = f'  ({r["prog"][0]}/{r["prog"][1]})'
            label = trunc(label, maxc - len(suffix)) + suffix
        else:
            label = trunc(label, maxc)
        undated = r["start"] is None
        fill = C["muted"] if undated and not group else C["text"]
        weight = ' font-weight="600"' if group else ""
        s.append(f'<text x="{PAD + indent}" y="{yc + 4.5:.1f}" fill="{fill}"{weight}>{esc(label)}</text>')

        if undated:
            s.append(f'<text x="{X0 + 10}" y="{yc + 4.5:.1f}" font-style="italic" '
                     f'fill="{C["muted"]}" font-size="12">brak dat</text>')
            continue
        xa = x(r["start"])
        xb = max(x(r["end"] + timedelta(days=1)), xa + 4)
        if group:
            y = top + 9
            if xb - xa > 14:
                s.append(f'<path d="M{xa:.1f},{y} H{xb:.1f} V{y + 12} L{xb - 6:.1f},{y + 6} '
                         f'H{xa + 6:.1f} L{xa:.1f},{y + 12} Z" fill="{C["group"]}"/>')
            else:
                s.append(f'<rect x="{xa:.1f}" y="{y}" width="{xb - xa:.1f}" height="8" fill="{C["group"]}"/>')
        else:
            col = C["done"] if t["closed"] else C["open"]
            s.append(f'<rect x="{xa:.1f}" y="{top + 6}" width="{xb - xa:.1f}" height="{ROW_H - 12}" '
                     f'rx="3" fill="{col}"/>')
        pos[t["n"]] = (xa, xb, yc, r["start"], r["end"])

    for r in rows:
        t = r["t"]
        if t["n"] not in pos:
            continue
        xs, _, ys, bstart, _ = pos[t["n"]]
        for b in t["blockers"]:
            if b not in pos:
                continue
            _, xe, ye, _, bend = pos[b]
            bad = bstart <= bend
            if xs >= xe + 18:
                pts = [(xe, ye), (xe + 8, ye), (xe + 8, ys), (xs, ys)]
            else:
                ym = ye + (ROW_H / 2 if ys > ye else -ROW_H / 2)
                pts = [(xe, ye), (xe + 8, ye), (xe + 8, ym), (xs - 8, ym), (xs - 8, ys), (xs, ys)]
            p = " ".join(f"{a:.1f},{b_:.1f}" for a, b_ in pts)
            s.append(f'<polyline points="{p}" fill="none" stroke="{C["bad"] if bad else C["dep"]}" '
                     f'stroke-width="1.4" stroke-linejoin="round" '
                     f'marker-end="url(#{"ar" if bad else "ag"})"/>')

    if SHOW_TODAY and t0 <= today < t1:
        xt = x(today)
        s.append(f'<line x1="{xt:.1f}" x2="{xt:.1f}" y1="{y0}" y2="{y_end + 6}" stroke="{C["bad"]}" '
                 f'stroke-width="1.5" stroke-dasharray="4 3"/>')
        s.append(f'<text x="{xt:.1f}" y="{y_end + 19}" text-anchor="middle" font-size="11" '
                 f'font-weight="600" fill="{C["bad"]}">dziś</text>')

    ly = y_end + 48
    lx = PAD
    for kind, label in [("open", "W toku"), ("done", "Zamknięte"), ("group", "Grupa (parent issue)"),
                        ("dep", "Zależność (blocked by)"), ("bad", "Konflikt terminów")]:
        if kind in ("open", "done"):
            s.append(f'<rect x="{lx}" y="{ly - 9}" width="16" height="10" rx="2" fill="{C[kind]}"/>')
        elif kind == "group":
            s.append(f'<path d="M{lx},{ly - 9} H{lx + 16} V{ly + 1} L{lx + 12},{ly - 3} H{lx + 4} L{lx},{ly + 1} Z" '
                     f'fill="{C["group"]}"/>')
        else:
            s.append(f'<polyline points="{lx},{ly - 4} {lx + 22},{ly - 4}" stroke="{C[kind]}" stroke-width="1.4" '
                     f'marker-end="url(#{"ar" if kind == "bad" else "ag"})"/>')
        w = 26 if kind in ("dep", "bad") else 20
        s.append(f'<text x="{lx + w}" y="{ly}" font-size="12" fill="{C["muted"]}">{esc(label)}</text>')
        lx += w + len(label) * 6.4 + 22
    s.append("</svg>")
    return "\n".join(s) + "\n"


def main():
    tasks = load_tasks()
    svg = render(tasks, title=f"Roadmap — {REPO}")
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Zapisano {OUT} ({len(tasks)} zadań w projekcie)")


if __name__ == "__main__":
    main()
