const out: Record<string,string> = {};
async function t(l: string, f: () => unknown) { try { out[l] = 'OK ' + String(await f()).slice(0,80); } catch (e) { out[l] = 'ERR ' + String(e).slice(0,100); } }
Deno.serve(async () => {
  await t('write_tmp', () => Deno.writeTextFile('/tmp/a', 'x'));
  await t('write_home_deno', () => Deno.writeTextFile('/home/deno/a', 'x'));
  await t('write_etc', () => Deno.writeTextFile('/etc/a', 'x'));
  await t('write_root', () => Deno.writeTextFile('/a', 'x'));
  await t('fetch_https', async () => (await fetch('https://example.com')).status);
  return Response.json(out);
});
