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
set_var LIVRES_OWNER_EMAIL "$(ask 'Your e-mail (recipient): ')"
set_var LIVRES_MAIL_FROM "$(ask 'Sender address (From): ')"
set_var LIVRES_SMTP_HOST "$(ask 'SMTP host (e.g. smtp.gmail.com): ')"
port="$(ask 'SMTP port [587]: ')"; set_var LIVRES_SMTP_PORT "${port:-587}"
tls="$(ask 'TLS mode starttls|ssl|none [starttls]: ')"; set_var LIVRES_SMTP_TLS "${tls:-starttls}"
set_var LIVRES_SMTP_USER "$(ask 'SMTP user: ')"
set_var LIVRES_SMTP_PASSWORD "$(ask_secret 'SMTP password (e.g. a Gmail app password): ')"

if [ "$MAIL_ONLY" = 1 ]; then
  echo
  echo "Mail settings saved. Apply them by redeploying the running version (no new release needed):"
  echo "  sudo hosh-deploy status livres            # shows the version in use"
  echo "  sudo hosh-deploy deploy livres vX.Y.Z     # same version: restarts with the new settings"
  echo "Messages that could not be sent are kept in the outbox and retried with the new settings."
  exit 0
fi
echo
echo "Done. Store this agent token now; it is not kept anywhere in clear:"
echo "  GitHub: gh secret set LIVRES_ADMIN_TOKEN -R pazka/livres   (paste it)"
echo
echo "  $token"
echo
echo "Then: sudo hosh-deploy register livres  (or restart the container if already deployed)."
