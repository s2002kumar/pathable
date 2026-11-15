// PathAble AI — the load workload for the public pilot, in k6.
//
// Run by run_stage.py on the pilot host, never by hand against an address it
// did not choose. See docs/deployment/VPS.md#measuring-capacity.
//
// Every virtual user is a closed loop with no think time: it sends a request,
// waits for the whole answer, checks it, and sends the next. So VUS is the
// number of requests in flight at every moment — far heavier than the same
// number of people using the planner, who pause between requests.
//
// Three workloads (MODE):
//
//   route   POST /api/v1/routes/compare for the established smoke journey
//           (journey.json), wheelchair profile. Every 200 is checked against
//           the recorded answer: shortest 401.3 m with 3 stairways, accessible
//           503.7 m with none, the expected dataset, no model predictions.
//           Any drift aborts the stage.
//   mix     The same, taking the smoke journey and three longer journeys from
//           the evaluation corpus in turn (journey.json "mix", 1.3-1.6 km).
//           Each answer must equal the one production gave probe.py baseline
//           sequentially in the same run.
//   search  POST /api/v1/geocode/search, alternating a submitted search for a
//           journey end (checked: the expected place is among the matches) with
//           as-you-type prefixes.
//
// Who the API thinks is asking. The API rate-limits each client (30 routes and
// 60 searches a minute, KI-13), and behind Caddy the client is the right-most
// X-Forwarded-For entry. This script runs inside the host's edge network and
// sets that header itself, one address per request in turn from the three
// RFC 5737 documentation ranges — addresses no real visitor can have — so 762
// distinct clients share the load and none comes near its own limit. A 429 here
// is therefore a harness fault, and is counted as a failure.

import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';
const STAGE = __ENV.STAGE || 'unnamed';
const MODE = __ENV.MODE || 'route';
const VUS = Number(__ENV.VUS || '1');
const DURATION = __ENV.DURATION || '60s';
const OUT = __ENV.OUT_DIR || '/out';
// With RATE set, the stage is open-loop instead: RATE requests a second arrive
// whether or not earlier ones have been answered (as independent visitors do),
// and VUS is the most that may be in flight at once.
const RATE = __ENV.RATE ? Number(__ENV.RATE) : null;

const journey = JSON.parse(open('./journey.json'));

// Requests in flight when the duration ends are allowed to finish: at 50 in
// flight one can wait several seconds, and cutting it off would count a slow
// answer as a failure.
const GRACEFUL_STOP = '40s';

export const options = {
  scenarios: {
    load:
      RATE === null
        ? { executor: 'constant-vus', vus: VUS, duration: DURATION, gracefulStop: GRACEFUL_STOP }
        : {
            executor: 'constant-arrival-rate',
            rate: RATE,
            timeUnit: '1s',
            duration: DURATION,
            preAllocatedVUs: VUS,
            maxVUs: VUS,
            gracefulStop: GRACEFUL_STOP,
          },
  },
  summaryTrendStats: ['count', 'min', 'med', 'avg', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    // A wrong answer under load invalidates every number the stage produced.
    semantic_mismatches: [{ threshold: 'count<1', abortOnFail: true }],
    // Errors, timeouts and 429s together. Evaluated after ten seconds so the
    // first slow answer of a cold stage cannot abort it alone.
    http_req_failed: [{ threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '10s' }],
  },
  // Not a browser: no cookies, and the connection is reused like a client's.
  noCookiesReset: true,
  userAgent: `pathable-load/1 (${STAGE})`,
};

const semanticMismatches = new Counter('semantic_mismatches');
const semanticMatches = new Counter('semantic_matches');
const rateLimited = new Counter('rate_limited');
const serverErrors = new Counter('server_errors');
const transportErrors = new Counter('transport_errors');
// The server's own computation_ms, summed over the two routes of a comparison.
const serverComputationMs = new Trend('server_computation_ms');
const standardDistanceM = new Trend('standard_distance_m');
const accessibleDistanceM = new Trend('accessible_distance_m');
const responseBytes = new Trend('response_bytes');

// 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24, hosts .1 to .254.
const DOCUMENTATION_PREFIXES = ['192.0.2.', '198.51.100.', '203.0.113.'];

function syntheticClient() {
  const index = exec.scenario.iterationInTest % (DOCUMENTATION_PREFIXES.length * 254);
  return `${DOCUMENTATION_PREFIXES[Math.floor(index / 254)]}${(index % 254) + 1}`;
}

function post(path, body, endpoint) {
  return http.post(`${BASE_URL}${path}`, JSON.stringify(body), {
    headers: {
      'Content-Type': 'application/json',
      'X-Forwarded-For': syntheticClient(),
    },
    timeout: '30s',
    tags: { endpoint },
  });
}

