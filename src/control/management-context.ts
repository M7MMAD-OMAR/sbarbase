import {AsyncLocalStorage} from 'node:async_hooks';

const authorization=new AsyncLocalStorage<()=>boolean>();
/** Keep the authenticated request's durable grant check across awaited body reads. */
export function withManagementAuthorization<T>(check:()=>boolean,work:()=>T):T {
 return authorization.run(check,work);
}
/** Workers and private catalog calls have no HTTP scope and retain their existing checks. */
export function requireCurrentManagement():void {
 const check=authorization.getStore();if(check&&!check())throw new Error('Forbidden');
}
