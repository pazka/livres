"""Server-rendered HTML (no JavaScript, no external asset: strict CSP). French for the public page,
plain for the owner's admin."""

from __future__ import annotations

import time
from html import escape as e

from ..domain import SearchPage

CSS = """
:root{--ink:#2b2118;--paper:#fbf6ec;--line:#e2d6c0;--accent:#7a4b22;--ok:#2f7d32;--warn:#b26a00;--bad:#b3261e}
*{box-sizing:border-box}body{margin:0;font:18px/1.5 Georgia,serif;color:var(--ink);background:var(--paper)}
main{max-width:960px;margin:0 auto;padding:1.5rem}h1{font-size:1.8rem;margin:.2rem 0 1rem}
a{color:var(--accent)}input,textarea,select,button{font:inherit;padding:.45rem .6rem;border:1px solid var(--line);border-radius:6px;background:#fff}
button{background:var(--accent);color:#fff;border:0;cursor:pointer}button.secondary{background:#fff;color:var(--accent);border:1px solid var(--accent)}
form.inline{display:inline}table{border-collapse:collapse;width:100%;font-size:.9rem}td,th{border-bottom:1px solid var(--line);padding:.4rem;text-align:left;vertical-align:top}
.book{border-bottom:1px solid var(--line);padding:.8rem 0}.muted{color:#7d6e5c;font-size:.85rem}
.state-ok{color:var(--ok)}.state-degraded,.state-unknown{color:var(--warn)}.state-down,.state-blocked{color:var(--bad)}
nav a{margin-right:1rem}.msg{padding:.6rem 1rem;background:#fff;border:1px solid var(--line);border-radius:6px;margin:1rem 0}
label{display:block;margin:.5rem 0 .2rem}.grid{display:grid;grid-template-columns:1fr 1fr;gap:.5rem 1rem}
"""


def layout(title: str, body: str, admin: bool = False, csrf: str = "") -> str:
    nav = ""
    if admin:
        nav = (
            '<nav><a href="/admin">Sources</a><a href="/admin/requests">Demandes</a><a href="/admin/events">Journal</a>'
            f'<form class="inline" method="post" action="/admin/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
            '<button class="secondary">Se déconnecter</button></form></nav>'
        )
    return (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{e(title)}</title><link rel="stylesheet" href="/static/style.css"></head>'
        f"<body><main>{nav}{body}</main></body></html>"
    )


def home(result: SearchPage | None = None, query: str = "", message: str = "") -> str:
    parts = [
        "<h1>Livres gratuits et légaux</h1>",
        '<p class="muted">Livres du domaine public et catalogues gratuits officiels, en français, sans DRM.</p>',
        f'<form method="get" action="/recherche"><input name="q" value="{e(query)}" placeholder="Titre ou auteur" size="40" autofocus> <button>Chercher</button></form>',
    ]
    if message:
        parts.append(f'<p class="msg">{e(message)}</p>')
    if result is not None:
        if not result.books:
            parts.append("<p>Aucun livre trouvé. Vous pouvez le demander ci-dessous.</p>")
        for b in result.books:
            links = " · ".join(
                f'<a href="{e(a.href)}" rel="noopener noreferrer">{e(a.media_type.rsplit("/", 1)[-1].replace("x-mobipocket-ebook", "mobi").replace("epub+zip", "epub"))}</a>'
                for a in b.sorted_acquisitions()
            )
            parts.append(
                f'<div class="book"><strong>{e(b.title)}</strong> — {e(", ".join(b.authors) or "auteur inconnu")}'
                f'<div class="muted">{e(b.source_id)} · {links}</div></div>'
            )
        if result.has_next:
            parts.append(f'<p><a href="/recherche?q={e(query)}&amp;page={result.page + 1}">Page suivante</a></p>')
    parts.append(
        "<h2>Demander un livre</h2>"
        '<form method="post" action="/demande"><div class="grid">'
        '<div><label for="title">Titre</label><input id="title" name="title" required maxlength="200"></div>'
        '<div><label for="author">Auteur</label><input id="author" name="author" maxlength="200"></div></div>'
        '<label for="message">Message</label><textarea id="message" name="message" rows="3" cols="60" maxlength="2000"></textarea>'
        '<label for="contact">Votre e-mail (facultatif)</label><input id="contact" name="contact" type="email" maxlength="254">'
        "<p><button>Envoyer la demande</button></p></form>"
    )
    return layout("Livres gratuits et légaux", "".join(parts))


def login(error: str = "") -> str:
    err = f'<p class="msg">{e(error)}</p>' if error else ""
    return layout(
        "Admin livres",
        f"<h1>Admin</h1>{err}<form method=\"post\" action=\"/admin/login\"><label for=\"password\">Mot de passe</label>"
        '<input id="password" type="password" name="password" autocomplete="current-password" required autofocus> <button>Entrer</button></form>',
    )


def _ts(value) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(value)) if value else "—"


