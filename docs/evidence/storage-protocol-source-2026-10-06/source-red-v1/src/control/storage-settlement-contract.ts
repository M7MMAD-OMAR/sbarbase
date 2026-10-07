import {createHash} from 'node:crypto';

/** Source contract preparation only. A parsed receipt does not prove storage settlement. */
export type SourceSettlementBinding = Readonly<{
  environment: string;
  runtime: string;
  epoch: number;
  coverage: 'dedicated-resources' | 'complete-shared-resources' | 'disposable-fixture' | null;
  placement: 'legacy-shared' | 'native-dedicated';
  inventoryDigest: string;
  placementDigest: string;
}>;

export type SourceSettlementReceipt = Readonly<{
  version: 1;
  contract: 'storage-write-settlement-v1';
  evidence: 'fixture' | 'native';
  binding: SourceSettlementBinding;
  operation: string;
  purpose: 'transfer' | 'purge' | 'backup' | 'restore' | 'migration';
  actor: string;
  management_epoch: number;
  installation: string;
  daemon: string;
  namespace: string;
  generation: number;
  routing_revision: number;
  manifest_sha256: string;
  reconciliation_sha256: string;
  journal_sha256: string;
  sequence: number;
  fence_token: string;
}>;

export type SourceSettlementExpectedContext = Omit<SourceSettlementReceipt,
  'reconciliation_sha256' | 'journal_sha256' | 'sequence'>;

/** The caller owns these current observations. Structural agreement cannot authenticate them. */
export type SourceSettlementObservations = Readonly<{
  sourceInventory: readonly Readonly<Record<string, unknown>>[];
  currentJournalEntry: Readonly<{
    journal_sha256: string;
    reconciliation_sha256: string;
    sequence: number;
  }>;
  liveFenceToken: string;
  liveInventory: readonly Readonly<Record<string, unknown>>[];
}>;

export type SourceSettlementComparison = Readonly<{
  sourceChecksPassed: boolean;
  receipt?: SourceSettlementReceipt;
  mismatches: readonly string[];
  nativeAdmitted: false;
}>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const REF = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}$/;
const RUNTIME = /^e_[0-9a-f]{24}$/;
type Check = (value: unknown, path: string) => unknown;
type Schema = Readonly<Record<string, Check>>;

function fail(path: string): never {
  throw new TypeError(`Invalid source settlement field: ${path}`);
}

function literal(...values: readonly unknown[]): Check {
  return (value, path) => values.includes(value) ? value : fail(path);
}

function string(pattern: RegExp): Check {
  return (value, path) => typeof value === 'string' && pattern.exec(value)?.[0] === value ? value : fail(path);
}

function integer(minimum: number): Check {
  return (value, path) => typeof value === 'number' && Number.isSafeInteger(value)
    && value >= minimum && !Object.is(value, -0) ? value : fail(path);
}

/** Reject getters, hidden properties, symbols and class instances before reading any field. */
function descriptors(input: unknown, path: string): Record<string, PropertyDescriptor> {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) fail(path);
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) fail(path);
  if (Object.getOwnPropertySymbols(input).length) fail(path);
  const fields = Object.getOwnPropertyDescriptors(input);
  for (const [key, field] of Object.entries(fields)) {
    if (!field.enumerable || !Object.hasOwn(field, 'value')) fail(`${path}.${key}`);
  }
  return fields;
}

function object(input: unknown, schema: Schema, path: string): Readonly<Record<string, unknown>> {
  const fields = descriptors(input, path);
  const keys = Object.keys(schema);
  if (Object.keys(fields).length !== keys.length || Object.keys(fields).some(key => !Object.hasOwn(schema, key))) fail(path);
  const result: Record<string, unknown> = {};
  for (const key of keys) {
    if (!Object.hasOwn(fields, key)) fail(`${path}.${key}`);
    result[key] = schema[key]!(fields[key]!.value, `${path}.${key}`);
  }
  return Object.freeze(result);
}

const bindingSchema: Schema = {
  environment: string(UUID), runtime: string(RUNTIME), epoch: integer(0),
  coverage: literal('dedicated-resources', 'complete-shared-resources', 'disposable-fixture', null),
  placement: literal('legacy-shared', 'native-dedicated'),
  inventoryDigest: string(HASH), placementDigest: string(HASH),
};

const expectedSchema: Schema = {
  version: literal(1), contract: literal('storage-write-settlement-v1'), evidence: literal('fixture', 'native'),
  binding: (value, path) => object(value, bindingSchema, path),
  operation: string(UUID), purpose: literal('transfer', 'purge', 'backup', 'restore', 'migration'),
  actor: string(REF), management_epoch: integer(0), installation: string(UUID), daemon: string(HASH),
  namespace: string(REF), generation: integer(1), routing_revision: integer(0),
  manifest_sha256: string(HASH), fence_token: string(UUID),
};

const journalSchema: Schema = {
  journal_sha256: string(HASH), reconciliation_sha256: string(HASH), sequence: integer(1),
};
const receiptSchema: Schema = {...expectedSchema, ...journalSchema};

