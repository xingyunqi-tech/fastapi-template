import http from 'k6/http';
import { check, fail } from 'k6';
import crypto from 'k6/crypto';

const BASE_URL = __ENV.BASE_URL;
const ENDPOINT = __ENV.ENDPOINT;
const VUS = Number(__ENV.VUS || 10);
const WARMUP_S = Number(__ENV.WARMUP_S || 10);
const DURATION_S = Number(__ENV.DURATION_S || 30);
const SUMMARY_FILE = __ENV.SUMMARY_FILE || '/out/summary.json';

const PASSWORD = crypto.md5('Secret00', 'hex');
const USER = 'john.doe@example.com';
const ADMIN = 'admin@example.com';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

const main = '{phase:main}';

export const options = {
  scenarios: {
    bench: {
      executor: 'constant-vus',
      vus: VUS,
      duration: `${WARMUP_S + DURATION_S}s`,
      gracefulStop: '5s',
    },
  },
  setupTimeout: '120s',
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    [`http_reqs${main}`]: ['count>=0'],
    [`http_req_failed${main}`]: ['rate<=1'],
    [`http_req_duration${main}`]: ['max>=0'],
    [`checks${main}`]: ['rate>=0'],
  },
};

function login(email) {
  const res = http.post(
    `${BASE_URL}/api/v1/auth/email/login`,
    JSON.stringify({ email, password: PASSWORD }),
    { headers: JSON_HEADERS, tags: { phase: 'setup' } },
  );
  if (res.status !== 200) {
    fail(`login ${email}: ${res.status} ${res.body}`);
  }
  return res.json('data');
}

export function setup() {
  const data = { startedAt: Date.now() };
  if (ENDPOINT === 'refresh') {
    data.refreshTokens = Array.from({ length: VUS }, () => login(USER).refreshToken);
  } else if (ENDPOINT === 'me') {
    data.token = login(USER).token;
  } else if (ENDPOINT === 'users') {
    data.token = login(ADMIN).token;
  } else if (ENDPOINT !== 'login') {
    fail(`unknown ENDPOINT: ${ENDPOINT}`);
  }
  return data;
}

let refreshToken;

function phase(data) {
  return Date.now() - data.startedAt < WARMUP_S * 1000 ? 'warmup' : 'main';
}

function bearer(token, tags) {
  return { headers: { Authorization: `Bearer ${token}` }, tags };
}

export default function (data) {
  const tags = { phase: phase(data), endpoint: ENDPOINT };
  let res;
  if (ENDPOINT === 'login') {
    res = http.post(
      `${BASE_URL}/api/v1/auth/email/login`,
      JSON.stringify({ email: USER, password: PASSWORD }),
      { headers: JSON_HEADERS, tags },
    );
  } else if (ENDPOINT === 'refresh') {
    if (refreshToken === undefined) {
      refreshToken = data.refreshTokens[__VU - 1];
    }
    res = http.post(`${BASE_URL}/api/v1/auth/refresh`, null, bearer(refreshToken, tags));
    if (res.status === 200) {
      refreshToken = res.json('data.refreshToken');
    }
  } else if (ENDPOINT === 'me') {
    res = http.get(`${BASE_URL}/api/v1/auth/me`, bearer(data.token, tags));
  } else {
    res = http.get(`${BASE_URL}/api/v1/users?limit=10`, bearer(data.token, tags));
  }
  check(res, { 'status is 200': (r) => r.status === 200 }, tags);
}

function metric(data, name) {
  return data.metrics[`${name}${main}`]?.values ?? {};
}

export function handleSummary(data) {
  const reqs = metric(data, 'http_reqs');
  const duration = metric(data, 'http_req_duration');
  const failed = metric(data, 'http_req_failed');
  const result = {
    endpoint: ENDPOINT,
    vus: VUS,
    requests: reqs.count ?? 0,
    rps: (reqs.count ?? 0) / DURATION_S,
    p50: duration.med,
    p95: duration['p(95)'],
    p99: duration['p(99)'],
    max: duration.max,
    errorRate: failed.rate ?? 0,
  };
  return {
    stdout: `${JSON.stringify(result)}\n`,
    [SUMMARY_FILE]: JSON.stringify(result),
  };
}
