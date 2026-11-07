#!/bin/sh
# Load the pilot's alert rules into Grafana Cloud's Prometheus (its Mimir ruler).
#
#   ROUTE_P95_THRESHOLD_SECONDS=<seconds> \
#   MIMIR_ADDRESS=https://prometheus-prod-NN-prod-REGION.grafana.net \
#   MIMIR_TENANT_ID=<Prometheus instance ID> \
#   MIMIR_API_KEY=<access policy token with rules:read and rules:write> \
#     load-alerts.sh
#
# MIMIR_ADDRESS is the Prometheus URL from the Grafana Cloud portal without its
# /api/prom/push path. The token is not the one Alloy holds: Alloy's can only
# write metrics, and this one never needs to live on the pilot host.
#
# Runs mimirtool from its pinned image, so the only requirement is Docker.
# `rules load` replaces groups of the same name, so running it again with a new
# threshold updates the rule in place. See docs/deployment/OBSERVABILITY.md.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
MIMIRTOOL_IMAGE="grafana/mimirtool:3.2.2"

for name in MIMIR_ADDRESS MIMIR_TENANT_ID MIMIR_API_KEY; do
    eval "value=\${$name:-}"
    if [ -z "$value" ]; then
        echo "load-alerts: set $name (see the header of this script)" >&2
        exit 2
    fi
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# mimirtool wants a namespace; promtool, which tests the same rules, refuses the
# key. So it is added here, at load time only.
{
    echo "namespace: pathable"
    "$HERE/render-alerts.sh"
} > "$work/pathable.rules.yaml"

docker run --rm \
    -e MIMIR_ADDRESS -e MIMIR_TENANT_ID -e MIMIR_API_KEY \
    -v "$work:/rules:ro" \
    "$MIMIRTOOL_IMAGE" rules load /rules/pathable.rules.yaml

echo "load-alerts: loaded the pathable namespace with a route p95 threshold of ${ROUTE_P95_THRESHOLD_SECONDS}s"
