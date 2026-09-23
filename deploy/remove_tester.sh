#!/usr/bin/env bash
# Remove one tester: stop and delete their containers, route and token.
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

# Stop while the service is still in the override, so compose can find it.
compose rm -sfv "backend-$name" "dashboard-$name" 2>/dev/null \
  || compose rm -sfv "backend-$name" || true
# The dashboard image was built for this tester's base path; nobody else can use it.
docker image rm "$DASHBOARD_IMAGE:$name" >/dev/null 2>&1 || true

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
