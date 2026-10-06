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

export async function exerciseNativeSdk(client, bucket, observe, fetchSigned) {
  if (typeof fetchSigned !== 'function') throw new Error('Original signed-download transport required');
  const key = 'settlement-object.bin';
  const first = new Uint8Array(256 * 1024).fill(37);
  const second = new Uint8Array(first.length).fill(91);
  const sha = bytes => createHash('sha256').update(bytes).digest('hex');
  const call = async (id, action) => {
    const result = await action();
    if (result.error) throw new Error('Native SDK operation failed: ' + id);
    observe(id, {outcome:'observed', native_settlement_accepted:false});
    return result.data;
  };
  const storage = client.storage.from(bucket);
  await call('sdk-new-upload', () => storage.upload(key, first,
    {contentType:'application/octet-stream', cacheControl:'3600', upsert:false}));
  const before = await storage.download(key);
  if (before.error || sha(new Uint8Array(await before.data.arrayBuffer())) !== sha(first))
    throw new Error('Native new upload bytes differ');
  observe('sdk-exact-byte-download', {outcome:'observed', data_sha256:sha(first)});
  await call('sdk-overwrite', () => storage.upload(key, second,
    {contentType:'application/octet-stream', cacheControl:'3600', upsert:true}));
  const after = await storage.download(key);
  if (after.error || sha(new Uint8Array(await after.data.arrayBuffer())) !== sha(second))
    throw new Error('Native overwrite bytes differ');
  const signed = await call('sdk-signed-upload', () => storage.createSignedUploadUrl('signed.bin'));
  const uploaded = await storage.uploadToSignedUrl('signed.bin', signed.token, first,
    {contentType:'application/octet-stream'});
  if (uploaded.error) throw new Error('Original signed upload failed');
  const download = await storage.createSignedUrl('signed.bin', 60);
  if (download.error) throw new Error('Original signed URL failed');
  const signedBytes = await fetchSigned(download.data.signedUrl);
  if (!signedBytes.ok || sha(new Uint8Array(await signedBytes.arrayBuffer())) !== sha(first))
    throw new Error('Original signed download bytes differ');
  observe('sdk-signed-download', {outcome:'observed', data_sha256:sha(first), token_recorded:false});
  await call('sdk-delete', () => storage.remove([key, 'signed.bin']));
  const removed = await storage.download(key);
  if (!removed.error) throw new Error('Original delete did not remove object');
  return {observations:7, native_settlement_accepted:false};
}

export function nativeClient(endpoint, serviceKey, tenantHost) {
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
      return fetch(url, {...init, headers});
  };
  return {client:createClient(root.origin, serviceKey, {
    auth:{persistSession:false, autoRefreshToken:false, detectSessionInUrl:false},
    global:{fetch:transport}
  }), fetchSigned:transport};
}
'''


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
                       'separately admitted queue/Redis/S3 provider phases and recovery policy'],
            'required_test_ids': list(CASES), 'sdk_fixture_sha256': hashlib.sha256(SDK_FIXTURE.encode()).hexdigest(),
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
