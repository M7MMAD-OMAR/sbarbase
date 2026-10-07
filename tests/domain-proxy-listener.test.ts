import {expect,test} from 'bun:test';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {serveLocal} from '../src/http/local-server';

const configuration={version:1,profile:'public',public_url:'https://api.example.org',
 ipv4:['8.8.8.8'],ipv6:[],provider_access:true,tls:'managed',acme_email:'operator@example.org',
 certificate_ref:null,site_url:'https://app.example.org',redirect_urls:['https://app.example.org/return'],smtp_ref:'environment-mail'};

// Feed the actual renderer output into the actual HTTP listener.
test('rendered public and local proxy Hosts reach the private listener; forwarding cannot admit an untrusted Host',async()=>{
 let calls=0;
 const server=await serveLocal(request=>{
  calls++;
  return Response.json({hostname:new URL(request.url).hostname,authority:request.headers.get('x-forwarded-host')});
 });
 try {
  const profiles=[configuration,{...configuration,public_url:'https://api.example.org:443'},
   {...configuration,profile:'local',public_url:'https://localhost:8443',ipv4:['127.0.0.1'],tls:'local',
    acme_email:'',site_url:'http://localhost:3000',redirect_urls:[]},
   {...configuration,profile:'local',public_url:'https://[::1]:8443',ipv4:[],ipv6:['::1'],tls:'local',
    acme_email:'',site_url:'http://localhost:3000',redirect_urls:[]}];
  for(const config of profiles){
   const rendered=spawnSync('python3',['-B','-c',
    'import json,sys;from domain_transport import render_proxy;print(render_proxy(json.load(sys.stdin),int(sys.argv[1])),end="")',String(server.port)],
    {cwd:fileURLToPath(new URL('../lab',import.meta.url)),input:JSON.stringify(config),encoding:'utf8',timeout:3000,maxBuffer:65536});
   expect(rendered.error).toBeUndefined();expect(rendered.status).toBe(0);
   const host=rendered.stdout.match(/^\s*header_up Host (\S+)$/m)?.[1];
   const publicAuthority=rendered.stdout.match(/^\s*header_up X-Forwarded-Host (\S+)$/m)?.[1];
   expect(host).toBeDefined();expect(publicAuthority).toBe(config.public_url.slice('https://'.length));
   const response=await fetch(`http://127.0.0.1:${server.port}/health`,{headers:{Host:host!,
    'X-Forwarded-Host':publicAuthority!,'X-Forwarded-Proto':'https'}});
   expect(response.status).toBe(200);
   expect(await response.json()).toEqual({hostname:'127.0.0.1',authority:publicAuthority});
  }
  const before=calls;
  const refused=await fetch(`http://127.0.0.1:${server.port}/health`,{headers:{Host:'attacker.example.org',
   'X-Forwarded-Host':`127.0.0.1:${server.port}`,'X-Forwarded-Proto':'https',Forwarded:'host=127.0.0.1;proto=https'}});
  expect(refused.status).toBe(403);expect(calls).toBe(before);
 } finally {server.stop(true);}
},10000);
