// Which literal spellings of the cloud metadata address the worker's deny_net refuses. NotCapable means refused by
// deny_net; anything else (a timeout here, since the address is unreachable on the test host) means the check let
// the connection attempt through.
const FORMS = [
  '169.254.169.254', '2851995902', '0xa9fea9fe', '0251.0376.0251.0376', '169.254.43518', '169.254.169.254.',
  '[::ffff:169.254.169.254]', '[::ffff:a9fe:a9fe]', '[0:0:0:0:0:ffff:a9fe:a9fe]', '[fd00:ec2::254]',
  '[fd00:0ec2:0:0:0:0:0:254]', '[64:ff9b::a9fe:a9fe]', '169.254.169.254.sslip.io', 'metadata.google.internal',
];
Deno.serve(async () => {
  const out: Record<string, string> = {};
  for (const form of FORMS) {
    try { out[form] = 'HTTP ' + (await fetch('http://' + form + '/', { signal: AbortSignal.timeout(1500) })).status; }
    catch (e) { out[form] = String(e).includes('NotCapable') ? 'BLOCKED NotCapable' : 'NOT BLOCKED ' + String(e).slice(0, 70); }
  }
  return Response.json(out);
});
