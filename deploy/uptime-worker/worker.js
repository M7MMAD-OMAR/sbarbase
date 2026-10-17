// External uptime check for a Sbarbase installation, run by Cloudflare every five minutes.
//
// The installation's own notifications cannot report that the whole server is down, so this
// Worker asks the public health route from outside. Two failed checks in a row send one Telegram
// message, and the first good check after that sends another; nothing repeats while the state
// holds. The bot token and the chat id are Worker secrets (wrangler secret put TELEGRAM_TOKEN and
// CHAT_ID), never in a file. Without it, or without CHAT_ID, the Worker only records the state.
// docs/reference/configuration.md, "Checking from outside".

const KEY = 'state';

async function check(url) {
  try {
    const response = await fetch(url, {redirect: 'manual', signal: AbortSignal.timeout(10_000), cf: {cacheTtl: 0}});
    if (response.status !== 200) return `HTTP ${response.status}`;
    const body = await response.json().catch(() => null);
    return body && body.status === 'ok' ? null : 'unexpected health answer';
  } catch (error) {
    return error && error.name === 'TimeoutError' ? 'no answer in 10 seconds' : 'unreachable';
  }
}

async function tell(env, text) {
  if (!env.TELEGRAM_TOKEN || !env.CHAT_ID) return false;
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({chat_id: env.CHAT_ID, text, disable_web_page_preview: true}),
    signal: AbortSignal.timeout(10_000),
  }).catch(() => null);
  return !!response && response.ok;
}

export async function run(env, now = Date.now()) {
  const previous = JSON.parse((await env.STATE.get(KEY)) ?? '{"down":false,"failures":0}');
  const failure = await check(env.HEALTH_URL);
  const state = {down: previous.down, failures: failure ? previous.failures + 1 : 0, at: now, last: failure};
  if (failure && !previous.down && state.failures >= 2) {
    state.down = true;
    state.since = now;
    await tell(env, `Sbarbase is down: ${env.HEALTH_URL} failed twice in a row (${failure}). Check the server.`);
  } else if (!failure && previous.down) {
    state.down = false;
    const minutes = previous.since ? Math.round((now - previous.since) / 60000) : null;
    await tell(env, `Sbarbase is back: ${env.HEALTH_URL} answers again${minutes === null ? '' : ` after about ${minutes} minutes`}.`);
  } else if (previous.down) {
    state.since = previous.since;
  }
  await env.STATE.put(KEY, JSON.stringify(state));
  return state;
}

export default {
  async scheduled(_event, env, context) { context.waitUntil(run(env)); },
  // GET shows the recorded state, for a quick look; it never runs a check or sends anything.
  async fetch(_request, env) {
    return new Response((await env.STATE.get(KEY)) ?? '{}', {headers: {'content-type': 'application/json', 'cache-control': 'no-store'}});
  },
};
