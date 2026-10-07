/** Private subprocess pipe only. No credentials are accepted as command arguments. */
import {liveManagementClient,liveManagementLogin} from './live-management-auth';
try{
 const input=await Bun.stdin.json();
 if(!input||typeof input.base!=='string'||typeof input.email!=='string'||typeof input.password!=='string')throw new Error('Invalid private input');
 const login=await liveManagementLogin(liveManagementClient(input.base),input.base,{email:input.email,password:input.password});
 process.stdout.write(JSON.stringify({access_token:login.data.session.access_token})+'\n');
}catch{process.stderr.write('Native management probe authentication refused\n');process.exitCode=1;}
