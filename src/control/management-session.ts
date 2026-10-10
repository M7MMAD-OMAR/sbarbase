/** How long a password sign-in may keep a management session alive. One value for every enforcement point. */
export const DEFAULT_MANAGEMENT_SESSION_SECONDS=43200;
export const MIN_MANAGEMENT_SESSION_SECONDS=3600;
export const MAX_MANAGEMENT_SESSION_SECONDS=2592000;

/** SBARBASE_MANAGEMENT_SESSION_SECONDS, read on every call. A missing, malformed or out of range value is the default. */
export function managementSessionSeconds(value:string|undefined=process.env.SBARBASE_MANAGEMENT_SESSION_SECONDS):number {
 if(value===undefined||!/^[0-9]{1,8}$/.test(value))return DEFAULT_MANAGEMENT_SESSION_SECONDS;
 const seconds=Number(value);
 return seconds>=MIN_MANAGEMENT_SESSION_SECONDS&&seconds<=MAX_MANAGEMENT_SESSION_SECONDS?seconds:DEFAULT_MANAGEMENT_SESSION_SECONDS;
}
