#!/usr/bin/env bash
# Add one tester: their own backend, dashboard and phone web app containers,
# token, database and port.
#
#   ./new_tester.sh alice
#
# Prints what that tester needs: the phone app link, the glasses socket for the
# iOS app and the dashboard link, all carrying their token.
source "$(dirname "$0")/_lib.sh"

name="${1:-}"
[ -n "$name" ] || die "usage: ./new_tester.sh NAME"
valid_name "$name" || die "NAME must be lower-case letters, digits and dashes, starting with a letter (at most 31)"
need_env
command -v docker >/dev/null || die "docker is not installed"

success=""
env_created=""
yml_created=""
route_created=""
data_created=""
containers_attempted=""
services=()

rollback() {
  local status=$?
  trap - ERR EXIT INT TERM
  if [ -z "$success" ]; then
    [ -n "$containers_attempted" ] && compose rm -sfv "${services[@]}" >/dev/null 2>&1 || true
    [ -n "$env_created" ] && rm -f "testers/$name.env"
    [ -n "$yml_created" ] && rm -f "testers/$name.yml"
    [ -n "$route_created" ] && rm -f "routes/$name.caddy"
    [ -n "$yml_created" ] && render_override >/dev/null 2>&1 || true
    [ -n "$route_created" ] && reload_caddy >/dev/null 2>&1 || true
    [ -n "$data_created" ] && rm -rf -- "data/$name"
    echo "rolled back failed provisioning for $name" >&2
  fi
  release_deploy_lock
  exit "$status"
}
trap rollback ERR EXIT
trap 'exit 130' INT TERM

acquire_deploy_lock
[ ! -e "testers/$name.env" ] && [ ! -e "testers/$name.yml" ] && [ ! -e "routes/$name.caddy" ] \
  || die "tester '$name' already exists (./remove_tester.sh $name first)"

# The dashboard is optional: without dashboard/Dockerfile the tester still gets
# a backend, and the phone still works.
with_dashboard=""
[ -f ../dashboard/Dockerfile ] && with_dashboard=1
# Likewise the phone web app (phone/, Lukas's Next.js app) at /t/NAME/app.
with_phone=""
[ -f ../phone/Dockerfile ] && with_phone=1

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

mkdir -p testers routes
if [ ! -d "data/$name" ]; then
  mkdir -p "data/$name/dashboard"
  data_created=1
else
  mkdir -p "data/$name/dashboard"
fi

# Optional preset persona: deploy/persona.txt, copied per tester so a tester's
# own edits in the dashboard never touch anyone else's.
persona_line=""
if [ -f persona.txt ]; then
  cp persona.txt "data/$name/persona.txt"
  persona_line="PERSONA_FILE=/data/persona.txt"
fi

# The token file is the only secret here: readable by this user alone.
# HOST_PORT is bookkeeping for this script; the containers ignore it.
env_created=1
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

yml_created=1
tester_yml "$name" "$port" "$with_dashboard" "$with_phone" > "testers/$name.yml"
route_created=1
tester_route "$name" "$with_dashboard" "$with_phone" > "routes/$name.caddy"

render_override

# The backend image, on first use. (The dashboard and phone images are per
# tester and are built by `up` below, about a minute each.)
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
[ -n "$with_phone" ] && services+=("phone-$name")
containers_attempted=1
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

SEND THIS ONE to the tester — paste into the Zeroist iOS app:
  wss://$DOMAIN/t/$name/ws/glasses?token=$token

Optional (browser only, not for the iOS app):
  phone app:
    https://$DOMAIN/t/$name/app/?token=$token
  dashboard:
    https://$DOMAIN/t/$name/dashboard/?token=$token

backend API base (header X-Access-Token: <token>, or ?token=):
  https://$DOMAIN/t/$name

health (no token needed):
  https://$DOMAIN/t/$name/healthz
  https://$DOMAIN/t/$name/app/healthz

check:
  curl -fsS https://$DOMAIN/t/$name/healthz
  curl -fsS -H 'X-Access-Token: $token' https://$DOMAIN/t/$name/api/persona
logs:
  docker compose logs -f backend-$name
OUT
[ -n "$with_dashboard" ] || echo "note: no dashboard/Dockerfile, so no dashboard for $name"
[ -n "$with_phone" ] || echo "note: no phone/Dockerfile, so no phone app for $name"
success=1
