// DataWhisper capacity baseline — login → upload → query loop.
//
// Establishes a p95 latency / error-rate baseline against a running stack
// (issue #2 / M11). Each VU logs in once, uploads a dataset once, then loops
// asking questions against that session. Thresholds fail the run (non-zero
// exit) if latency or error budgets are exceeded, so this doubles as a CI gate.
//
// Usage:
//   BASE_URL=http://localhost:8000 \
//   DW_USER=ceo DW_PASS='Admin@2024' \
//   k6 run loadtest/k6-login-upload-query.js
//
// Tunables (env): VUS, DURATION, QUERIES_PER_VU, SLEEP, CACHE_MODE.
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter, Gauge } from 'k6/metrics';
import { SharedArray } from 'k6/data';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const USER = __ENV.DW_USER || 'ceo';
const PASS = __ENV.DW_PASS || 'Admin@2024';
const QUERIES_PER_VU = Number(__ENV.QUERIES_PER_VU || 5);
const THINK = Number(__ENV.SLEEP || 1);

// Which question is being answered (issue #26). The LLM cache is keyed on
// model+prompt, so a run over a handful of repeated questions stops reaching the
// LLM after the first few iterations and reports cache-lookup latency as if it
// were inference latency — 61ms vs 38.3s on the same stack. That number is not
// wrong, it just answers a different question, and nothing used to say which.
//
//   cold  — sizes LLM capacity. Needs LLM_CACHE_ENABLED=false on the TARGET
//           stack; k6 cannot turn the cache off from here, so the run verifies
//           it from the server's own counters and fails if it was lied to.
//   warm  — deliberately measures the steady state real users see (they do
//           repeat questions). Asserts the cache actually was warm.
//   auto  — no assertion. Reports what happened; the default, so existing
//           invocations behave as before but are no longer silent about mode.
const CACHE_MODE = (__ENV.CACHE_MODE || 'auto').toLowerCase();
const METRICS_URL = __ENV.METRICS_URL || `${BASE_URL}/metrics`;

// Custom metrics per phase so the baseline breaks down by operation.
const loginDur = new Trend('dw_login_duration', true);
const uploadDur = new Trend('dw_upload_duration', true);
const queryDur = new Trend('dw_query_duration', true);
const bizErrors = new Rate('dw_business_errors'); // non-2xx at the app level
const queriesRun = new Counter('dw_queries_run');
// 429s are counted separately from real errors. A load generator hits the API
// from ONE source IP while slowapi's limits are per-IP burst protection, so a
// stack left on production rate limits will 429 almost everything — which looks
// exactly like a capacity failure unless it is called out by name. See the
// "Rate limits" section of README.md.
const rateLimited = new Counter('dw_rate_limited');
// Share of this run's LLM lookups served from cache, computed in teardown from
// the server's llm_cache_{hits,misses}_total deltas. This is the metric that
// tells you what the latency numbers above actually mean.
const cacheHitRatio = new Gauge('dw_cache_hit_ratio');

// Load the sample CSV once, shared across all VUs (kept in repo alongside this
// script so the test is self-contained).
const csv = new SharedArray('dataset', function () {
  return [open('./sample.csv')];
})[0];

// Aligned to loadtest/sample.csv columns (total_amount, region, category, ...).
// Kept wide on purpose: with only a few questions the cache is warm within
// seconds regardless of CACHE_MODE, which is how the original 5-question list
// turned every long run into a cache benchmark (issue #26).
const QUESTIONS = [
  'How many rows are in the data?',
  'What is the total amount across all orders?',
  'Show the top 5 orders by total amount',
  'What is the average total amount per region?',
  'Which region has the highest total amount?',
  'What is the total amount per category?',
  'Which category has the most orders?',
  'What is the smallest order by total amount?',
  'What is the largest order by total amount?',
  'How many distinct regions are there?',
  'How many distinct categories are there?',
  'Show the bottom 5 orders by total amount',
  'What is the median total amount?',
  'What is the average total amount per category?',
  'Which region has the fewest orders?',
  'Show the total amount by region sorted descending',
  'How many orders are above the average total amount?',
  'What is the sum of total amount for the top category?',
  'List the regions with their order counts',
  'What is the average order value overall?',
];

// A declared cache mode is only worth something if the run checks it. These
// thresholds turn "I meant to measure cold inference" into a pass/fail, so a
// stack that quietly kept its cache on can't produce a fantasy baseline.
const cacheThresholds = {
  cold: { dw_cache_hit_ratio: ['value<0.05'] },
  warm: { dw_cache_hit_ratio: ['value>0.5'] },
  auto: {},
}[CACHE_MODE] || {};

export const options = {
  scenarios: {
    steady: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: Number(__ENV.VUS || 10) }, // ramp up
        { duration: __ENV.DURATION || '2m', target: Number(__ENV.VUS || 10) }, // hold
        { duration: '20s', target: 0 }, // ramp down
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    // Any 429 means the environment is throttling the generator, so the run
    // measures the rate limiter rather than the stack. Fail loudly and by name
    // instead of letting it read as an error-rate problem.
    dw_rate_limited: ['count<1'],
    // Capacity SLOs — a run that breaches these exits non-zero.
    http_req_failed: ['rate<0.01'], // < 1% transport errors
    dw_business_errors: ['rate<0.01'], // < 1% app-level errors
    dw_login_duration: ['p(95)<1500'],
    dw_upload_duration: ['p(95)<3000'],
    dw_query_duration: ['p(95)<8000'], // LLM-bound; loosen/tighten per hardware
    ...cacheThresholds,
  },
};

