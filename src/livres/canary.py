"""Turn a canary report into GitHub issue actions (used by .github/workflows/sources-canary.yml).

One open issue per broken source, titled "source-down: <id>", label `source-down`. It is the work queue
for agents (AGENTS.md P2). Recovered sources get their issue closed. Candidates that start passing get
one `source-candidate-ok` issue so someone can enable them (P1a).

    python -m livres.canary plan report.json prod-status.json open-issues.json  -> JSON list of actions
"""

from __future__ import annotations

import json
import sys

DOWN_LABEL = "source-down"
CANDIDATE_LABEL = "source-candidate-ok"


def title_for(source_id: str, label: str) -> str:
    return f"{label}: {source_id}"


def plan(report: list[dict], prod: dict, open_issues: list[dict], mention: str = "", run_url: str = "") -> list[dict]:
    """Pure decision logic (unit-tested). `open_issues` items: {number, title, labels:[names]}."""
    by_title = {i["title"]: i for i in open_issues}
    prod_state = {s["id"]: s for s in (prod.get("sources") or [])}
    actions: list[dict] = []
    seen_down: set[str] = set()

    for r in report:
        sid = r["id"]
        if r.get("status") == "candidate" and not r.get("enabled"):
            title = title_for(sid, CANDIDATE_LABEL)
            if r["ok"] and title not in by_title:
                actions.append({
                    "action": "open", "title": title, "labels": [CANDIDATE_LABEL],
                    "body": _candidate_body(r, run_url, mention),
                })
            continue
        prod_row = prod_state.get(sid) or {}
        prod_down = prod_row.get("enabled", True) and prod_row.get("state") in ("down", "blocked")
        if not r["ok"] or prod_down:
            seen_down.add(sid)
            title = title_for(sid, DOWN_LABEL)
            body = _down_body(r, prod_row, run_url, mention)
            if title in by_title:
                actions.append({"action": "comment", "number": by_title[title]["number"], "body": _still_down(r, prod_row, run_url)})
            else:
                actions.append({"action": "open", "title": title, "labels": [DOWN_LABEL], "body": body})

    for issue in open_issues:
        if DOWN_LABEL in issue.get("labels", []) and issue["title"].startswith(DOWN_LABEL + ": "):
            sid = issue["title"].split(": ", 1)[1]
            checked = any(r["id"] == sid for r in report)
            if checked and sid not in seen_down:
                actions.append({"action": "close", "number": issue["number"], "body": f"The canary passes again for `{sid}` ({run_url}). Closing."})
    return actions


def _down_body(r: dict, prod_row: dict, run_url: str, mention: str) -> str:
    lines = [
        f"The daily canary found the source **{r.get('name', r['id'])}** (`{r['id']}`) broken.",
        "",
        f"- From GitHub Actions: {'OK' if r['ok'] else 'FAIL'} — `{r.get('detail', '')}`",
    ]
    if r.get("harvest"):
        lines.append(f"- Harvest: {'OK' if r['harvest']['ok'] else 'FAIL'} — `{r['harvest']['detail']}`")
    if prod_row:
        lines.append(f"- From livres.hosh.it: state `{prod_row.get('state')}` — `{prod_row.get('detail', '')}`")
    lines += [
        f"- Run: {run_url}",
        "",
        "**What to do:** follow protocol **P2 (fix a broken source)** in `AGENTS.md`.",
        "If the site is gone or now forbids robots, follow **P3** (disable/remove) instead and say why here.",
        "Mom's searches keep working with the other sources in the meantime.",
    ]
    if mention:
        lines += ["", f"{mention} please take this issue: follow AGENTS.md protocol P2 and open a PR."]
    return "\n".join(lines)


def _still_down(r: dict, prod_row: dict, run_url: str) -> str:
    extra = f" Production: `{prod_row.get('state')}`." if prod_row else ""
    return f"Still failing on {run_url}: `{r.get('detail', '')}`.{extra}"


def _candidate_body(r: dict, run_url: str, mention: str) -> str:
    text = (
        f"The candidate source **{r.get('name', r['id'])}** (`{r['id']}`) passed its live canary: `{r.get('detail', '')}` ({run_url}).\n\n"
        "**What to do:** protocol **P1a** in `AGENTS.md`: record fixtures from the live feed, check the legal note, "
        'set `"status": "verified"` and `"enabled": true`, open a PR.'
    )
    if mention:
        text += f"\n\n{mention} please take this issue (AGENTS.md P1a)."
    return text


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[0] != "plan":
        print(__doc__)
        return 2
    report = json.loads(open(argv[1], encoding="utf-8").read())
    try:
        prod = json.loads(open(argv[2], encoding="utf-8").read() or "{}")
    except ValueError:
        prod = {}
    issues = json.loads(open(argv[3], encoding="utf-8").read() or "[]")
    issues = [{"number": i["number"], "title": i["title"], "labels": [l["name"] if isinstance(l, dict) else l for l in i.get("labels", [])]} for i in issues]
    mention = argv[4] if len(argv) > 4 else ""
    run_url = argv[5] if len(argv) > 5 else ""
    print(json.dumps(plan(report, prod, issues, mention, run_url), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