function countRefusal(response) {
  if (response.status === 0) {
    transportErrors.add(1);
  } else if (response.status === 429) {
    rateLimited.add(1);
  } else if (response.status >= 500) {
    serverErrors.add(1);
  }
}

function round1(value) {
  return typeof value === 'number' ? Math.round(value * 10) / 10 : null;
}

function mismatch(detail) {
  semanticMismatches.add(1);
  console.error(`semantic mismatch: ${detail}`);
}

// The smoke journey and, for MODE=mix, the longer journeys probe.py baseline
// routed sequentially in the same run, with the answers production gave then.
const SMOKE = {
  key: 'smoke',
  origin: journey.origin,
  destination: journey.destination,
  standard: journey.expected.standard,
  accessible: journey.expected.accessible,
};
const MIX = [SMOKE, ...(journey.mix || [])];

function routeIteration(target) {
  const response = post(
    '/api/v1/routes/compare',
    {
      region: journey.region,
      origin: target.origin,
      destination: target.destination,
      profile: journey.profile,
    },
    'compare',
  );
  if (response.status !== 200) {
    countRefusal(response);
    return;
  }
  responseBytes.add(response.body.length, { journey: target.key });
  let payload;
  try {
    payload = response.json();
  } catch (error) {
    mismatch(`unparseable 200 body: ${error}`);
    return;
  }
  const standard = payload.standard_route || {};
  const accessible = payload.accessible_route || {};
  const dataset = payload.dataset || {};
  serverComputationMs.add((standard.computation_ms || 0) + (accessible.computation_ms || 0), {
    journey: target.key,
  });
  if (target.key === 'smoke') {
    // Spread of the raw distances: min and max equal means no drift at all.
    standardDistanceM.add(standard.distance_m || 0);
    accessibleDistanceM.add(accessible.distance_m || 0);
  }

  const got = {
    journey: target.key,
    standard: [round1(standard.distance_m), standard.stairway_count],
    accessible: [round1(accessible.distance_m), accessible.stairway_count],
    dataset: dataset.dataset_id,
    ml: payload.ml_predictions_used,
  };
  const ok =
    got.standard[0] === target.standard.distance_m &&
    got.standard[1] === target.standard.stairway_count &&
    got.accessible[0] === target.accessible.distance_m &&
    got.accessible[1] === target.accessible.stairway_count &&
    got.dataset === journey.expected.dataset_id &&
    got.ml === false;
  if (ok) {
    semanticMatches.add(1);
  } else {
    mismatch(JSON.stringify(got));
  }
}

// A submitted search for each journey end, then two as-you-type prefixes.
const SEARCHES = [
  { query: journey.search.start_query, expect: journey.search.start_label },
  { query: 'Davis Cen', autocomplete: true },
  { query: journey.search.end_query, expect: journey.search.end_label },
  { query: 'Student Li', autocomplete: true },
];

function searchIteration() {
  const search = SEARCHES[exec.scenario.iterationInTest % SEARCHES.length];
  const body = { region: journey.region, query: search.query };
  if (search.autocomplete) {
    body.autocomplete = true;
  }
  const response = post('/api/v1/geocode/search', body, 'search');
  if (response.status !== 200) {
    countRefusal(response);
    return;
  }
  responseBytes.add(response.body.length);
  if (!search.expect) {
    return;
  }
  let payload;
  try {
    payload = response.json();
  } catch (error) {
    mismatch(`unparseable 200 body: ${error}`);
    return;
  }
  const labels = (payload.matches || []).map((match) => match.label);
  if (payload.provider === 'local' && labels.includes(search.expect)) {
    semanticMatches.add(1);
  } else {
    mismatch(`search ${search.query}: provider ${payload.provider}, labels ${labels}`);
  }
}

export default function () {
  if (MODE === 'search') {
    searchIteration();
  } else if (MODE === 'mix') {
    routeIteration(MIX[exec.scenario.iterationInTest % MIX.length]);
  } else {
    routeIteration(SMOKE);
  }
}

export function handleSummary(data) {
  const metric = (name, stat) => {
    const found = data.metrics[name];
    return found && found.values[stat] !== undefined ? found.values[stat] : null;
  };
  const line =
    `${STAGE} ${MODE} vus=${VUS} requests=${metric('http_reqs', 'count')} ` +
    `rate=${metric('http_reqs', 'rate')}/s failed=${metric('http_req_failed', 'rate')} ` +
    `p50=${metric('http_req_duration', 'med')}ms p95=${metric('http_req_duration', 'p(95)')}ms ` +
    `p99=${metric('http_req_duration', 'p(99)')}ms ` +
    `mismatches=${metric('semantic_mismatches', 'count') || 0}\n`;
  return {
    stdout: line,
    [`${OUT}/${STAGE}-k6-summary.json`]: JSON.stringify(data, null, 2),
  };
}
