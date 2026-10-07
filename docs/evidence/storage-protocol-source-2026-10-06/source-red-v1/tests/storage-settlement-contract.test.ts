import {describe, expect, test} from 'bun:test';
import {createHash} from 'node:crypto';
import {
  compareSourceSettlementReceipt, requireNativeStorageSettlement, sourceSettlementInventoryDigest,
  validateSourceSettlementReceipt,
  type SourceSettlementExpectedContext, type SourceSettlementObservations, type SourceSettlementReceipt,
} from '../src/control/storage-settlement-contract';

const uuid = '12345678-1234-1234-1234-123456789abc';
const otherUuid = '12345678-1234-1234-1234-123456789abd';
const hash = (character: string) => character.repeat(64);

function fixture() {
  const sourceInventory = [{kind: 'directory', id: '/objects/tenant', runtime: `e_${'a'.repeat(24)}`,
    installation: uuid, marker: 'objects-1', files: [{name: '日本語.txt', bytes: 42}]}];
  const receipt: SourceSettlementReceipt = {
    version: 1, contract: 'storage-write-settlement-v1', evidence: 'fixture',
    binding: {environment: uuid, runtime: `e_${'a'.repeat(24)}`, epoch: 2,
      coverage: 'dedicated-resources', placement: 'native-dedicated',
      inventoryDigest: sourceSettlementInventoryDigest(sourceInventory), placementDigest: hash('a')},
    operation: uuid, purpose: 'backup', actor: 'operator:owner', management_epoch: 3,
    installation: uuid, daemon: hash('b'), namespace: 'storage:tenant', generation: 4,
    routing_revision: 5, manifest_sha256: hash('c'), reconciliation_sha256: hash('d'),
    journal_sha256: hash('e'), sequence: 6, fence_token: uuid,
  };
  const {journal_sha256, reconciliation_sha256, sequence, ...expected} = receipt;
  const observations: SourceSettlementObservations = {sourceInventory,
    currentJournalEntry: {journal_sha256, reconciliation_sha256, sequence}, liveFenceToken: uuid,
    liveInventory: structuredClone(sourceInventory)};
  return {receipt, expected, observations};
}

