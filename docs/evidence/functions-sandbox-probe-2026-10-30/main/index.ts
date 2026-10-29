const FUNCTIONS='/home/deno/functions';
Deno.serve(async (req: Request) => {
  const url = new URL(req.url);
  const name = url.pathname.split('/')[1];
  const variant = req.headers.get('x-variant') ?? 'cur';
  const base: Record<string, unknown> = {
    servicePath: `${FUNCTIONS}/${name}`, memoryLimitMb: 150, workerTimeoutMs: 60_000,
    cpuTimeSoftLimitMs: 10_000, cpuTimeHardLimitMs: 20_000, noModuleCache: false, forceCreate: false,
    envVars: [['SUPABASE_URL','http://x'],['PLAIN','1']],
  };
  let options: Record<string, unknown>;
  if (variant === 'cur') options = { ...base, importMapPath: null, netAccessDisabled: true };
  else if (variant === 'noneta') options = { ...base };
  else if (variant === 'hard') options = { ...base, tmpFsConfig: { quota: 32 * 1024 * 1024 },
    permissions: { allow_read: ['/tmp'], allow_write: ['/tmp'], allow_net: ['example.com:443', 'registry.npmjs.org:443'] } };
  else if (variant === 'tmponly') options = { ...base, tmpFsConfig: { quota: 32 * 1024 * 1024 } };
  else if (variant === 'deny') options = { ...base, tmpFsConfig: { quota: 32 * 1024 * 1024 },
    permissions: { deny_net: ['169.254.0.0/16', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '100.64.0.0/10'] } };
  else if (variant === 'deny_host') options = { ...base, permissions: { deny_net: ['172.17.0.1', '169.254.169.254', '127.0.0.1', 'localhost'] } };
  else if (variant === 'deny2') options = { ...base, tmpFsConfig: { quota: 32 * 1024 * 1024 },
    permissions: { allow_net: [], allow_env: [], allow_read: ['/tmp'], allow_write: ['/tmp'],
      deny_net: ['169.254.169.254', '172.17.0.1', '127.0.0.1', '10.0.0.0/8'] } };
  else if (variant === 'deny3') options = { ...base, permissions: { allow_net: [], allow_env: [], allow_read: ['/tmp'], allow_write: ['/tmp'],
      deny_net: ['169.254.169.254', '172.17.0.1', '127.0.0.1'] } };
  else return new Response('variant', { status: 400 });
  try {
    const worker = await (globalThis as any).EdgeRuntime.userWorkers.create(options);
    return await worker.fetch(req);
  } catch (e) {
    return new Response(JSON.stringify({ mainError: String(e) }), { status: 500 });
  }
});
