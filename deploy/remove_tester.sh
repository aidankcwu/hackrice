#!/usr/bin/env bash
# Remove one tester: stop and delete their containers (backend, dashboard,
# phone app), images, route and token.
#
#   ./remove_tester.sh alice             # permanently delete their data
#   ./remove_tester.sh --archive alice   # retain it under data/_removed/
source "$(dirname "$0")/_lib.sh"

archive=""
if [ "${1:-}" = "--archive" ]; then archive=1; shift; fi
name="${1:-}"
[ -n "$name" ] && [ "$#" -eq 1 ] || die "usage: ./remove_tester.sh [--archive] NAME"
valid_name "$name" || die "not a tester name: $name"
[ -e "testers/$name.env" ] || die "no tester '$name'"

acquire_deploy_lock
trap release_deploy_lock EXIT

# Stop while the services are still in the override, so compose can find them.
# Only the ones this tester has: one added before the phone app existed has none.
services=()
while read -r svc; do [ -n "$svc" ] && services+=("$svc"); done < <(tester_services "$name")
[ "${#services[@]}" -gt 0 ] || services=("backend-$name")
retry="docker compose rm -sfv ${services[*]}"
if ! compose rm -sfv "${services[@]}"; then
  echo "error: could not remove every container for '$name'; tester state was kept" >&2
  echo "containers still present:" >&2
  for svc in "${services[@]}"; do
    docker ps -a --filter "name=^${svc}\$" --format '{{.Names}}' >&2 || true
  done
  echo "retry: $retry" >&2
  exit 1
fi

remaining=""
for svc in "${services[@]}"; do
  found="$(docker ps -a --filter "name=^${svc}\$" --format '{{.Names}}')"
  [ -z "$found" ] || remaining="${remaining}${found}"$'\n'
done
if [ -n "$remaining" ]; then
  echo "error: containers remain for '$name'; tester state was kept" >&2
  printf 'containers still present:\n%s' "$remaining" >&2
  echo "retry: $retry" >&2
  exit 1
fi
# The dashboard and phone images were built for this tester's base paths;
# nobody else can use them.
docker image rm "$DASHBOARD_IMAGE:$name" "$PHONE_IMAGE:$name" >/dev/null 2>&1 || true

rm -f "testers/$name.yml" "testers/$name.env" "routes/$name.caddy"
render_override
reload_caddy

if [ -d "data/$name" ]; then
  if [ -n "$archive" ]; then
    mkdir -p data/_removed
    dest="data/_removed/$name-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "data/$name" "$dest"
    echo "tester data archived at deploy/$dest"
  else
    rm -rf -- "data/$name"
    echo "permanently deleted deploy/data/$name (database, evidence and dashboard data)"
  fi
fi
echo "removed $name; its token no longer works."
