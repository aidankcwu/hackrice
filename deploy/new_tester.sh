#!/usr/bin/env bash
# Add one tester: their own backend and dashboard containers, token, database
# and port.
#
#   ./new_tester.sh alice
#
# Prints what that tester needs: the glasses socket for the iOS app and the
# dashboard link, both carrying their token.
source "$(dirname "$0")/_lib.sh"

name="${1:-}"
[ -n "$name" ] || die "usage: ./new_tester.sh NAME"
valid_name "$name" || die "NAME must be lower-case letters, digits and dashes, starting with a letter (at most 31)"
need_env
[ ! -e "testers/$name.env" ] || die "tester '$name' already exists (./remove_tester.sh $name first)"
command -v docker >/dev/null || die "docker is not installed"

# The dashboard is optional: without dashboard/Dockerfile the tester still gets
# a backend, and the phone still works.
with_dashboard=""
[ -f ../dashboard/Dockerfile ] && with_dashboard=1

# Next free host port: one above the highest any tester holds.
port=$FIRST_PORT
for f in testers/*.env; do
  [ -e "$f" ] || continue
  p="$(env_value HOST_PORT "$f")"
  if [ -n "$p" ] && [ "$p" -ge "$port" ]; then port=$((p + 1)); fi
done

# URL-safe on purpose: it goes into ?token= on a socket URL and an image URL,
# and a '+' or '/' would be mangled between Python's query parsing and iOS.
token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
  || openssl rand -hex 32)"
[ "${#token}" -ge 40 ] || die "could not generate a token (need python3 or openssl)"

mkdir -p "data/$name/dashboard" testers routes

# Optional preset persona: deploy/persona.txt, copied per tester so a tester's
# own edits in the dashboard never touch anyone else's.
persona_line=""
if [ -f persona.txt ]; then
  cp persona.txt "data/$name/persona.txt"
  persona_line="PERSONA_FILE=/data/persona.txt"
fi

# The token file is the only secret here: readable by this user alone.
# HOST_PORT is bookkeeping for this script; the containers ignore it.
(
  umask 077
  cat > "testers/$name.env" <<ENV
# $name -- written by new_tester.sh on $(date -u +%Y-%m-%dT%H:%MZ)
HOST_PORT=$port
ACCESS_TOKEN=$token
HOSTED=1
ROOT_PATH=/t/$name
$persona_line
ENV
)

{
  cat <<YML
services:
  backend-$name:
    extends: { file: tester.yml, service: backend }
    env_file: [.env, testers/$name.env]
    ports: ["127.0.0.1:$port:$BACKEND_PORT"]
    volumes: ["./data/$name:/data"]
YML
  if [ -n "$with_dashboard" ]; then
    cat <<YML
  dashboard-$name:
    extends: { file: tester.yml, service: dashboard }
    image: $DASHBOARD_IMAGE:$name
    build:
      context: ../dashboard
      args: { NEXT_BASE_PATH: /t/$name/dashboard }
    # Only the token file, never .env: the dashboard needs no API keys.
    env_file: [testers/$name.env]
    environment:
      BACKEND_URL: http://backend-$name:$BACKEND_PORT
    volumes: ["./data/$name/dashboard:/data"]
    depends_on: [backend-$name]
YML
  fi
} > "testers/$name.yml"

{
  cat <<CADDY
# $name -- written by new_tester.sh
@root-$name path /t/$name /t/$name/
redir @root-$name /t/$name/dashboard{?query} 302

CADDY
  if [ -n "$with_dashboard" ]; then
    cat <<CADDY
# Prefix kept: the dashboard was built with basePath /t/$name/dashboard.
handle /t/$name/dashboard* {
	reverse_proxy dashboard-$name:3000 {
		# Its auth cookie is https-only; say so even though this hop is http.
		header_up X-Forwarded-Proto https
	}
}

CADDY
  fi
  cat <<CADDY
# Prefix stripped: the backend serves /api, /frames and /ws/glasses at its root.
handle /t/$name/* {
	uri strip_prefix /t/$name
	reverse_proxy backend-$name:$BACKEND_PORT {
		# A config reload (adding or removing a tester) must not drop this
		# tester's glasses socket mid-demo. Streams have no idle timeout by
		# default, so the phone's 10 s pings keep the socket up indefinitely.
		stream_close_delay 2h
	}
}
CADDY
} > "routes/$name.caddy"

render_override

# The backend image, on first use. (The dashboard image is per tester and is
# built by `up` below, about a minute each.)
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "building $IMAGE (first tester only, a few minutes) ..."
  compose build backend-image
fi

# The bind mounts belong to whoever ran this script; the containers run as
# unprivileged users and must be able to write there.
docker run --rm --user 0 --entrypoint sh -v "$DEPLOY_DIR/data/$name:/data" "$IMAGE" -c \
  "chown -R $RUNTIME_UID:$RUNTIME_UID /data && chown -R $DASHBOARD_UID:$DASHBOARD_UID /data/dashboard"

services=("backend-$name")
[ -n "$with_dashboard" ] && services+=("dashboard-$name")
compose up -d "${services[@]}"
reload_caddy

printf 'waiting for backend-%s to answer' "$name"
ok=""
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then ok=1; break; fi
  printf '.'; sleep 2
done
echo
[ -n "$ok" ] || echo "warning: backend-$name did not answer yet; see: docker compose logs backend-$name"

cat <<OUT

tester:      $name
host port:   $port  (127.0.0.1 only, the backend)
token:       $token

iOS app (glasses socket):
  wss://$DOMAIN/t/$name/ws/glasses?token=$token

dashboard (the link to send the tester):
  https://$DOMAIN/t/$name/dashboard/?token=$token

backend API base (header X-Access-Token: <token>, or ?token=):
  https://$DOMAIN/t/$name

health (no token needed):
  https://$DOMAIN/t/$name/healthz

check:
  curl -fsS https://$DOMAIN/t/$name/healthz
  curl -fsS -H 'X-Access-Token: $token' https://$DOMAIN/t/$name/api/persona
logs:
  docker compose logs -f backend-$name
OUT
[ -n "$with_dashboard" ] || echo "note: no dashboard/Dockerfile, so no dashboard for $name"