describe('closed source settlement receipt', () => {
  test('valid fixture is detached, readonly at runtime, and grants no native admission', () => {
    const f = fixture();
    const receipt = validateSourceSettlementReceipt(f.receipt);
    expect(receipt).toEqual(f.receipt);
    expect(receipt).not.toBe(f.receipt);
    expect(receipt.binding).not.toBe(f.receipt.binding);
    expect(Object.isFrozen(receipt)).toBe(true);
    expect(Object.isFrozen(receipt.binding)).toBe(true);
    const result = compareSourceSettlementReceipt(f.receipt, f.expected, f.observations);
    expect(result.sourceChecksPassed).toBe(true);
    expect(result.nativeAdmitted).toBe(false);
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.mismatches)).toBe(true);
    expect(() => requireNativeStorageSettlement(result)).toThrow('authority unavailable');
  });

  test('later input mutation cannot alter the validated receipt', () => {
    const original = fixture().receipt;
    const mutable = {...original, binding: {...original.binding}};
    const validated = validateSourceSettlementReceipt(mutable);
    mutable.actor = 'operator:attacker';
    mutable.binding.environment = otherUuid;
    expect(validated.actor).toBe('operator:owner');
    expect(validated.binding.environment).toBe(uuid);
    expect(Reflect.set(validated, 'generation', 999)).toBe(false);
    expect(Reflect.set(validated.binding, 'epoch', 999)).toBe(false);
  });

  for (const [field, value] of [
    ['version', '1'], ['version', 2], ['contract', 'settled'], ['evidence', 'complete'],
    ['operation', uuid.toUpperCase()], ['operation', 'not-a-uuid'], ['operation', uuid + '\n'], ['purpose', 'delete'],
    ['actor', 'owner name'], ['actor', 'opérateur'], ['actor', ''], ['actor', 'operator:owner\n'], ['installation', 'installation:one'],
    ['daemon', hash('B')], ['daemon', `sha256:${hash('b')}`], ['daemon', hash('b') + '\n'], ['namespace', 'storage tenant'],
    ['management_epoch', '3'], ['management_epoch', true], ['management_epoch', -1],
    ['management_epoch', -0], ['generation', 0], ['generation', 4.1], ['routing_revision', NaN],
    ['sequence', Infinity], ['sequence', Number.MAX_SAFE_INTEGER + 1], ['sequence', 0],
    ['manifest_sha256', hash('C')], ['reconciliation_sha256', hash('d').slice(1)],
    ['journal_sha256', 'z'.repeat(64)], ['fence_token', uuid.toUpperCase()],
  ] as const) {
    test(`rejects invalid ${field} value ${JSON.stringify(value)}`, () => {
      expect(() => validateSourceSettlementReceipt({...fixture().receipt, [field]: value})).toThrow();
    });
  }

  for (const [field, value] of [
    ['environment', 'environment:one'], ['environment', uuid.toUpperCase()],
    ['runtime', 'e_' + 'A'.repeat(24)], ['runtime', 'e_abc'], ['runtime', `e_${'a'.repeat(24)}\n`], ['epoch', 1.5],
    ['coverage', 'dedicated'], ['coverage', undefined], ['placement', 'shared'],
    ['inventoryDigest', hash('A')], ['placementDigest', hash('a') + 'a'],
  ] as const) {
    test(`rejects invalid binding.${field}`, () => {
      const f = fixture();
      expect(() => validateSourceSettlementReceipt({...f.receipt, binding: {...f.receipt.binding, [field]: value}})).toThrow();
    });
  }

  test('rejects missing, unknown, inherited, symbol, hidden and accessor fields without invoking getters', () => {
    const f = fixture();
    for (const field of Object.keys(f.receipt)) {
      const incomplete: Record<string, unknown> = {...f.receipt};
      delete incomplete[field];
      expect(() => validateSourceSettlementReceipt(incomplete)).toThrow();
    }
    for (const field of Object.keys(f.receipt.binding)) {
      const binding: Record<string, unknown> = {...f.receipt.binding};
      delete binding[field];
      expect(() => validateSourceSettlementReceipt({...f.receipt, binding})).toThrow();
    }
    expect(() => validateSourceSettlementReceipt({...f.receipt, status: 'settled'})).toThrow();
    expect(() => validateSourceSettlementReceipt({...f.receipt, binding: {...f.receipt.binding, native: true}})).toThrow();
    expect(() => validateSourceSettlementReceipt(Object.assign(Object.create(f.receipt), {}))).toThrow();
    expect(() => validateSourceSettlementReceipt({...f.receipt, [Symbol('authority')]: true})).toThrow();
    const hidden = {...f.receipt};
    Object.defineProperty(hidden, 'secret', {value: true});
    expect(() => validateSourceSettlementReceipt(hidden)).toThrow();
    let invoked = false;
    const getter = {...f.receipt};
    Object.defineProperty(getter, 'actor', {enumerable: true, get() {invoked = true; return 'operator:owner';}});
    expect(() => validateSourceSettlementReceipt(getter)).toThrow();
    expect(invoked).toBe(false);
    for (const value of [null, undefined, [], 'receipt', 1]) expect(() => validateSourceSettlementReceipt(value)).toThrow();
  });

  test('accepts all documented purposes and coverage values as source data only', () => {
    const f = fixture();
    for (const purpose of ['transfer', 'purge', 'backup', 'restore', 'migration']) {
      expect(validateSourceSettlementReceipt({...f.receipt, purpose}).purpose).toBe(purpose);
    }
    for (const coverage of ['dedicated-resources', 'complete-shared-resources', 'disposable-fixture', null]) {
      const receipt = {...f.receipt, binding: {...f.receipt.binding, coverage}};
      expect(validateSourceSettlementReceipt(receipt).binding.coverage).toBe(coverage);
      expect(() => requireNativeStorageSettlement(receipt)).toThrow('authority unavailable');
    }
    expect(validateSourceSettlementReceipt({...f.receipt, binding: {...f.receipt.binding, placement: 'legacy-shared'}})
      .binding.placement).toBe('legacy-shared');
  });

  test('reference syntax matches the Python codec and has a maximum length of 255', () => {
    const f = fixture();
    for (const field of ['actor', 'namespace'] as const) {
      for (const value of ['operator@owner', 'operator+owner', 'a'.repeat(256)]) {
        expect(() => validateSourceSettlementReceipt({...f.receipt, [field]: value})).toThrow();
      }
      const value = 'a'.repeat(255);
      expect(validateSourceSettlementReceipt({...f.receipt, [field]: value})[field]).toBe(value);
      expect(validateSourceSettlementReceipt({...f.receipt, [field]: 'operator:owner/name_.-1'})[field])
        .toBe('operator:owner/name_.-1');
    }
  });
});

