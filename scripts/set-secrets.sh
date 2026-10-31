#!/bin/bash
# set-secrets.sh — OWNER ONLY, in your own SSH terminal (needs a TTY). Writes the secrets of livres.env.
#
#   sudo bash /srv/livres/scripts/set-secrets.sh /srv/livres/livres.env [image]      everything (first install)
#   sudo bash /srv/livres/scripts/set-secrets.sh --mail /srv/livres/livres.env         only the mail settings
#                                       (admin password and agent token are left untouched)
#
# Nothing secret is printed, logged or passed on a command line, except the agent API token, shown
# ONCE so you can store it as the GitHub secret LIVRES_ADMIN_TOKEN (and in your password manager).
# Hashing runs inside the released image (no network, read-only, non-root): no repo code on the host.
set -euo pipefail
MAIL_ONLY=0
if [ "${1:-}" = "--mail" ]; then MAIL_ONLY=1; shift; fi
ENV_FILE="${1:?usage: set-secrets.sh [--mail] /srv/livres/livres.env [image]}"
[ -f "$ENV_FILE" ] || { echo "$ENV_FILE not found (create it from .env.example first)" >&2; exit 1; }
[ -t 0 ] || { echo "run this in an interactive terminal" >&2; exit 1; }
IMAGE="${2:-$(sed -n "s/^APP_IMAGE='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" "$ENV_FILE")}"
RUN=(docker run --rm -i --network none --read-only --cap-drop ALL --user 10001:10001 --security-opt no-new-privileges:true "$IMAGE")

set_var() {  # set_var NAME VALUE  (single-quoted: compose takes it literally, '$' in hashes is safe)
  local name="$1" value="$2" tmp
  case "$value" in *"'"*) echo "a value may not contain a single quote" >&2; exit 1 ;; esac
  tmp="$(mktemp "${ENV_FILE}.XXXX")"; chmod 600 "$tmp"
  grep -v "^${name}=" "$ENV_FILE" > "$tmp" || true
  printf "%s='%s'\n" "$name" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
}

ask() { local prompt="$1" var; read -r -p "$prompt" var; printf '%s' "$var"; }
ask_secret() { local prompt="$1" var; read -r -s -p "$prompt" var; echo >&2; printf '%s' "$var"; }

if [ "$MAIL_ONLY" = 0 ]; then
echo "== Admin page password (12+ characters)"
while :; do
  p1="$(ask_secret 'Password: ')"; p2="$(ask_secret 'Again: ')"
  [ "$p1" = "$p2" ] && [ ${#p1} -ge 12 ] && break
  echo "passwords differ or are shorter than 12 characters" >&2
done
hash="$(printf '%s\n' "$p1" | "${RUN[@]}" python -m livres.cli hash-password)"
unset p1 p2
set_var LIVRES_ADMIN_PASSWORD_HASH "$hash"

echo "== Agent / CI API token"
tok_json="$("${RUN[@]}" python -m livres.cli new-token)"
token="$(printf '%s' "$tok_json" | sed -n 's/.*"token": "\([^"]*\)".*/\1/p')"
sha="$(printf '%s' "$tok_json" | sed -n 's/.*"sha256": "\([0-9a-f]*\)".*/\1/p')"
set_var LIVRES_ADMIN_TOKEN_SHA256 "$sha"
fi

echo "== Mail (book requests and source alerts are sent to you)"
# Every answer is checked before anything is written, so a typo can't leave a half-broken mail setup.
email_ok() { case "$1" in *@*.*) case "$1" in *" "*) return 1 ;; esac; return 0 ;; *) return 1 ;; esac; }
ask_until() {  # ask_until PROMPT CHECK_FN MESSAGE [DEFAULT]
  local v
  while :; do
    v="$(ask "$1")"; v="${v:-${4:-}}"
    "$2" "$v" && { printf '%s' "$v"; return; }
    echo "$3" >&2
  done
}
nonempty() { [ -n "$1" ] && case "$1" in *" "*) return 1 ;; esac; }
port_ok() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
tls_ok() { case "$1" in starttls|ssl|none) return 0 ;; *) return 1 ;; esac; }
owner="$(ask_until 'Your e-mail (recipient): ' email_ok 'enter a full e-mail address')"
from="$(ask_until 'Sender address (From): ' email_ok 'enter a full e-mail address')"
host="$(ask_until 'SMTP host (e.g. smtp.gmail.com): ' nonempty 'the SMTP host is required (Gmail: smtp.gmail.com)')"
port="$(ask_until 'SMTP port [587]: ' port_ok 'enter a port number (Gmail: 587)' 587)"
tls="$(ask_until 'TLS mode starttls|ssl|none [starttls]: ' tls_ok 'enter starttls, ssl or none' starttls)"
while :; do
  user="$(ask_until 'SMTP user (Gmail: your full address): ' nonempty 'the SMTP user is required')"
  case "$host" in *gmail.com|*googlemail.com) email_ok "$user" || { echo "Gmail needs the full address as user (e.g. $from)" >&2; continue; } ;; esac
  break
done
while :; do
  smtp_pw="$(ask_secret 'SMTP password (Gmail: a 16-letter app password, not your normal password): ')"
  [ -n "$smtp_pw" ] && break
  echo "the SMTP password is required" >&2
done
set_var LIVRES_OWNER_EMAIL "$owner"
set_var LIVRES_MAIL_FROM "$from"
set_var LIVRES_SMTP_HOST "$host"
set_var LIVRES_SMTP_PORT "$port"
set_var LIVRES_SMTP_TLS "$tls"
set_var LIVRES_SMTP_USER "$user"
set_var LIVRES_SMTP_PASSWORD "$smtp_pw"
unset smtp_pw

if [ "$MAIL_ONLY" = 1 ]; then
  echo
  echo "Mail settings saved. Apply them by redeploying the running version (no new release needed):"
  echo "  sudo hosh-deploy status livres            # shows the version in use"
  echo "  sudo hosh-deploy deploy livres vX.Y.Z     # same version: restarts with the new settings"
  echo "  (or on GitHub: Actions → deploy → vX.Y.Z)"
  echo "Messages that could not be sent are kept in the outbox and retried with the new settings."
  exit 0
fi
echo
echo "Done. Store this agent token now; it is not kept anywhere in clear:"
echo "  GitHub: gh secret set LIVRES_ADMIN_TOKEN -R pazka/livres   (paste it)"
echo
echo "  $token"
echo
echo "Then: sudo hosh-deploy register livres  (first install), or if livres already runs:"
echo "  sudo hosh-deploy deploy livres vX.Y.Z  (the version in use, see: sudo hosh-deploy status livres),"
echo "  or GitHub: Actions → deploy → vX.Y.Z"
