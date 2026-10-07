import {AsyncLocalStorage} from 'node:async_hooks';

type Authorization={check:()=>boolean;refresh?:()=>Promise<void>};
const authorization=new AsyncLocalStorage<Authorization>();
/** Retain synchronous Catalog guards and the original request's native refresh together. */
export function withManagementAuthorization<T>(check:()=>boolean,work:()=>T,refresh?:()=>Promise<void>):T {
 return authorization.run({check,refresh},work);
}
/** Workers and private catalog calls have no HTTP scope and retain their existing checks. */
export function requireCurrentManagement():void {
 const scope=authorization.getStore();if(scope&&!scope.check())throw new Error('Forbidden');
}
/** Await original native authentication before effects following a body or control await.
 * Private workers without an HTTP scope retain their own concrete native authority. */
export async function refreshCurrentManagement():Promise<void> {
 const scope=authorization.getStore();
 if(scope?.refresh)await scope.refresh();
 requireCurrentManagement();
}
