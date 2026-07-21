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
// Tunables (env): VUS, DURATION, QUERIES_PER_VU, SLEEP.
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';
import { SharedArray } from 'k6/data';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const USER = __ENV.DW_USER || 'ceo';
const PASS = __ENV.DW_PASS || 'Admin@2024';
const QUERIES_PER_VU = Number(__ENV.QUERIES_PER_VU || 5);
const THINK = Number(__ENV.SLEEP || 1);

// Custom metrics per phase so the baseline breaks down by operation.
const loginDur = new Trend('dw_login_duration', true);
const uploadDur = new Trend('dw_upload_duration', true);
const queryDur = new Trend('dw_query_duration', true);
const bizErrors = new Rate('dw_business_errors'); // non-2xx at the app level
const queriesRun = new Counter('dw_queries_run');

// Load the sample CSV once, shared across all VUs (kept in repo alongside this
// script so the test is self-contained).
const csv = new SharedArray('dataset', function () {
  return [open('./sample.csv')];
})[0];

// Aligned to loadtest/sample.csv columns (total_amount, region, category, ...).
const QUESTIONS = [
  'How many rows are in the data?',
  'What is the total amount across all orders?',
  'Show the top 5 orders by total amount',
  'What is the average total amount per region?',
  'Which region has the highest total amount?',
];

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
    // Capacity SLOs — a run that breaches these exits non-zero.
    http_req_failed: ['rate<0.01'], // < 1% transport errors
    dw_business_errors: ['rate<0.01'], // < 1% app-level errors
    dw_login_duration: ['p(95)<1500'],
    dw_upload_duration: ['p(95)<3000'],
    dw_query_duration: ['p(95)<8000'], // LLM-bound; loosen/tighten per hardware
  },
};

function login() {
  const res = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ username: USER, password: PASS }),
    { headers: { 'Content-Type': 'application/json' }, tags: { phase: 'login' } },
  );
  loginDur.add(res.timings.duration);
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
  queriesRun.add(1);
  const ok = check(res, {
    'query 200': (r) => r.status === 200,
    'query not error type': (r) => !(r.json() && r.json().type === 'error'),
  });
  bizErrors.add(!ok);
}

export default function () {
  let token;
  let sessionId;

  group('login', () => {
    token = login();
  });
  if (!token) {
    sleep(THINK);
    return;
  }

  group('upload', () => {
    sessionId = upload(token);
  });
  if (!sessionId) {
    sleep(THINK);
    return;
  }

  group('query', () => {
    for (let i = 0; i < QUERIES_PER_VU; i++) {
      query(token, sessionId, QUESTIONS[i % QUESTIONS.length]);
      sleep(THINK);
    }
  });
}
