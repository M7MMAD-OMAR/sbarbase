// No imports and no Deno globals, so the control plane's tests load this same file.

/** Takes the oldest events off `queue`: at most `maxCount`, and at most `maxBytes` of JSON in all, but always at least one (an event too large
 * to share a batch goes alone). Messages with quotes or Arabic letters are several times longer written as JSON than as text, so the size is
 * measured on the JSON. */
export function takeBatch(queue: unknown[], maxCount: number, maxBytes: number): unknown[] {
  const batch: unknown[] = [];
  let size = 2;
  while (queue.length && batch.length < maxCount) {
    const length = JSON.stringify(queue[0]).length + 1;
    if (batch.length && size + length > maxBytes) break;
    size += length;
    batch.push(queue.shift());
  }
  return batch;
}
