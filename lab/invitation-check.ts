// Invitation check: the live path from an owner's invitation to a working member.
//
// Usage: bun lab/invitation-check.ts <operator.json> [--evidence PATH]
//
// Against the running installation (the URL in .lab/upstream/server.json) and its
// real management Auth realm, the operator from the 0600 bootstrap file invites a
// new address as a viewer. The invitee previews and redeems the token with a new
// password, which creates a confirmed management account, signs in, sees the
// organization and is refused a project creation. The token then refuses a second
// use, a cancelled invitation refuses redemption, and a viewer cannot invite. The
// invitee is removed from the organization at the end; the management account
// itself stays, because the API has no route that deletes one. Passwords are
// generated here and never printed or written.
import {checkList,installationUrl,managementCaller,operatorFrom,option,signIn} from './check-kit';

const args=process.argv.slice(2);
const operator=operatorFrom(args[0],'usage: bun lab/invitation-check.ts <operator.json> [--evidence PATH]');
const base=installationUrl();
const {record,finish}=checkList('invitation',option(args,'--evidence','docs/evidence/invitation-check.json'));

async function redeem(body:unknown,token?:string){
 const response=await fetch(`${base}/management/invitations/redeem`,{method:'POST',
  headers:{'content-type':'application/json',...(token?{authorization:`Bearer ${token}`}:{})},body:JSON.stringify(body)});
 return {status:response.status,json:await response.json().catch(()=>null) as any};
}

try {
 const owner=await signIn(base,operator.email,operator.password);
 if(!record('the operator signs in through the management Auth realm',!!owner.token,owner.error))await finish();
 const call=managementCaller(base,owner.token!);
 const organizations=await call('GET','/organizations');
 const organization=organizations.json?.data?.[0]?.id as string|undefined;
 if(!record('the operator sees an organization',organizations.status===200&&!!organization,`status ${organizations.status}`))await finish();

 const email=`invitee-${crypto.randomUUID().slice(0,8)}@example.com`;
 const invited=await call('POST',`/organizations/${organization}/invitations`,{email,role:'viewer'});
 const token=invited.json?.data?.token as string|undefined;
 if(!record('the owner invites a viewer and receives the token once',invited.status===201&&!!token,`status ${invited.status}`))await finish();
 const listed=await call('GET',`/organizations/${organization}/invitations`);
 record('the pending invitation is listed without its token',listed.status===200&&
  JSON.stringify(listed.json).includes(email)&&!JSON.stringify(listed.json).includes(token!),`status ${listed.status}`);

 const preview=await redeem({token,preview:true});
 record('the invitee previews the organization and role',preview.status===200&&preview.json?.data?.role==='viewer'&&preview.json?.data?.email===email,`status ${preview.status}`);
 const weak=await redeem({token,password:'short'});
 record('a short password is refused before any account exists',weak.status===400,`status ${weak.status}`);
 const password=crypto.randomUUID()+crypto.randomUUID();
 const joined=await redeem({token,password});
 record('redeeming creates the account and the membership',joined.status===201&&joined.json?.data?.role==='viewer',`status ${joined.status}`);
 const again=await redeem({token,password});
 record('the same token is refused a second time',again.status===400,`status ${again.status}`);

 const invitee=await signIn(base,email,password);
 if(!record('the invitee signs in with the new password',!!invitee.token,invitee.error))await finish();
 const as=managementCaller(base,invitee.token!);
 const seen=await as('GET','/organizations');
 record('the invitee sees the organization',seen.status===200&&(seen.json?.data??[]).some((row:any)=>row.id===organization),`status ${seen.status}`);
 const project=await as('POST',`/organizations/${organization}/projects`,{name:'Viewer may not create'});
 record('a viewer cannot create a project',project.status===403,`status ${project.status}`);
 const invite=await as('POST',`/organizations/${organization}/invitations`,{email:'someone@example.com',role:'viewer'});
 record('a viewer cannot invite',invite.status===403,`status ${invite.status}`);

 const second=await call('POST',`/organizations/${organization}/invitations`,{email:`cancelled-${crypto.randomUUID().slice(0,8)}@example.com`,role:'viewer'});
 const cancelled=await call('DELETE',`/organizations/${organization}/invitations/${second.json?.data?.id}`);
 record('the owner cancels a pending invitation',second.status===201&&cancelled.status===200,`status ${second.status}/${cancelled.status}`);
 const dead=await redeem({token:second.json?.data?.token,password});
 record('a cancelled invitation cannot be redeemed',dead.status===400,`status ${dead.status}`);

 const removed=await call('DELETE',`/organizations/${organization}/members/${invitee.userId}`);
 record('the owner removes the invitee',removed.status===200,`status ${removed.status}`);
 const after=await as('GET','/organizations');
 record('the removed invitee no longer sees the organization',after.status===200&&!(after.json?.data??[]).some((row:any)=>row.id===organization),`status ${after.status}`);
} catch(error) {
 record('the check completed without an exception',false,error instanceof Error?error.message:String(error));
}
await finish();
