/** The application gateway's fair share policy, in one place: the gate in managed.ts runs it and
 * the catalog checks a changed share against it. docs/engineering/FAIR-SHARE-ADMISSION.md */
export const GATEWAY={share:8,total:32,ceiling:24,headroom:8,recentMs:60_000} as const;
