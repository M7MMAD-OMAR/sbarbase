// (e) sandbox probe: what a function must not be able to do (adapted from functions-sandbox-probe-2026-10-30).
const out: Record<string, string> = {};
async function t(label: string, f: () => Promise<unknown> | unknown) {
  try { const v = await f(); out[label] = 'OK ' + String(v).slice(0, 120); } catch (e) { out[label] = 'DENIED ' + String(e).slice(0, 160); }
}
const timeout = () => AbortSignal.timeout(2500);
Deno.serve(async () => {
  await t('fetch_metadata_169_254_169_254', async () => (await fetch('http://169.254.169.254/', { signal: timeout() })).status);
  await t('metadata_via_dns_name_rebinding', async () => { try { return 'HTTP ' + (await fetch('http://169.254.169.254.sslip.io/', { signal: timeout() })).status; } catch (e) { if (String(e).includes('NotCapable')) throw e; return 'REACHED-OR-FAILED ' + String(e).slice(-120); } });
  await t('fetch_file_proc_environ', async () => (await (await fetch('file:///proc/1/environ')).text()).length);
  await t('read_proc_environ', () => Deno.readTextFile('/proc/1/environ').then((s) => s.length));
  await t('read_proc_self_status', () => Deno.readTextFile('/proc/self/status').then((s) => s.length));
  await t('read_run_secrets', () => Deno.readTextFile('/run/sbarbase/secrets.json'));
  await t('read_other_function', () => Deno.readTextFile('/home/deno/functions/v1/other/index.ts'));
  await t('read_functions_json', () => Deno.readTextFile('/home/deno/functions/functions.json'));
  await t('read_main_service', () => Deno.readTextFile('/home/deno/main/index.ts'));
  await t('read_etc_passwd', () => Deno.readTextFile('/etc/passwd').then((s) => s.length));
  await t('write_etc', () => Deno.writeTextFile('/etc/pwned', 'x'));
  await t('write_home', () => Deno.writeTextFile('/home/deno/pwned', 'x'));
  await t('write_tmp_small', () => Deno.writeTextFile('/tmp/a', 'x'));
  await t('read_tmp_small', () => Deno.readTextFile('/tmp/a'));
  await t('deno_command', () => new Deno.Command('id').outputSync());
  await t('env_sb_jwt', () => Deno.env.get('SB_JWT_SECRET') ?? 'undefined');
  await t('tmp_fill_200mb', async () => {
    const chunk = new Uint8Array(1024 * 1024); const f = await Deno.open('/tmp/big', { write: true, create: true });
    let n = 0; try { for (; n < 200; n++) await f.write(chunk); } finally { f.close(); } return n + ' MiB written';
  });
  return Response.json(out);
});