def admin_sources(rows: list[dict], csrf: str, flash: str = "") -> str:
    def button(sid, action, label, cls=""):
        return (
            f'<form class="inline" method="post" action="/admin/sources/{e(sid)}/{action}">'
            f'<input type="hidden" name="csrf" value="{e(csrf)}"><button class="{cls}">{label}</button></form>'
        )

    body = ["<h1>Sources</h1>"]
    if flash:
        body.append(f'<p class="msg">{e(flash)}</p>')
    body.append("<table><tr><th>Source</th><th>État</th><th>Dernière vérification</th><th>Actions</th></tr>")
    for r in rows:
        actions = [button(r["id"], "check", "Tester", "secondary")]
        actions.append(button(r["id"], "disable", "Désactiver", "secondary") if r["enabled"] else button(r["id"], "enable", "Activer"))
        if r["override"] is not None:
            actions.append(button(r["id"], "reset", "Revenir au réglage du dépôt", "secondary"))
        if "harvest" in r["modes"]:
            actions.append(button(r["id"], "harvest", "Recharger le catalogue", "secondary"))
        if r["origin"] == "admin":
            actions.append(button(r["id"], "delete", "Supprimer", "secondary"))
        body.append(
            f'<tr><td><strong>{e(r["name"])}</strong> <span class="muted">({e(r["id"])}, {e(r["kind"])}, {e(r["origin"])}, {e(r["status"])})</span>'
            f'<div class="muted">{e(r["legal"])}</div></td>'
            f'<td class="state-{e(r["state"])}">{e(r["state"])}{"" if r["enabled"] else " (désactivée)"}'
            f'<div class="muted">{e(r["detail"] or "")}</div>{("<div class=muted>" + str(r["books"]) + " livres indexés</div>") if r["books"] else ""}</td>'
            f'<td>{_ts(r["last_check"])}<div class="muted">dernier succès {_ts(r["last_ok"])}</div></td><td>{" ".join(actions)}</td></tr>'
        )
    body.append("</table>")
    body.append(
        "<h2>Ajouter un catalogue OPDS</h2>"
        "<p class=\"muted\">Seulement des sources légales : domaine public ou catalogue gratuit officiel, sans DRM ni compte. "
        "Le catalogue est testé avant d’être enregistré.</p>"
        f'<form method="post" action="/admin/sources"><input type="hidden" name="csrf" value="{e(csrf)}"><div class="grid">'
        '<div><label>Identifiant (minuscules)</label><input name="id" required pattern="[a-z][a-z0-9-]{1,40}"></div>'
        '<div><label>Nom</label><input name="name" required></div>'
        '<div><label>Type</label><select name="kind"><option value="opds1">OPDS 1 (Atom)</option><option value="opds2">OPDS 2 (JSON)</option></select></div>'
        '<div><label>Adresse de recherche ({searchTerms}) ou racine OPDS 1</label><input name="url" required type="url"></div>'
        '<div><label>Requête test (doit toujours trouver un livre)</label><input name="canary" required value="Victor Hugo"></div>'
        '<div><label>Pourquoi c’est légal</label><input name="legal" required minlength="20"></div>'
        "</div><p><button>Tester et ajouter</button></p></form>"
    )
    return layout("Sources — admin livres", "".join(body), admin=True, csrf=csrf)


def admin_requests(rows, csrf: str) -> str:
    body = ["<h1>Demandes de livres</h1><table><tr><th>Date</th><th>Livre</th><th>Message</th><th>État</th></tr>"]
    for r in rows:
        options = "".join(
            f'<option value="{s}"{" selected" if r["status"] == s else ""}>{label}</option>'
            for s, label in (("new", "nouvelle"), ("found", "trouvé"), ("not_found", "introuvable"), ("done", "terminé"))
        )
        body.append(
            f'<tr><td>{_ts(r["created_at"])}</td><td><strong>{e(r["title"])}</strong><div class="muted">{e(r["author"] or "")}</div>'
            f'<div class="muted">{e(r["contact"] or "")}</div></td><td>{e(r["message"] or "")}</td>'
            f'<td><form method="post" action="/admin/requests/{r["id"]}/status"><input type="hidden" name="csrf" value="{e(csrf)}">'
            f'<select name="status">{options}</select> <button class="secondary">OK</button></form></td></tr>'
        )
    body.append("</table>")
    return layout("Demandes — admin livres", "".join(body), admin=True, csrf=csrf)


def admin_events(rows, mail: dict, csrf: str) -> str:
    body = [f"<h1>Journal</h1><p class=\"muted\">E-mails envoyés : {mail['sent']} · en attente : {mail['pending']}</p>"]
    body.append("<table><tr><th>Date</th><th>Qui</th><th>Action</th><th>Détail</th></tr>")
    for r in rows:
        body.append(f'<tr><td>{_ts(r["at"])}</td><td>{e(r["actor"])}</td><td>{e(r["action"])}</td><td>{e(r["detail"] or "")}</td></tr>')
    body.append("</table>")
    return layout("Journal — admin livres", "".join(body), admin=True, csrf=csrf)
