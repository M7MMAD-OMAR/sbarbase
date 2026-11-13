/** Capabilities narrow the owner's current role. They never replace membership checks.
 * SQL writes and executable code can destroy data: grant them only to trusted agents.
 * Account security, token minting and environment/project purge are never agent operations. */
export const AGENT_PERMISSIONS=[
 'projects.read','projects.write','organization.read','organization.write',
 'auth.read','auth.write','auth.delete','database.read','database.configure','database.write',
 'functions.read','functions.write','functions.delete','storage.read',
 'keys.read','keys.write','keys.delete','logs.read','realtime.read','realtime.write',
] as const;
export type AgentPermission=typeof AGENT_PERMISSIONS[number];
export const READ_PERMISSIONS:AgentPermission[]=AGENT_PERMISSIONS.filter(value=>value.endsWith('.read'));
/** Deliberately excludes arbitrary SQL, code execution/deployment, deletion and revocation. */
export const SAFE_ADMIN_PERMISSIONS:AgentPermission[]=AGENT_PERMISSIONS.filter(value=>
 !['database.write','functions.write','functions.delete','keys.write','keys.delete','auth.delete'].includes(value));
export const DANGEROUS_PERMISSIONS:AgentPermission[]=['database.write','functions.write','functions.delete','keys.write','keys.delete','auth.delete'];
export function parseAgentPermissions(value:unknown,readOnly=false):AgentPermission[]{
 if(value===undefined)return [...READ_PERMISSIONS];
 if(!Array.isArray(value)||value.length>AGENT_PERMISSIONS.length||value.some(item=>typeof item!=='string'||!AGENT_PERMISSIONS.includes(item as AgentPermission))||new Set(value).size!==value.length)
  throw new Error('permissions must be a list of distinct known capabilities');
 if(readOnly&&value.some(item=>!item.endsWith('.read')))throw new Error('A read-only token cannot grant write capabilities');
 return [...value] as AgentPermission[];
}
export function hasAgentPermission(token:{read_only:boolean;permissions?:readonly string[]},permission:AgentPermission):boolean{
 return (!token.read_only||permission.endsWith('.read'))&&!!token.permissions?.includes(permission);
}
