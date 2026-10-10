/** UI routing only. The server independently validates native Auth and the management grant. */
export function sessionHasMfa(token:string):boolean {
 try{return JSON.parse(atob(token.split('.')[1]!.replace(/-/g,'+').replace(/_/g,'/'))).aal==='aal2';}catch{return false;}
}
/** The console opens only for a session that passed native MFA. Derived from the session itself, never remembered in memory,
 * so a reload or a returning tab keeps working while the server still accepts the session. */
export function consoleAccess(session:{access_token:string}|null|undefined):boolean {
 return !!session&&sessionHasMfa(session.access_token);
}
export function mfaError(error:{status?:number;code?:string}|null):string {
 if(error?.status===429)return 'Too many attempts. Wait ten minutes before trying again.';
 if(error?.status&&error.status>=500)return 'Authentication is temporarily unavailable. Try again.';
 if(error?.code==='mfa_retry')return 'Wait for a new authenticator code, then verify again.';
 if(error?.status===409)return 'Another account security operation is in progress. Try again in a moment.';
 return 'The code was not accepted. Use the current code from your authenticator.';
}
