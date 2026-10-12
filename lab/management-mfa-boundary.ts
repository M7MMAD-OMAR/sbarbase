/** Interleave one actual successful native verification before its proxy grant. */
type VerificationTransport=(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>Promise<Response>;
export function nativeVerificationBoundary(transport:VerificationTransport, endpoint:URL,
 intervene:(response:Response)=>Promise<void>):typeof fetch {
 let consumed=false;
 return (async(input:Parameters<typeof fetch>[0],init?:Parameters<typeof fetch>[1])=>{
  const response=await transport(input,init);
  if(!consumed&&String(input)===endpoint.href&&init?.method==='POST'&&response.ok){
   consumed=true;
   await intervene(response.clone());
  }
  return response;
 }) as typeof fetch;
}
