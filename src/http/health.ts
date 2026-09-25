/** `GET /health` on the loopback console: 200 once the process serves and its control catalog
 * and key store read, which is part of what confirms an upgrade (lab/upgrade_health.py; the
 * gateway itself is probed separately, src/gateway/hold-bypass.ts). Unauthenticated, so it says
 * nothing beyond that and whether application traffic is held. */
export function healthHandler(catalog:{schemaVersion():number},held:()=>boolean,keys?:{check():void}) {
 return (request:Request):Response=>{
  const headers={'cache-control':'no-store'};
  if(request.method!=='GET'&&request.method!=='HEAD')return Response.json({message:'Method not allowed'},{status:405,headers});
  try{catalog.schemaVersion();keys?.check();}
  catch{return Response.json({status:'unavailable'},{status:503,headers});}
  return Response.json({status:'ok',held:held()},{headers});
 };
}
