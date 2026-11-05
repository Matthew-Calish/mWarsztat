import json, os, re, subprocess

repo = os.environ["GITHUB_REPOSITORY"]

def api(path):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)

# wszystkie issues (bez pull requestów)
issues, page = [], 1
while True:
    batch = api(f"repos/{repo}/issues?state=all&per_page=100&page={page}")
    if not batch:
        break
    issues += [i for i in batch if "pull_request" not in i]
    page += 1

lines = ["```mermaid", "flowchart LR"]
for i in issues:
    title = i["title"].replace('"', "'")
    lines.append(f'    I{i["number"]}["#{i["number"]} {title}"]')

# strzałki: blokujące --> blokowane
for i in issues:
    for b in api(f"repos/{repo}/issues/{i['number']}/dependencies/blocked_by"):
        lines.append(f'    I{b["number"]} --> I{i["number"]}')

# zamknięte zadania na zielono
closed = [f'I{i["number"]}' for i in issues if i["state"] == "closed"]
lines.append("    classDef done fill:#d4edda,stroke:#28a745,color:#000")
if closed:
    lines.append(f'    class {",".join(closed)} done')
lines.append("```")
diagram = "\n".join(lines)

readme = open("README.md", encoding="utf-8").read()
new = re.sub(
    r"(<!-- ROADMAP:START -->).*?(<!-- ROADMAP:END -->)",
    lambda m: f"{m.group(1)}\n{diagram}\n{m.group(2)}",
    readme,
    flags=re.DOTALL,
)
open("README.md", "w", encoding="utf-8").write(new)