describe('expected identity and live source comparison', () => {
  for (const [field, value] of [
    ['evidence', 'native'], ['operation', otherUuid], ['purpose', 'purge'], ['actor', 'operator:other'],
    ['management_epoch', 4], ['installation', otherUuid], ['daemon', hash('f')], ['namespace', 'storage:other'],
    ['generation', 5], ['routing_revision', 6], ['manifest_sha256', hash('f')], ['fence_token', otherUuid],
  ] as const) {
    test(`rejects forged or stale ${field} scope`, () => {
      const f = fixture();
      const result = compareSourceSettlementReceipt({...f.receipt, [field]: value}, f.expected, f.observations);
      expect(result.sourceChecksPassed).toBe(false);
      expect(result.mismatches).toContain(field);
      expect(result.nativeAdmitted).toBe(false);
    });
  }

  for (const [field, value] of [
    ['environment', otherUuid], ['runtime', `e_${'b'.repeat(24)}`], ['epoch', 3],
    ['coverage', 'complete-shared-resources'], ['placement', 'legacy-shared'],
    ['inventoryDigest', hash('f')], ['placementDigest', hash('f')],
  ] as const) {
    test(`rejects forged binding.${field}`, () => {
      const f = fixture();
      const result = compareSourceSettlementReceipt({...f.receipt, binding: {...f.receipt.binding, [field]: value}},
        f.expected, f.observations);
      expect(result.sourceChecksPassed).toBe(false);
      expect(result.mismatches).toContain(`binding.${field}`);
    });
  }

  for (const [field, value] of [['journal_sha256', hash('f')], ['reconciliation_sha256', hash('f')], ['sequence', 7]] as const) {
    test(`rejects ${field} disagreement with the current journal entry`, () => {
      const f = fixture();
      const result = compareSourceSettlementReceipt({...f.receipt, [field]: value}, f.expected, f.observations);
      expect(result.sourceChecksPassed).toBe(false);
      expect(result.mismatches).toContain(`currentJournalEntry.${field}`);
    });
  }

  test('rejects a restarted live fence even when the receipt and expected context still agree', () => {
    const f = fixture();
    const result = compareSourceSettlementReceipt(f.receipt, f.expected, {...f.observations, liveFenceToken: otherUuid});
    expect(result.sourceChecksPassed).toBe(false);
    expect(result.mismatches).toContain('liveFenceToken');
  });

  test('checks source and live inventory independently of a mutually forged receipt and context digest', () => {
    const f = fixture();
    const receipt = {...f.receipt, binding: {...f.receipt.binding, inventoryDigest: hash('f')}};
    const expected = {...f.expected, binding: receipt.binding};
    const result = compareSourceSettlementReceipt(receipt, expected, f.observations);
    expect(result.mismatches).toEqual(['sourceInventory', 'liveInventory']);
    for (const field of ['sourceInventory', 'liveInventory'] as const) {
      const current = {...f.observations, [field]: [{kind: 'directory', id: '/different'}]};
      expect(compareSourceSettlementReceipt(f.receipt, f.expected, current).mismatches).toContain(field);
    }
  });

  test('refuses invalid expected context and observations rather than coercing caller input', () => {
    const f = fixture();
    const invalidExpected = {...f.expected, management_epoch: '3'} as unknown as SourceSettlementExpectedContext;
    expect(compareSourceSettlementReceipt(f.receipt, invalidExpected, f.observations).sourceChecksPassed).toBe(false);
    const contextWithProof = {...f.expected, journal_sha256: hash('e')} as SourceSettlementExpectedContext;
    expect(compareSourceSettlementReceipt(f.receipt, contextWithProof, f.observations).sourceChecksPassed).toBe(false);
    const invalidObservations = {...f.observations, currentJournalEntry: {...f.observations.currentJournalEntry, sequence: '6'}};
    expect(compareSourceSettlementReceipt(f.receipt, f.expected,
      invalidObservations as unknown as SourceSettlementObservations).sourceChecksPassed).toBe(false);
    expect(compareSourceSettlementReceipt({status: 'settled'}, f.expected, f.observations).sourceChecksPassed).toBe(false);
  });

  test('a source-matching native label still cannot install an authority or invoke a verifier callback', () => {
    const f = fixture();
    const receipt = {...f.receipt, evidence: 'native' as const};
    const expected = {...f.expected, evidence: 'native' as const};
    const result = compareSourceSettlementReceipt(receipt, expected, f.observations);
    expect(result.sourceChecksPassed).toBe(true);
    expect(result.nativeAdmitted).toBe(false);
    let invoked = false;
    const forgedAuthority = {kind: 'native', verify() {invoked = true; return true;}};
    expect(() => requireNativeStorageSettlement(receipt, expected, f.observations, forgedAuthority)).toThrow('authority unavailable');
    expect(() => requireNativeStorageSettlement(() => {invoked = true; return 'settled';})).toThrow('authority unavailable');
    expect(invoked).toBe(false);
  });
});

