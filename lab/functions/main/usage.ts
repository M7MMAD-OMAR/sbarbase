// What the runtime's event worker learns about each function, kept in memory by the main service of ONE environment's container
// (docs/engineering/EDGE-FUNCTIONS.md, "Event worker"): one bucket a minute for a day per function, and the last lines it wrote. No imports
// and no Deno globals, so the control plane's tests load this same file.

export type UsageEvent = {
  timestamp?: string;
  event_type?: string;
  event?: { boot_time?: number; reason?: string; cpu_time_used?: number; msg?: string; level?: string; memory_used?: { total?: number; heap?: number; external?: number } };
  metadata?: { service_path?: string };
};
export type Bucket = { minute: number; boots: number; bootMs: number; workers: number; cpuSum: number; cpuMax: number; memSum: number; memMax: number; heapSum: number; externalSum: number; errors: number };
export type Line = { at: number; level: string; msg: string };
export type FunctionUsage = { since: number; buckets: Bucket[]; lines: Line[] };
type Slot = FunctionUsage & { seen: number };

const MINUTES = 1440, LINES = 200, MAX_FUNCTIONS = 100, MAX_MESSAGE = 1000;
const NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** The function's name from the worker's service path, `/home/deno/functions/<version>/<name>`; null for anything else. */
export function functionOf(path: unknown): string | null {
  if (typeof path !== 'string') return null;
  const match = path.match(/\/functions\/[^/]+\/([^/]+)\/?$/);
  const name = match?.[1];
  return name !== undefined && NAME.test(name) ? name : null;
}
const count = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0);

export class Usage {
  private byName = new Map<string, Slot>();
  private startedAt: number;
  constructor(private now: () => number = Date.now) { this.startedAt = now(); }

  /** The record of a function. When 100 are held, the one least recently heard from makes room: functions that were deleted do not keep a place. */
  private slot(name: string): Slot {
    let item = this.byName.get(name);
    if (!item) {
      if (this.byName.size >= MAX_FUNCTIONS) {
        let oldest: string | undefined;
        for (const [key, value] of this.byName) if (oldest === undefined || value.seen < this.byName.get(oldest)!.seen) oldest = key;
        if (oldest !== undefined) this.byName.delete(oldest);
      }
      item = { since: this.now(), buckets: [], lines: [], seen: 0 };
      this.byName.set(name, item);
    }
    item.seen = this.now();
    return item;
  }

  /** The bucket of a minute, created in its place in time order: a batch that is retried arrives late, and its events must land where they happened. */
  private bucket(item: FunctionUsage, at: number): Bucket {
    const minute = Math.floor(at / 60_000);
    let index = item.buckets.length;
    while (index > 0 && item.buckets[index - 1]!.minute > minute) index--;
    const before = item.buckets[index - 1];
    if (before && before.minute === minute) return before;
    const row: Bucket = { minute, boots: 0, bootMs: 0, workers: 0, cpuSum: 0, cpuMax: 0, memSum: 0, memMax: 0, heapSum: 0, externalSum: 0, errors: 0 };
    item.buckets.splice(index, 0, row);
    const oldest = Math.floor(this.now() / 60_000) - MINUTES;
    let drop = 0;
    while (drop < item.buckets.length && item.buckets[drop]!.minute <= oldest) drop++;
    if (drop) item.buckets.splice(0, drop);
    return row;
  }

  /** Takes a batch from the event worker. Anything that is not one of the four kinds, or has no function, is ignored. */
  add(events: UsageEvent[]) {
    for (const item of Array.isArray(events) ? events : []) {
      const name = functionOf(item?.metadata?.service_path);
      if (!name || typeof item.event_type !== 'string') continue;
      const stamp = Date.parse(item.timestamp ?? '');
      // An event is placed in the minute it came from, never in the future, and a time that makes no sense becomes now.
      const at = Number.isFinite(stamp) && stamp > this.now() - MINUTES * 60_000 ? Math.min(stamp, this.now()) : this.now();
      const slot = this.slot(name);
      const row = this.bucket(slot, at), data = item.event ?? {};
      if (item.event_type === 'Boot') { row.boots++; row.bootMs += count(data.boot_time); }
      else if (item.event_type === 'Shutdown') {
        const memory = data.memory_used ?? {};
        row.workers++;
        row.cpuSum += count(data.cpu_time_used); row.cpuMax = Math.max(row.cpuMax, count(data.cpu_time_used));
        row.memSum += count(memory.total); row.memMax = Math.max(row.memMax, count(memory.total));
        row.heapSum += count(memory.heap); row.externalSum += count(memory.external);
      } else if (item.event_type === 'Log' || item.event_type === 'UncaughtException') {
        const level = item.event_type === 'UncaughtException' ? 'Error' : String(data.level ?? 'Info').slice(0, 12);
        if (level === 'Error') row.errors++;
        slot.lines.push({ at, level, msg: String(data.msg ?? (item.event_type === 'UncaughtException' ? 'Uncaught exception' : '')).slice(0, MAX_MESSAGE) });
        if (slot.lines.length > LINES) slot.lines.splice(0, slot.lines.length - LINES);
      }
    }
  }

  /** One function's numbers, or null when nothing has been heard of it since this container started. */
  of(name: string): FunctionUsage | null {
    const item = this.byName.get(name);
    return item ? { since: item.since, buckets: item.buckets.map((row) => ({ ...row })), lines: item.lines.map((line) => ({ ...line })) } : null;
  }
  bucketCount(): number { let total = 0; for (const item of this.byName.values()) total += item.buckets.length; return total; }
}
