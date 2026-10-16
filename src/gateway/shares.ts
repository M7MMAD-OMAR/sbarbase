/** Environments one installation may hold. Mirrors ENVIRONMENT_LIMIT in lab/durable_runtime.py,
 * which stays the enforcing check; the catalog refuses before a job is queued, so the request
 * answers 409 instead of queueing work the worker must then refuse. */
export const ENVIRONMENT_LIMIT=8;

/** The application gateway's fair share policy, in one place: the gate in managed.ts runs it and
 * the catalog checks a changed share against it. The total keeps every environment's default
 * share at once. docs/engineering/FAIR-SHARE-ADMISSION.md */
export const GATEWAY={share:8,total:8*ENVIRONMENT_LIMIT,ceiling:24,headroom:8,recentMs:60_000} as const;