// ── Cache-mode accounting ───────────────────────────────────────────────────
// Reads the two Prometheus counters straight out of /metrics. Parsed by hand
// because k6 has no Prometheus client and this is two lines of a text format.
function readCacheCounters() {
  const res = http.get(METRICS_URL, { tags: { phase: 'metrics' } });
  if (res.status !== 200) return null;
  const read = (name) => {
    const m = res.body.match(new RegExp(`^${name}\\s+([0-9.e+]+)$`, 'm'));
    return m ? Number(m[1]) : null;
  };
  const hits = read('llm_cache_hits_total');
  const misses = read('llm_cache_misses_total');
  return hits === null || misses === null ? null : { hits, misses };
}

export function setup() {
  const before = readCacheCounters();
  if (!before) {
    console.warn(
      'Could not read llm_cache_*_total from /metrics — cache mode cannot be ' +
        'verified for this run. Is METRICS_ENABLED=true on the target?',
    );
  }
  return { before };
}

export function teardown(data) {
  const after = readCacheCounters();
  if (!data.before || !after) return;

  const hits = after.hits - data.before.hits;
  const misses = after.misses - data.before.misses;
  const total = hits + misses;
  if (total <= 0) {
    console.warn('No LLM cache lookups recorded during this run.');
    return;
  }

  const ratio = hits / total;
  cacheHitRatio.add(ratio);

  let verdict;
  if (CACHE_MODE === 'cold' && ratio >= 0.05) {
    verdict =
      'THIS RUN DID NOT MEASURE WHAT IT CLAIMED. CACHE_MODE=cold asks for LLM ' +
      'capacity, but the target served part of the load from cache — set ' +
      'LLM_CACHE_ENABLED=false on the stack under test and rerun. The ' +
      'dw_cache_hit_ratio threshold has failed this run for exactly this reason.';
  } else if (ratio > 0.5) {
    verdict =
      'These query latencies are mostly CACHE LOOKUPS, not inference. They ' +
      'describe steady-state user experience, NOT LLM capacity. For capacity, ' +
      'rerun with LLM_CACHE_ENABLED=false on the target and CACHE_MODE=cold.';
  } else {
    verdict = 'These query latencies are mostly real inference.';
  }

  console.log(
    `\nCACHE MODE: ${CACHE_MODE} — ${(ratio * 100).toFixed(1)}% of ${total} LLM ` +
      `lookups served from cache (${misses} reached the model).\n${verdict}\n` +
      'Record this line alongside any baseline — a warm number and a cold ' +
      'number are not comparable.\n',
  );
}

// Separates "throttled" from "broken" so a misconfigured environment doesn't
// masquerade as a capacity result.
function noteRateLimit(res) {
  if (res.status === 429) {
    rateLimited.add(1);
    return true;
  }
  return false;
}

function login() {
  const res = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ username: USER, password: PASS }),
    { headers: { 'Content-Type': 'application/json' }, tags: { phase: 'login' } },
  );
  loginDur.add(res.timings.duration);
  noteRateLimit(res);
  const ok = check(res, {
    'login 200': (r) => r.status === 200,
    'login has token': (r) => !!(r.json() && r.json().access_token),
  });
  bizErrors.add(!ok);
  return ok ? res.json().access_token : null;
}

function upload(token) {
  const res = http.post(
    `${BASE_URL}/api/upload/`,
    { file: http.file(csv, 'sample.csv', 'text/csv') },
    { headers: { Authorization: `Bearer ${token}` }, tags: { phase: 'upload' } },
  );
  uploadDur.add(res.timings.duration);
  noteRateLimit(res);
  const ok = check(res, {
    'upload 200': (r) => r.status === 200,
    'upload has session': (r) => !!(r.json() && r.json().session_id),
  });
  bizErrors.add(!ok);
  return ok ? res.json().session_id : null;
}

function query(token, sessionId, question) {
  const res = http.post(
    `${BASE_URL}/api/query/`,
    JSON.stringify({ session_id: sessionId, question }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, tags: { phase: 'query' } },
  );
  queryDur.add(res.timings.duration);
  noteRateLimit(res);
  queriesRun.add(1);
  const ok = check(res, {
    'query 200': (r) => r.status === 200,
    'query not error type': (r) => !(r.json() && r.json().type === 'error'),
  });
  bizErrors.add(!ok);
}

// Per-VU state, initialised on the VU's first iteration and reused after that.
// Module scope in k6 is per-VU, not global, so each VU gets its own session.
//
// This MUST NOT move into the default function. Logging in and uploading on
// every iteration is what the first version did, and it made the test measure
// the per-IP rate limiter instead of capacity: every VU shares one source IP,
// so N VUs looping produce N×iterations logins against RATE_LIMIT_LOGIN
// (10/minute by default) and the run drowns in 429s. It also burns a fresh
// upload quota slot per iteration.
let vu = null;

export default function () {
  if (vu === null) {
    let token;
    group('login', () => {
      token = login();
    });
    if (!token) {
      sleep(THINK);
      return; // retry the handshake on the next iteration
    }

    let sessionId;
    group('upload', () => {
      sessionId = upload(token);
    });
    if (!sessionId) {
      sleep(THINK);
      return;
    }

    vu = { token, sessionId, offset: (__VU - 1) * QUERIES_PER_VU };
  }

  group('query', () => {
    for (let i = 0; i < QUERIES_PER_VU; i++) {
      // Offset by VU and iteration rather than always starting at question 0 —
      // otherwise every VU asks the same first QUERIES_PER_VU questions and the
      // pool being wide buys nothing.
      query(vu.token, vu.sessionId, QUESTIONS[(vu.offset + i) % QUESTIONS.length]);
      sleep(THINK);
    }
    vu.offset += QUERIES_PER_VU;
  });
}
