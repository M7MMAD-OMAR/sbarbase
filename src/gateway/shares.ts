/** Environments one installation may hold. Mirrors ENVIRONMENT_LIMIT in lab/durable_runtime.py,
 * which stays the enforcing check; the catalog refuses before a job is queued, so the request
 * answers 409 instead of queueing work the worker must then refuse. */
export const ENVIRONMENT_LIMIT=8;

/** The application gateway's fair share policy, in one place: the gate in managed.ts runs it and
 * the catalog checks a changed share against it. The total keeps every environment's default
 * share at once. docs/engineering/FAIR-SHARE-ADMISSION.md */
export const GATEWAY={share:8,total:8*ENVIRONMENT_LIMIT,ceiling:24,headroom:8,recentMs:60_000} as const;

/** In-flight Function calls one environment may hold. A call can run up to 150 s and the functions
 * container has 0.5 CPU and 384 MiB while one worker may use 150 MiB, so about two busy workers fit;
 * 4 allows a few light calls at once and leaves half of the default share of 8 for REST and Auth.
 * Over it a call is refused like any service over its cap (429, retry-after 1). A route's
 * `serviceConcurrency.functions` replaces it. */
export const FUNCTION_CONCURRENCY=4;

/** How long one Function call may take, in ms: the gate's header and body deadline for the service
 * and the transport timeout. Documented as 150 s in docs/guides/edge-functions.md. */
export const FUNCTION_DEADLINE_MS=150_000;
