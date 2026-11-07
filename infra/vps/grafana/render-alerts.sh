#!/bin/sh
# Render the pilot's route latency alert with its threshold.
#
#   ROUTE_P95_THRESHOLD_SECONDS=<seconds> render-alerts.sh > latency.rules.yaml
#
# The availability alert needs no threshold and is used as it stands
# (availability.rules.yaml).
#
# The threshold has no default on purpose: it is to be set from the p95 the
# pilot is actually observed to have, not guessed. Refuses anything that is not
# a positive decimal number of seconds.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"

threshold="${ROUTE_P95_THRESHOLD_SECONDS:-}"
if [ -z "$threshold" ]; then
    echo "render-alerts: set ROUTE_P95_THRESHOLD_SECONDS (seconds, from observed production p95)" >&2
    exit 2
fi
case "$threshold" in
    *[!0-9.]* | .* | *. | *.*.* )
        echo "render-alerts: ROUTE_P95_THRESHOLD_SECONDS must be a decimal number of seconds, got '$threshold'" >&2
        exit 2
        ;;
esac
if ! awk -v t="$threshold" 'BEGIN { exit !(t > 0) }'; then
    echo "render-alerts: ROUTE_P95_THRESHOLD_SECONDS must be greater than zero" >&2
    exit 2
fi

sed "s/\${ROUTE_P95_THRESHOLD_SECONDS}/$threshold/" "$HERE/latency.rules.yaml.template"
