#!/usr/bin/env python3
"""Native role preparation and original SDK fixture, with no import-time effects.

The coordinator has not assigned a runtime role. This file may emit a plan or
fixture source, but cannot allocate resources or confer native acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from storage_write_settlement import IMAGE, SOURCE, Refused, closed, integer, text, UUID


CASES = (
    'sdk-new-upload', 'sdk-overwrite', 'sdk-delete', 'sdk-signed-upload',
    'sdk-signed-download', 'sdk-exact-byte-download', 'large-stream-upload',
    'disconnect-before-body-end', 'disconnect-after-file-before-db',
    'all-original-processes-ended', 'lost-stop-acknowledgement',
    'controller-interruption', 'controller-restart-fence', 'storage-recreation-fence',
    'same-version-tus-resume', 'same-version-tus-cancel', 'multipart-parts',
    'multipart-assembly-interruption', 'metadata-file-reconciliation',
    'changed-generation-refusal', 'multiple-writer-processes', 'neighbor-upload-preserved',
    'queue-pending-active-retry-quarantine', 'redis-lock-and-metadata-quarantine',
    'remote-provider-inflight-settlement', 'remote-version-and-part-reconciliation',
    'coherent-backup-and-restore', 'sdk-new-write-after-recovery', 'exact-owned-cleanup',
)

SDK_FIXTURE = r'''
// Original pinned SDK probes. These observations alone do not prove settlement.
import {createClient} from '@supabase/supabase-js';
import {createHash} from 'node:crypto';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const bytesOf = async result => {
  if (result.error || !result.data) throw new Error('Original download failed');
  return new Uint8Array(await result.data.arrayBuffer());
};
const checked = async action => {
  const result = await action();
  if (result.error) throw new Error('Original SDK operation failed');
  return result.data;
};
const resultFor = observations => ({observations:observations.length,
  observed_ids:observations.map(row => row.id), artifacts:observations,
  native_settlement_accepted:false});

export async function exerciseNativeSdk(client, bucket, observe, fetchSigned) {
  if (typeof fetchSigned !== 'function') throw new Error('Original signed-download transport required');
  const observations = [];
  const record = (id, fields={}) => {
    const row = {id, outcome:'protocol-observed', native_settlement_accepted:false, ...fields};
    observations.push(row);
    if (observe) observe(id, {...row});
  };
  const key = 'settlement-object.bin';
  const first = new Uint8Array(256 * 1024).fill(37);
  const second = new Uint8Array(first.length).fill(91);
  const storage = client.storage.from(bucket);
  await checked(() => storage.upload(key, first,
    {contentType:'application/octet-stream', cacheControl:'3600', upsert:false}));
  if (sha(await bytesOf(await storage.download(key))) !== sha(first))
    throw new Error('Native new upload bytes differ');
  record('sdk-new-upload', {data_sha256:sha(first)});
  record('sdk-exact-byte-download', {data_sha256:sha(first)});
  await checked(() => storage.upload(key, second,
    {contentType:'application/octet-stream', cacheControl:'3600', upsert:true}));
  if (sha(await bytesOf(await storage.download(key))) !== sha(second))
    throw new Error('Native overwrite bytes differ');
  record('sdk-overwrite', {data_sha256:sha(second)});
  const signed = await checked(() => storage.createSignedUploadUrl('signed.bin'));
  await checked(() => storage.uploadToSignedUrl('signed.bin', signed.token, first,
    {contentType:'application/octet-stream'}));
  if (sha(await bytesOf(await storage.download('signed.bin'))) !== sha(first))
    throw new Error('Native signed upload bytes differ');
  record('sdk-signed-upload', {data_sha256:sha(first), token_recorded:false});
  const download = await checked(() => storage.createSignedUrl('signed.bin', 60));
  const signedBytes = await fetchSigned(download.signedUrl);
  if (!signedBytes.ok || sha(new Uint8Array(await signedBytes.arrayBuffer())) !== sha(first))
    throw new Error('Original signed download bytes differ');
  record('sdk-signed-download', {data_sha256:sha(first), token_recorded:false});
  await checked(() => storage.remove([key, 'signed.bin']));
  for (const removedKey of [key, 'signed.bin']) {
    const removed = await storage.download(removedKey);
    if (!removed.error || ![404, '404'].includes(removed.error.status ?? removed.error.statusCode))
      throw new Error('Original delete absence was not corroborated');
  }
  record('sdk-delete');
  return resultFor(observations);
}

// Exact original protocol effects, still awaiting independent row/file corroboration.
export async function exerciseLargeStream(client, bucket, request) {
  const total = 8 * 1024 * 1024;
  const chunk = new Uint8Array(64 * 1024).fill(113);
  const hash = createHash('sha256');
  let sent = 0;
  const body = new ReadableStream({pull(controller) {
    if (sent === total) return controller.close();
    controller.enqueue(chunk.slice());
    hash.update(chunk);
    sent += chunk.length;
  }});
  const key = 'settlement-stream.bin';
  const response = await request('/object/' + encodeURIComponent(bucket) + '/' + key,
    {method:'POST', body, duplex:'half', headers:{'content-type':'application/octet-stream',
      'content-length':String(total), 'x-upsert':'false'}});
  if (!response.ok || sent !== total) throw new Error('Original stream upload failed');
  await response.arrayBuffer();
  const expected = hash.digest('hex');
  const actual = await bytesOf(await client.storage.from(bucket).download(key));
  if (actual.length !== total || sha(actual) !== expected) throw new Error('Native stream bytes differ');
  return resultFor([{id:'large-stream-upload', outcome:'protocol-observed',
    data_sha256:expected, bytes:total, native_settlement_accepted:false}]);
}

export async function exerciseTus(client, bucket, request, mode, tusPath='/upload/resumable') {
  if (!['resume', 'cancel'].includes(mode) || !/^\/[a-zA-Z0-9/_-]+$/.test(tusPath))
    throw new Error('Invalid original TUS probe');
  const key = 'settlement-tus-' + mode + '.bin';
  const bytes = new Uint8Array(32 * 1024).fill(mode === 'resume' ? 43 : 67);
  const metadata = Object.entries({bucketName:bucket, objectName:key,
    contentType:'application/octet-stream', cacheControl:'3600'})
    .map(([name, value]) => name + ' ' + Buffer.from(value).toString('base64')).join(',');
  const headers = {'tus-resumable':'1.0.0', 'x-upsert':'false'};
  const created = await request(tusPath, {method:'POST', headers:{...headers,
    'upload-length':String(bytes.length), 'upload-metadata':metadata}});
  if (created.status !== 201) throw new Error('Original TUS creation failed');
  const location = created.headers.get('location');
  if (!location) throw new Error('Original TUS location missing');
  const identity = new URL(location, 'http://invalid.local').pathname.split('/').pop();
  if (!identity || !/^[a-zA-Z0-9_-]+$/.test(identity)) throw new Error('Invalid original TUS identity');
  const originalUploadId = Buffer.from(identity, 'base64url').toString('utf8');
  const expectedPrefix = bucket + '/' + key;
  const versionSuffix = originalUploadId.slice(expectedPrefix.length);
  if (!originalUploadId.startsWith(expectedPrefix) ||
      !/^(?:\/|-\$v-)[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(versionSuffix))
    throw new Error('Original TUS key/version differs');
  const locationSha = sha(Buffer.from(location));
  const patch = async (offset, payload) => {
    const reply = await request(location, {method:'PATCH', body:payload, headers:{...headers,
      'content-type':'application/offset+octet-stream', 'upload-offset':String(offset)}});
    if (reply.status !== 204 || reply.headers.get('upload-offset') !== String(offset + payload.length))
      throw new Error('Original TUS offset differs');
  };
  await patch(0, bytes.slice(0, 16 * 1024));
  const interrupted = await request(location, {method:'HEAD', headers});
  if (interrupted.status !== 200 || interrupted.headers.get('upload-offset') !== String(16 * 1024) ||
      interrupted.headers.get('upload-length') !== String(bytes.length))
    throw new Error('Original interrupted TUS identity/offset differs');
  if (mode === 'resume') {
    await patch(16 * 1024, bytes.slice(16 * 1024));
    if (sha(await bytesOf(await client.storage.from(bucket).download(key))) !== sha(bytes))
      throw new Error('Original resumed TUS bytes differ');
  } else {
    const removed = await request(location, {method:'DELETE', headers});
    if (removed.status !== 204) throw new Error('Original TUS cancellation failed');
    const missing = await request(location, {method:'HEAD', headers});
    if (![404, 410].includes(missing.status)) throw new Error('Original TUS cancellation absence differs');
    const object = await client.storage.from(bucket).download(key);
    if (!object.error || ![404, '404'].includes(object.error.status ?? object.error.statusCode))
      throw new Error('Cancelled incomplete TUS object became visible');
  }
  return resultFor([{id:'same-version-tus-' + mode, outcome:'protocol-observed',
    original_upload_id_sha256:sha(Buffer.from(originalUploadId)), location_sha256:locationSha,
    accepted_partial_bytes:16 * 1024, data_sha256:mode === 'resume' ? sha(bytes) : null,
    native_settlement_accepted:false}]);
}

export function nativeClient(endpoint, serviceKey, tenantHost) {
  if (typeof serviceKey !== 'string' || !serviceKey.length || serviceKey.length > 8192 || /[\r\n]/.test(serviceKey))
    throw new Error('Invalid private native credential');
  const root = new URL(endpoint);
  if (!['http:', 'https:'].includes(root.protocol) || root.username || root.password ||
      root.search || root.hash || root.pathname !== '/') throw new Error('Invalid native endpoint');
  if (/^e_[a-f0-9]{24}\.storage\.internal$/.exec(tenantHost)?.[0] !== tenantHost)
    throw new Error('Invalid tenant host');
  const transport = async (input, init={}) => {
      const url = new URL(typeof input === 'string' ? input : input.url ?? input.toString());
      if (url.origin !== root.origin || !url.pathname.startsWith('/storage/v1/'))
        throw new Error('Fixture only permits original native Storage requests');
      url.pathname = url.pathname.slice('/storage/v1'.length);
      const headers = new Headers(init.headers);
      headers.set('x-forwarded-host', tenantHost);
      headers.set('x-forwarded-port', root.port || (root.protocol === 'https:' ? '443' : '80'));
      const deadline = AbortSignal.timeout(45000);
      const signal = init.signal ? AbortSignal.any([init.signal, deadline]) : deadline;
      return fetch(url, {...init, headers, signal, redirect:'error'});
  };
  const request = async (path, init={}) => {
    const url = new URL(path, root.origin);
    if (url.hostname === tenantHost && url.protocol === root.protocol && url.port === root.port)
      url.host = root.host;
    if (url.origin !== root.origin || url.username || url.password || url.hash)
      throw new Error('Original request must remain within assigned native endpoint');
    if (url.pathname.startsWith('/storage/v1/')) url.pathname = url.pathname.slice('/storage/v1'.length);
    const headers = new Headers(init.headers);
    headers.set('x-forwarded-host', tenantHost);
    headers.set('x-forwarded-port', root.port || (root.protocol === 'https:' ? '443' : '80'));
    headers.set('authorization', 'Bearer ' + serviceKey);
    headers.set('apikey', serviceKey);
    const deadline = AbortSignal.timeout(45000);
    const signal = init.signal ? AbortSignal.any([init.signal, deadline]) : deadline;
    return fetch(url, {...init, headers, signal, redirect:'error'});
  };
  return {client:createClient(root.origin, serviceKey, {
    auth:{persistSession:false, autoRefreshToken:false, detectSessionInUrl:false},
    global:{fetch:transport}
  }), fetchSigned:transport, request};
}
'''


# A helper performs actual original protocol I/O only in a later assigned runtime.
# SDK/HTTP helpers report protocol effects; local helpers call the concrete
# observer/controller. Installed authority and full native case proof stay pending.
_PROTOCOL_HELPERS = {**{key: 'exerciseNativeSdk' for key in CASES[:6]},
                     'large-stream-upload': 'exerciseLargeStream',
                     'same-version-tus-resume': 'exerciseTus:resume',
                     'same-version-tus-cancel': 'exerciseTus:cancel',
                     **{key: 'run_local_case:' + key for key in (
                         'all-original-processes-ended', 'lost-stop-acknowledgement',
                         'changed-generation-refusal', 'metadata-file-reconciliation',
                         'multiple-writer-processes')}}
_PENDING_IMPLEMENTATIONS = {
    'disconnect-before-body-end': 'Bounded raw original HTTP disconnect and original partial-file/row observer',
    'disconnect-after-file-before-db': 'Original transaction holder and observed file-before-row barrier',
    'controller-interruption': 'Kill actual controller after durable intent and resume exact owned ledger',
    'controller-restart-fence': 'Installed persistent launch fence recovery across controller restart',
    'storage-recreation-fence': 'Installed daemon launch guard preventing replacement CID while fenced',
    'multipart-parts': 'Pinned original S3 signing transport, create/upload/list/complete and exact part/file/row audit',
    'multipart-assembly-interruption': 'Actual original multipart assembly barrier and exit/reconciliation observer',
    'neighbor-upload-preserved': 'Concurrent original neighbor upload through actual selected-source fencing',
    'queue-pending-active-retry-quarantine': 'Separately bounded real queue workers, DB jobs and retries quarantine',
    'redis-lock-and-metadata-quarantine': 'Separately bounded original Redis lock/metadata dependency and observer',
    'remote-provider-inflight-settlement': 'Separately bounded actual provider inflight operation settlement',
    'remote-version-and-part-reconciliation': 'Actual provider version/part listing, ownership and reconciliation',
    'coherent-backup-and-restore': 'Original snapshot holders, stopped writer inventory and actual consumer restore',
    'sdk-new-write-after-recovery': 'Installed restore/publication authority followed by fresh original SDK roundtrip',
    'exact-owned-cleanup': 'Actual exact ledger cleanup with neighbor and resource absence observations',
}

_CASE_PENDING_STAGES = {
    'lost-stop-acknowledgement': ['Kill actual owning controller between delivered original stop and durable ack'],
    'changed-generation-refusal': ['Change installed management generation after original enrollment'],
}

# These are concrete executable source stages, not full native case runners.
# Keep the original full-case implementation pending until its complete outcome
# has actual installed authority and independent original effect corroboration.
_CASE_STAGE_SOURCES = {
    'disconnect-before-body-end': ['OriginalHttpTransport.disconnect'],
    'lost-stop-acknowledgement': ['LocalNativeAuthority.run_stop_fault:after-stop-before-ack',
                                'LocalNativeAuthority.recover_stop'],
    'controller-interruption': ['LocalNativeAuthority.run_stop_fault:after-intent'],
    'multipart-parts': ['exercise_multipart'],
}


def case_matrix(available_prerequisites=(), disabled_features=()):
    """Report implementation and prerequisites separately, with every ID retained."""
    if (type(available_prerequisites) not in (tuple, list) or
            type(disabled_features) not in (tuple, list) or
            any(type(item) is not str for item in (*available_prerequisites, *disabled_features))):
        raise Refused('Case prerequisites and disabled features must be string lists')
    rows = []
    for key in CASES:
        feature = ('tus' if key.startswith('same-version-tus-') else
                   'multipart' if key.startswith('multipart-') else
                   'queue' if key.startswith('queue-') else
                   'redis' if key.startswith('redis-') else
                   'remote_s3' if key.startswith('remote-') else 'standard')
        required_features = (feature, 'signed') if key in ('sdk-signed-upload', 'sdk-signed-download') else (feature,)
        prerequisites = ['assigned-runtime-role', 'installed-original-provenance',
                         'current-operation-generation-epoch', 'durable-before-effect-owner-ledger',
                         'installed-launch-ingress-publication-fence', 'original-row-file-version-observer',
                         'operation-owned-private-bucket',
                         'bounded-original-request-deadline', 'sdk-package-2.116.0']
        prerequisites.extend(item + '-enabled' for item in required_features)
        if feature in ('queue', 'redis', 'remote_s3'):
            prerequisites.append('separately-bounded-' + feature + '-authority')
        if key == 'lost-stop-acknowledgement':
            prerequisites.append('actual-owned-stop-delivered-and-acknowledgement-lost')
        if key == 'changed-generation-refusal':
            prerequisites.append('actual-installed-generation-changed-after-enrollment')
        if key == 'multiple-writer-processes':
            prerequisites.append('separately-bounded-original-multiple-writer-launches')
        helper = _PROTOCOL_HELPERS.get(key)
        missing = [item for item in prerequisites if item not in available_prerequisites]
        for required_feature in required_features:
            if required_feature in disabled_features and required_feature + '-enabled' not in missing:
                missing.append(required_feature + '-enabled')
        rows.append({'id': key, 'implementation': 'runnable-helper' if helper else 'pending',
                     'helper': helper, 'pending_implementation': _PENDING_IMPLEMENTATIONS.get(key),
                     'pending_case_stages': list(_CASE_PENDING_STAGES.get(key, ())),
                     'implemented_source_stages': list(_CASE_STAGE_SOURCES.get(key, ())),
                     'helper_scope': ('local-controller-observation' if helper and helper.startswith('run_local_case:')
                                      else 'original-protocol-effects' if helper else None),
                     'prerequisites': prerequisites, 'missing_prerequisites': missing,
                     'feature_disabled': any(item in disabled_features for item in required_features),
                     'status': 'implementation-pending' if not helper else
                               'prerequisite-unavailable' if missing else 'runtime-unrun',
                     'native_accepted': False})
    return rows


def run_protocol_stage(case_id, spec, endpoint, tenant_host, credentials_path, bucket, key):
    """Concrete original I/O stages only, no callbacks or full-case admission."""
    from storage_native_authority import OriginalHttpTransport, exercise_multipart
    if case_id not in ('disconnect-before-body-end', 'multipart-parts'):
        raise Refused('Unknown original protocol source stage')
    transport = OriginalHttpTransport(spec, endpoint, tenant_host, credentials_path)
    result = (transport.disconnect(bucket, key) if case_id == 'disconnect-before-body-end' else
              exercise_multipart(transport, bucket, key))
    return {'id': case_id, 'stage': result, 'full_case_completed': False, 'native_settlement_accepted': False}


def run_controller_fault_stage(case_id, spec, ledger_root):
    """Actual original stop fault source, awaiting installed authority and run."""
    from storage_native_authority import LocalNativeAuthority
    boundaries = {'lost-stop-acknowledgement': 'after-stop-before-ack',
                  'controller-interruption': 'after-intent'}
    if case_id not in boundaries:
        raise Refused('Unknown original controller fault source stage')
    authority = LocalNativeAuthority(spec, ledger_root)
    result = authority.run_stop_fault(boundaries[case_id])
    if case_id == 'lost-stop-acknowledgement':
        authority.recover_stop()
    return {'id': case_id, 'stage': result, 'full_case_completed': False, 'native_settlement_accepted': False}


def run_local_case(case_id, spec, ledger_root):
    """Call the concrete original observer/controller, never an injected callback.

    Live authority itself refuses before external effects while installed guards
    are pending. A prior actually delivered ambiguous stop is needed for recovery;
    this helper cannot create lost acknowledgements or native admission.
    """
    from storage_native_authority import LocalNativeAuthority
    from storage_write_settlement import canonical
    permitted = ('all-original-processes-ended', 'lost-stop-acknowledgement',
                 'changed-generation-refusal', 'metadata-file-reconciliation',
                 'multiple-writer-processes')
    if case_id not in permitted:
        raise Refused('Unknown local original case')
    authority = LocalNativeAuthority(spec, ledger_root)
    if case_id == 'changed-generation-refusal':
        try:
            authority.observe()
        except Refused as error:
            # Missing guards or generic errors are not successful refusals.
            if str(error) != 'Local authority binding differs: generation':
                raise
            material = {'refusal': str(error), 'expected_generation': authority.spec['manifest']['generation']}
        else:
            raise Refused('Actual changed generation was not refused')
    elif case_id == 'metadata-file-reconciliation':
        ended = authority.observe()
        if not ended['writers'] or any(not writer['stopped'] for writer in ended['writers']):
            raise Refused('Original reconciliation requires every writer ended')
        material = authority.reconcile()
        ended_again = authority.observe()
        if any(not writer['stopped'] for writer in ended_again['writers']):
            raise Refused('Original writers restarted during reconciliation')
        if material['unresolved']:
            raise Refused('Original file/row/version effects remain unresolved')
    elif case_id == 'multiple-writer-processes':
        material = authority.observe()
        if len(material['writers']) < 2 or any(writer['stopped'] for writer in material['writers']):
            raise Refused('Actual multiple original writer processes were not enrolled live')
    else:
        if case_id == 'lost-stop-acknowledgement':
            authority.recover_stop()
        else:
            authority.stop()
        material = authority.observe()
        if not material['writers'] or any(not writer['stopped'] or writer['processes']
                                          for writer in material['writers']):
            raise Refused('Every original writer process must be observed ended')
    return {'id': case_id, 'outcome': 'original-controller-observed',
            'artifact_sha256': hashlib.sha256(canonical(material)).hexdigest(),
            'native_settlement_accepted': False}


def plan(operation='00000000-0000-0000-0000-000000000001'):
    text(operation, UUID)
    prefix = 'sbarbase-settlement-' + operation.replace('-', '')
    return {'version': 1, 'status': 'source-preparation-native-unrun', 'operation': operation,
            'reference': {'storage_source': SOURCE, 'storage_image': IMAGE,
                          'sdk_package': '@supabase/supabase-js', 'sdk_version': '2.116.0'},
            'resources': {'prefix': prefix, 'owner': operation,
                          'network': prefix + '-net', 'published_ports': [],
                          'containers': [prefix + suffix for suffix in ('-db', '-auth-bootstrap', '-storage-a', '-storage-b')],
                          'volumes': [prefix + suffix for suffix in ('-db-data', '-objects-a', '-objects-b')],
                          'local_phase': {'max_running': 2, 'memory_mib': 512, 'cpus': .5},
                          'multiwriter_phase': {'max_running': 3, 'memory_mib': 768, 'cpus': .75},
                          'pids_per_container': 128, 'log_mib_per_container': 10},
            'bounds': {'command_seconds': 45, 'readiness_seconds': 120,
                       'local_role_seconds': 900, 'cleanup_seconds': 180},
            'inputs': ['coordinator-owned allocation with immutable public source identity',
                       'exact original DB/Auth/Storage image references and installed source provenance',
                       'current installation/daemon/namespace and Catalog bindings/source inventories',
                       'effective native tenant configuration and enabled feature manifest',
                       'installed persistent launch/ingress/publication fence authority',
                       'private synthetic credential files, never command arguments or evidence',
                       'complete planned ownership ledger with exact pre-allocation absence checks',
                       'per-database live snapshot holder identity and exported snapshot tokens',
                       'the exact operation-owned private bucket and bounded original SDK request deadline',
                       'separately admitted queue/Redis/S3 provider phases and recovery policy'],
            'required_test_ids': list(CASES), 'case_implementations': case_matrix(), 'sdk_fixture_sha256': hashlib.sha256(SDK_FIXTURE.encode()).hexdigest(),
            'pre_allocation': ['Persist planned resources before creation',
                               'Inspect exact absence, source/image identity and bounded host headroom',
                               'Record protected preexisting neighbor CIDs and immutable ownership'],
            'cleanup': ['Always run from exact planned ledger after failure or interruption',
                        'Stop/remove only exact verified owned CIDs, then owned volumes/network',
                        'No filter-based mass removal, retained inspection/adoption or prune',
                        'Unknown absence/daemon error retains unresolved resource state',
                        'Reinspect every exact owned resource and protected neighbor identity'],
            'unproven': ['installed native authority', 'actual native cases', 'all consumer integration',
                         'shared all-tenant migration', 'enabled remote/backend/queue effects',
                         'native-dedicated route admission', 'production']}


def validate_native_observations(value):
    """Check mandatory inventory completeness, never authenticate native evidence."""
    closed(value, ('version', 'operation', 'source_sha256', 'observations', 'unresolved_resources'))
    integer(value['version'], 1)
    if value['version'] != 1:
        raise Refused('Unsupported observation version')
    text(value['operation'], UUID)
    text(value['source_sha256'], r'[a-f0-9]{64}')
    observations = value['observations']
    if type(observations) is not list:
        raise Refused('Observation list required')
    ids = []
    for observed in observations:
        closed(observed, ('id', 'artifact_sha256', 'expected', 'observed'))
        text(observed['artifact_sha256'], r'[a-f0-9]{64}')
        if observed['id'] not in CASES or observed['expected'] != 'met' or observed['observed'] != 'met':
            raise Refused('Failed or unknown native observation')
        ids.append(observed['id'])
    if len(ids) != len(set(ids)) or set(ids) != set(CASES):
        raise Refused('Required native cases are missing or duplicated')
    if value['unresolved_resources'] != []:
        raise Refused('Owned cleanup is unresolved')
    return {'complete_inventory': True, 'native_accepted': False,
            'reason': 'Actual immutable artifacts and independent installed verification still required'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan', action='store_true')
    group.add_argument('--sdk-fixture', action='store_true')
    group.add_argument('--execute', action='store_true')
    group.add_argument('--source-control-check', action='store_true')
    parser.add_argument('--operation', default='00000000-0000-0000-0000-000000000001')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.execute:
        raise Refused('Native role and installed original writer authority remain unassigned')
    if args.source_control_check:
        if args.evidence is None:
            raise Refused('An explicit source log destination is required')
        result = subprocess.run(['bun', 'test', 'tests/storage-settlement-contract.test.ts'],
                                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=45)
        raw = result.stdout + result.stderr
        if len(raw.encode()) > 4 * 1024 * 1024:
            raise Refused('Source test output bound exceeded')
        args.evidence.write_text(raw)
        passes = re.findall(r'(?m)^\s*(\d+) pass\s*$', raw)
        failures = re.findall(r'(?m)^\s*(\d+) fail\s*$', raw)
        omissions = re.findall(r'(?m)^\s*(\d+) (?:skip|todo)\s*$', raw)
        passed = int(passes[0]) if len(passes) == 1 else 0
        failed = int(failures[0]) if len(failures) == 1 else 1
        skipped = sum(map(int, omissions))
        if result.returncode or not passed:
            failed = max(failed, 1)
        print(json.dumps({'total': passed + failed + skipped, 'failed': failed, 'skipped': skipped,
                          'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'native_accepted': False}))
        return 1 if failed or skipped else 0
    if args.sdk_fixture:
        sys.stdout.write(SDK_FIXTURE)
    else:
        print(json.dumps(plan(args.operation), indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Refused as error:
        raise SystemExit(str(error)) from None
