/** Secret-free connection selection. The authenticated route supplies admitted endpoints.
 * This pure module never authenticates a request or grants native database authority. */
export type ConnectionMode='direct'|'session'|'transaction';
export type DatabasePurpose='migration'|'persistent'|'serverless'|'session-state'|'prepared-transaction';
export type WorkflowEndpoint={mode:ConnectionMode;host:string;port:number;user:string;database:string;ready:boolean};
export type WorkflowBudget={usable:number;promised:number;operationsReserve:number;developer:number;poolBackends:number;poolClients:number};
export type DatabaseWorkflowView={purpose:DatabasePurpose;mode:ConnectionMode;available:boolean;url:string|null;
 preparedStatements:boolean;sessionState:boolean;message:string;budget:ReturnType<typeof budgetStatus>|null};
const RUNTIME=/^e_[a-f0-9]{24}$/;
const purposes:DatabasePurpose[]=['migration','persistent','serverless','session-state','prepared-transaction'];
function integer(value:number,min=0){if(!Number.isSafeInteger(value)||value<min)throw new Error('Invalid connection budget');}
export function budgetStatus(value:WorkflowBudget){
 for(const item of Object.values(value))integer(item);
 if(value.operationsReserve<10)throw new Error('Operator reserve must be preserved');
 if(value.developer<value.poolBackends||value.poolClients<value.poolBackends)throw new Error('Inconsistent pool budget');
 const headroom=value.usable-value.promised;
 return {usable:value.usable,promised:value.promised,operationsReserve:value.operationsReserve,
  headroom,available:Math.max(0,headroom-value.operationsReserve),fits:headroom>=value.operationsReserve,
  developer:value.developer,poolBackends:value.poolBackends,poolClients:value.poolClients};
}
function endpointUrl(runtime:string,endpoint:WorkflowEndpoint){
 integer(endpoint.port,1);
 if(endpoint.port>65535||endpoint.database!==runtime||
  endpoint.user!==(endpoint.mode==='direct'?runtime+'_developer':runtime+'_developer.'+runtime))
  throw new Error('Invalid environment connection');
 // Accept DNS names and IP literals, without URL delimiters or embedded credentials.
 const host=endpoint.host;
 if(!host||!(/^[a-zA-Z0-9.-]+$/.test(host)||/^\[[a-fA-F0-9:]+\]$/.test(host)))throw new Error('Invalid connection host');
 return `postgresql://${endpoint.user}:[YOUR-PASSWORD]@${host}:${endpoint.port}/${runtime}`;
}
/** Keep the client's loopback listener separate from the server destination. */
export function databaseTunnel(connection:Pick<WorkflowEndpoint,'host'|'port'|'user'|'database'>):{command:string;url:string}{
 integer(connection.port,1);
 if(connection.port>65535)throw new Error('Invalid environment connection');
 let host=connection.host;
 if(/^[a-fA-F0-9:]+$/.test(host)&&host.includes(':'))host=`[${host}]`;
 if(!(/^[a-zA-Z0-9.-]+$/.test(host)||/^\[[a-fA-F0-9:]+\]$/.test(host)))throw new Error('Invalid connection host');
 const local=host==='[::1]'?'[::1]':'127.0.0.1';
 return {command:`ssh -L ${local}:${connection.port}:${host}:${connection.port} you@your-server`,
  url:endpointUrl(connection.database,{...connection,host:local,mode:'direct',ready:true})};
}
export function databaseWorkflow(runtime:string,purpose:DatabasePurpose,endpoints:readonly WorkflowEndpoint[],budget?:WorkflowBudget):DatabaseWorkflowView{
 if(!RUNTIME.test(runtime)||!purposes.includes(purpose))throw new Error('Invalid database workflow');
 const modes=new Set<ConnectionMode>();
 for(const endpoint of endpoints){
  if(!['direct','session','transaction'].includes(endpoint.mode)||modes.has(endpoint.mode)||typeof endpoint.ready!=='boolean')
   throw new Error('Invalid connection endpoints');
  modes.add(endpoint.mode);endpointUrl(runtime,endpoint);
 }
 const mode:ConnectionMode=purpose==='serverless'?'transaction':purpose==='session-state'?'session':'direct';
 const endpoint=endpoints.find(item=>item.mode===mode&&item.ready);
 const status=budget?budgetStatus(budget):null;
 const available=!!endpoint;
 const message=!available?`The ${mode} connection is not enabled. Ask an environment owner to enable it.`:
  mode==='transaction'?'Use a small application pool. Disable prepared statements and query pipelining. Keep every operation inside its transaction.':
  purpose==='migration'?'Use one direct session. Review owners, grants, RLS and custom schemas before applying migrations. Capture a recovery point and reconcile interrupted migrations before retrying.':
  purpose==='prepared-transaction'?'Use direct access. Check max_prepared_transactions and inventory unresolved transactions. Reconcile each commit or rollback after interruption.':
  mode==='session'?'Keep one session for LISTEN, temporary tables, advisory locks and prepared statements. Each held session consumes database capacity.':
  'Set a finite application pool. Every application replica shares the environment developer connection limit.';
 return {purpose,mode,available,url:endpoint?endpointUrl(runtime,endpoint):null,preparedStatements:mode!=='transaction',
  sessionState:mode!=='transaction',message,budget:status};
}
export function connectionDiagnostic(code:string):string{
 switch(code){
  case '53300':return 'Connection capacity is exhausted. Reduce application pool sizes or replicas, close idle sessions and check reserved operator headroom.';
  case '28P01':return 'Authentication failed. Check the environment login and current password. After rotation, reconnect with the new password.';
  case '42501':return 'Permission denied. Review the actual object owner, schema grants and RLS policy. Do not use a service secret to bypass the policy.';
  case '42P05':case '26000':return 'Prepared statement mismatch. In transaction mode disable prepared statements; otherwise reconnect and check the driver cache.';
  case '55P03':case '57014':return 'The operation waited too long or was canceled. Check active migrations and locks before retrying.';
  default:return 'Check connection mode, address, tunnel, service readiness and the sanitized database error code.';
 }
}