describe('ordered inventory digest', () => {
  test('uses the exact lifecycle JSON array order, property order and UTF-8 bytes', () => {
    const inventory = [{kind: 'directory', id: '/objects/日本語', nested: {a: 1, b: true}}, {kind: 'volume', id: 'objects'}];
    const expected = createHash('sha256').update(JSON.stringify(inventory), 'utf8').digest('hex');
    expect(sourceSettlementInventoryDigest(inventory)).toBe(expected);
    expect(sourceSettlementInventoryDigest([...inventory].reverse())).not.toBe(expected);
    expect(sourceSettlementInventoryDigest([{id: '/objects/日本語', kind: 'directory', nested: {a: 1, b: true}}, inventory[1]]))
      .not.toBe(expected);
    expect(sourceSettlementInventoryDigest(JSON.parse('[{"__proto__":{"owned":true},"id":"a"}]')))
      .toBe(createHash('sha256').update('[{"__proto__":{"owned":true},"id":"a"}]').digest('hex'));
  });

  test('rejects fractional, unsafe and negative-zero inventory numbers', () => {
    for (const value of [1.25, Number.MAX_SAFE_INTEGER + 1, Number.MIN_SAFE_INTEGER - 1, -0]) {
      expect(() => sourceSettlementInventoryDigest([{nested: {value}}])).toThrow();
    }
    const input = [{low: Number.MIN_SAFE_INTEGER, high: Number.MAX_SAFE_INTEGER, zero: 0}];
    expect(sourceSettlementInventoryDigest(input))
      .toBe(createHash('sha256').update(JSON.stringify(input), 'utf8').digest('hex'));
  });

  test('accepts 1 through 100 resource objects and retains valid empty nested arrays', () => {
    expect(() => sourceSettlementInventoryDigest([])).toThrow();
    expect(() => sourceSettlementInventoryDigest(Array.from({length: 101}, () => ({})))).toThrow();
    for (const input of [[{roles: []}], Array.from({length: 100}, (_, id) => ({id}))]) {
      expect(sourceSettlementInventoryDigest(input))
        .toBe(createHash('sha256').update(JSON.stringify(input), 'utf8').digest('hex'));
    }
  });

  test('rejects unpaired surrogates in keys and values while preserving valid astral UTF-8', () => {
    for (const value of ['\ud800', '\udfff', 'before\ud800after', 'before\udfffafter']) {
      expect(() => sourceSettlementInventoryDigest([{name: value}])).toThrow();
      expect(() => sourceSettlementInventoryDigest([{[value]: 'name'}])).toThrow();
    }
    const input = [{'日本語😀': 'objects/😀/𐀀', nested: ['𐀀', '日本語']}];
    expect(sourceSettlementInventoryDigest(input))
      .toBe(createHash('sha256').update(JSON.stringify(input), 'utf8').digest('hex'));
  });

  test('rejects unsafe or lossy JSON rather than trusting toJSON or getters', () => {
    const cycle: Record<string, unknown> = {};
    cycle.self = cycle;
    const sparse = new Array(2);
    const extra: unknown[] = [{}];
    Object.assign(extra, {authority: true});
    let invoked = false;
    const getter = Object.defineProperty({}, 'kind', {enumerable: true, get() {invoked = true; return 'volume';}});
    const toJSON = {toJSON() {invoked = true; return {};}};
    for (const inventory of [null, {}, ['resource'], [null], [[]], [cycle], sparse, extra,
      [{id: undefined}], [{id: BigInt(1)}], [{id: NaN}], [{id: Infinity}], [new Date()], [getter], [toJSON]]) {
      expect(() => sourceSettlementInventoryDigest(inventory)).toThrow();
    }
    expect(invoked).toBe(false);
  });
});
