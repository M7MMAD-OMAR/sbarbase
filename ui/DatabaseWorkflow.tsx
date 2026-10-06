import {useId,useState} from 'react';
import {databaseWorkflow,connectionDiagnostic,type DatabasePurpose,type WorkflowEndpoint,type WorkflowBudget} from '../src/control/database-workflow';

/** The parent supplies only authorized, secret-free endpoints and a measured budget. */
export function DatabaseWorkflow({runtime,endpoints,budget}:{runtime:string;endpoints:readonly WorkflowEndpoint[];budget?:WorkflowBudget}){
 const id=useId();
 const [purpose,setPurpose]=useState<DatabasePurpose>('migration'),[code,setCode]=useState('');
 const view=databaseWorkflow(runtime,purpose,endpoints,budget);
 return <section className="details" aria-labelledby={id+'-title'}>
  <h3 id={id+'-title'}>Choose a database connection</h3>
  <label htmlFor={id+'-purpose'}>What are you connecting?</label>
  <select id={id+'-purpose'} value={purpose} onChange={event=>setPurpose(event.target.value as DatabasePurpose)}>
   <option value="migration">Migrations and database tools</option>
   <option value="persistent">A persistent application</option>
   <option value="serverless">A serverless application</option>
   <option value="session-state">A client that needs session state</option>
   <option value="prepared-transaction">Prepared transaction recovery</option>
  </select>
  <p role="status">{view.message}</p>
  {view.url&&<><label htmlFor={id+'-url'}>{view.mode} connection template</label>
   <input id={id+'-url'} readOnly value={view.url} aria-describedby={id+'-secret'}/>
   <p id={id+'-secret'} className="small muted">Use your saved password in the client's secret configuration. Keep it out of source control. A port number alone does not identify a connection mode. Use an SSH tunnel for the loopback direct endpoint.</p></>}
  {view.budget?<><h4>Connection budget</h4><dl>
   <dt>Database connections reserved</dt><dd>{view.budget.promised} of {view.budget.usable}</dd>
   <dt>Operator connections held back</dt><dd>{view.budget.operationsReserve}</dd>
   <dt>Unallocated connections after reserve</dt><dd>{view.budget.available}</dd>
   <dt>Developer connection limit</dt><dd>{view.budget.developer}</dd>
   <dt>Pooled database connections</dt><dd>{view.budget.poolBackends}</dd>
   <dt>Pooler client limit</dt><dd>{view.budget.poolClients}</dd>
  </dl><p className={view.budget.fits?'small muted':'notice'}>{view.budget.fits?'Leave the operator reserve available when sizing application pools.':'The proposed budget exceeds safe capacity. Reduce connections before enabling it.'}</p></>:
  <p className="notice">A current measured connection budget is unavailable. Ask the owner to check capacity before increasing application pools.</p>}
  <label htmlFor={id+'-code'}>Database error code</label>
  <input id={id+'-code'} value={code} maxLength={5} onChange={event=>setCode(event.target.value.toUpperCase())} aria-describedby={id+'-diagnostic'}/>
  <p id={id+'-diagnostic'} role="status">{connectionDiagnostic(code)}</p>
 </section>;
}