/** Validate a closed wire object, then return a detached, deeply frozen copy. */
export function validateSourceSettlementReceipt(input: unknown): SourceSettlementReceipt {
  return object(input, receiptSchema, 'receipt') as SourceSettlementReceipt;
}

function unicodeString(input: string, path: string): string {
  for (const character of input) {
    const code = character.charCodeAt(0);
    if (character.length === 1 && code >= 0xd800 && code <= 0xdfff) fail(path);
  }
  return input;
}

function jsonClone(input: unknown, path: string, parents: Set<object>, depth: number): unknown {
  if (depth > 100) fail(path);
  if (input === null || typeof input === 'boolean') return input;
  if (typeof input === 'string') return unicodeString(input, path);
  if (typeof input === 'number') return Number.isSafeInteger(input) && !Object.is(input, -0) ? input : fail(path);
  if (typeof input !== 'object' || parents.has(input)) fail(path);
  parents.add(input);
  try {
    if (Array.isArray(input)) {
      if (Object.getPrototypeOf(input) !== Array.prototype || Object.getOwnPropertySymbols(input).length) fail(path);
      const fields = Object.getOwnPropertyDescriptors(input);
      if (Object.keys(fields).length !== input.length + 1) fail(path);
      const result: unknown[] = [];
      for (let index = 0; index < input.length; index++) {
        const field = fields[String(index)];
        if (!field || !field.enumerable || !Object.hasOwn(field, 'value')) fail(`${path}[${index}]`);
        result.push(jsonClone(field.value, `${path}[${index}]`, parents, depth + 1));
      }
      return Object.freeze(result);
    }
    const fields = descriptors(input, path);
    const result: Record<string, unknown> = Object.create(null);
    for (const [key, field] of Object.entries(fields)) {
      unicodeString(key, path);
      result[key] = jsonClone(field.value, `${path}.${key}`, parents, depth + 1);
    }
    return Object.freeze(result);
  } finally {
    parents.delete(input);
  }
}

function inventory(input: unknown, path: string): readonly Readonly<Record<string, unknown>>[] {
  if (!Array.isArray(input) || input.length === 0 || input.length > 100) fail(path);
  const result = jsonClone(input, path, new Set(), 0) as readonly unknown[];
  for (const item of result) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) fail(path);
  }
  return result as readonly Readonly<Record<string, unknown>>[];
}

/** Hash exact resource and property order as UTF-8 JSON, matching the lifecycle owner contract. */
export function sourceSettlementInventoryDigest(input: unknown): string {
  return createHash('sha256').update(JSON.stringify(inventory(input, 'inventory')), 'utf8').digest('hex');
}

const observationsSchema: Schema = {
  sourceInventory: inventory,
  currentJournalEntry: (value, path) => object(value, journalSchema, path),
  liveFenceToken: string(UUID),
  liveInventory: inventory,
};

/** Source checks only. Current journal and live observations require an independent native owner. */
export function compareSourceSettlementReceipt(
  input: unknown,
  expected: SourceSettlementExpectedContext,
  observations: SourceSettlementObservations,
): SourceSettlementComparison {
  const mismatches: string[] = [];
  let receipt: SourceSettlementReceipt | undefined;
  try {
    receipt = validateSourceSettlementReceipt(input);
    const context = object(expected, expectedSchema, 'expected') as SourceSettlementExpectedContext;
    const current = object(observations, observationsSchema, 'observations') as SourceSettlementObservations;
    for (const key of Object.keys(expectedSchema) as (keyof SourceSettlementExpectedContext)[]) {
      if (key === 'binding') {
        for (const bindingKey of Object.keys(bindingSchema) as (keyof SourceSettlementBinding)[]) {
          if (receipt.binding[bindingKey] !== context.binding[bindingKey]) mismatches.push(`binding.${bindingKey}`);
        }
      } else if (receipt[key] !== context[key]) mismatches.push(key);
    }
    if (receipt.binding.inventoryDigest !== sourceSettlementInventoryDigest(current.sourceInventory)) mismatches.push('sourceInventory');
    if (receipt.binding.inventoryDigest !== sourceSettlementInventoryDigest(current.liveInventory)) mismatches.push('liveInventory');
    if (receipt.fence_token !== current.liveFenceToken) mismatches.push('liveFenceToken');
    for (const key of Object.keys(journalSchema) as (keyof SourceSettlementObservations['currentJournalEntry'])[]) {
      if (receipt[key] !== current.currentJournalEntry[key]) mismatches.push(`currentJournalEntry.${key}`);
    }
  } catch (error) {
    mismatches.push(error instanceof Error ? error.message : 'Invalid source settlement input');
  }
  return Object.freeze({sourceChecksPassed: mismatches.length === 0,
    ...(receipt ? {receipt} : {}), mismatches: Object.freeze(mismatches), nativeAdmitted: false});
}

/** No installed native storage authority exists here. No caller object or callback can confer one. */
export function requireNativeStorageSettlement(..._untrustedInputs: readonly unknown[]): never {
  throw new Error('Native storage settlement authority unavailable');
}
