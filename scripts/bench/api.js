import http from 'k6/http';
import { check, fail } from 'k6';

const BASE_URL = __ENV.BASE_URL;
const ENDPOINT = __ENV.ENDPOINT;
const VUS = Number(__ENV.VUS || 10);
const WARMUP_S = Number(__ENV.WARMUP_S || 10);
const DURATION_S = Number(__ENV.DURATION_S || 30);
const SUMMARY_FILE = __ENV.SUMMARY_FILE || '/out/summary.json';

const EMAIL = 'admin@example.com';
const PASSWORD = 'Secret00';
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
  },
};

// TRUSTED_PROXIES=1：每次登录换一个来源 IP，共享规则 auth-login 按 IP 计数
function clientIp() {
  const octet = () => Math.floor(Math.random() * 254) + 1;
  return `198.18.${octet()}.${octet()}`;
}

function login(tags) {
  return http.post(`${BASE_URL}/api/v1/auth/email/login`, JSON.stringify({ email: EMAIL, password: PASSWORD }), {
    headers: { ...JSON_HEADERS, 'X-Forwarded-For': clientIp() },
    tags,
  });
}

function bearer(token, tags) {
  return { headers: { ...JSON_HEADERS, Authorization: `Bearer ${token}` }, tags };
}

export function setup() {
  const res = login({ phase: 'setup' });
  if (res.status !== 200) {
    fail(`login: ${res.status} ${res.body}`);
  }
  const token = res.json('data.accessToken');
  if (ENDPOINT === 'notes') {
    for (let i = 0; i < 20; i += 1) {
      http.post(`${BASE_URL}/api/v1/notes`, JSON.stringify({ title: `bench ${i}` }), bearer(token, { phase: 'setup' }));
    }
  } else if (!['login', 'me', 'users'].includes(ENDPOINT)) {
    fail(`unknown ENDPOINT: ${ENDPOINT}`);
  }
  return { token, startedAt: Date.now() };
}

export default function (data) {
  const tags = { phase: Date.now() - data.startedAt < WARMUP_S * 1000 ? 'warmup' : 'main' };
  let res;
  if (ENDPOINT === 'login') {
    res = login(tags);
  } else if (ENDPOINT === 'me') {
    res = http.get(`${BASE_URL}/api/v1/me`, bearer(data.token, tags));
  } else if (ENDPOINT === 'users') {
    res = http.get(`${BASE_URL}/api/v1/users?pageSize=10`, bearer(data.token, tags));
  } else {
    res = http.get(`${BASE_URL}/api/v1/notes?pageSize=10`, bearer(data.token, tags));
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
