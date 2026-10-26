// Asks the console to install a release, for the live upgrade measurement (lab/live_upgrade_measure.py).
//
// Usage: bun live-upgrade-console.ts <operator.json> update VERSION
//
// As the operator (the management API, signed in through native Auth as lab/live-upgrade-setup.ts does):
// "check now", wait until the Updates view offers VERSION and says it can be installed now, then
// POST /updates/apply. It prints one JSON line {offered, applied, status, at} once the console took the
// request (202); `at` is the wall clock in seconds at the moment before the apply call. A refusal prints
// the view's install section and exits 1. Nothing here waits for the upgrade itself: the driver follows
// the upgrade state and the pulse.
import {readFileSync,statSync} from 'node:fs';

const root=process.env.SBARBASE_ROOT;
if(!root){console.error('SBARBASE_ROOT is not set');process.exit(2);}
const {liveManagementClient,liveManagementLogin}:any=await import(`${root}/lab/live-management-auth`);
const MANAGEMENT_KEY='sb_publishable_sbarbase_local_management';
const [operatorPath,command,version]=process.argv.slice(2);
if(!operatorPath||command!=='update'||!version){console.error('usage: bun live-upgrade-console.ts <operator.json> update VERSION');process.exit(2);}
if((statSync(operatorPath).mode&0o077)!==0){console.error('the operator file must be private (mode 600)');process.exit(2);}
const operator=JSON.parse(readFileSync(operatorPath,'utf8')) as {email:string;password:string};
const base=(JSON.parse(readFileSync('.lab/upstream/server.json','utf8')) as {url:string}).url.replace(/\/$/,'');

const management=liveManagementClient(base,MANAGEMENT_KEY);
const login=await liveManagementLogin(management,base,{email:operator.email,password:operator.password});
const token=login.data.session?.access_token;
if(!token){console.error('FAIL: the operator could not sign in: '+(login.error?.message??''));process.exit(1);}
const call=async(method:string,path:string,body?:unknown)=>{
 const response=await fetch(`${base}/management/v1${path}`,{method,headers:{authorization:`Bearer ${token}`,...(body!==undefined?{'content-type':'application/json'}:{})},body:body===undefined?undefined:JSON.stringify(body)});
 return {status:response.status,json:await response.json().catch(()=>null) as any};
};
const checked=await call('POST','/updates/check');
if(checked.status!==202){console.error(`FAIL: "check now" answered ${checked.status}`);process.exit(1);}
let view:any=null;
for(const deadline=Date.now()+5*60_000;Date.now()<deadline;){
 view=(await call('GET','/updates')).json?.data;
 if(view?.available?.version===version&&view?.install?.possible===true)break;
 await Bun.sleep(3000);
}
if(view?.available?.version!==version||view?.install?.possible!==true){
 console.error('FAIL: the release is not on offer or cannot be installed now: '+JSON.stringify({available:view?.available&&{version:view.available.version,class:view.available.class,signed:view.available.signed},install:view?.install}));
 process.exit(1);
}
const at=Date.now()/1000;
const applied=await call('POST','/updates/apply',{version});
console.log(JSON.stringify({offered:{version:view.available.version,class:view.available.class,signed:view.available.signed},applied:applied.status===202,status:applied.status,at}));
process.exit(applied.status===202?0:1);
