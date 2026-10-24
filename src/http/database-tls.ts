import {existsSync,mkdirSync,readFileSync,renameSync,writeFileSync,rmSync,mkdtempSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import {X509Certificate,createPrivateKey} from 'node:crypto';
import {isIP} from 'node:net';

/** The private certificate authority and server certificate for the database listeners
 * (docs/engineering/EXTERNAL-DATABASE.md). Created on first use with the system `openssl`, kept in
 * `.secrets/upstream/database-tls`, and made again when the server certificate has less than 30
 * days left or the requested names change. Clients that verify (`verify-ca`, Cloudflare Hyperdrive
 * with an uploaded CA) trust `ca.pem`; it holds no secret and can be shown and downloaded. */

export type DatabaseTls={cert:string;key:string;ca:string;names:string[]};
const RENEW_BEFORE_MS=30*24*3600*1000;

export function tlsNames(env:Record<string,string|undefined>=process.env):string[] {
 const names=new Set(['localhost','127.0.0.1','::1']);
 for(const raw of (env.SBARBASE_DATABASE_TLS_NAMES??'').split(',')){
  const name=raw.trim().toLowerCase();
  if(!name)continue;
  const dns=/^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(name)&&!/^[0-9.]+$/.test(name);
  if(!dns&&isIP(name)===0)
   throw new Error(`SBARBASE_DATABASE_TLS_NAMES has an invalid name: ${name}`);
  names.add(name);
 }
 return [...names].sort();
}

function openssl(args:string[],cwd:string) {
 const result=Bun.spawnSync(['openssl',...args],{cwd,stdout:'pipe',stderr:'pipe'});
 if(result.exitCode!==0)throw new Error(`openssl failed: ${result.stderr.toString().trim().split('\n')[0]}`);
}

const ipLike=(name:string)=>isIP(name)!==0;
const subjectAltName=(names:string[])=>names.map(name=>`${ipLike(name)?'IP':'DNS'}:${name}`).join(',');

export function ensureDatabaseTls(directory='.secrets/upstream/database-tls',names=tlsNames(),now=Date.now()):DatabaseTls {
 mkdirSync(directory,{recursive:true,mode:0o700});
 chmodSync(directory,0o700);
 const file=(name:string)=>join(directory,name);
 const read=(fresh=false):DatabaseTls|undefined=>{
  try{
   const recorded=readFileSync(file('names.txt'),'utf8').trim().split('\n');
   const cert=readFileSync(file('server.pem'),'utf8'),key=readFileSync(file('server.key'),'utf8'),ca=readFileSync(file('ca.pem'),'utf8');
   const parsed=new X509Certificate(cert);
   if(recorded.join('\n')!==names.join('\n'))return undefined;
   if(!parsed.checkPrivateKey(createPrivateKey(key)))return undefined;
   if((!fresh&&Date.parse(parsed.validTo)-now<RENEW_BEFORE_MS)||!parsed.verify(new X509Certificate(ca).publicKey))return undefined;
   return {cert,key,ca,names};
  }catch{return undefined;}
 };
 const current=read();
 if(current)return current;
 const work=mkdtempSync(join(directory,'.work-'));
 try{
  // The CA is kept (its key stays private) so renewing the server certificate does not change what clients trust.
  const haveCa=existsSync(file('ca.pem'))&&existsSync(file('ca.key'))&&
   Date.parse(new X509Certificate(readFileSync(file('ca.pem'))).validTo)-now>RENEW_BEFORE_MS*3;
  if(!haveCa){
   openssl(['ecparam','-name','prime256v1','-genkey','-noout','-out','ca.key'],work);
   openssl(['req','-new','-x509','-key','ca.key','-sha256','-days','3650','-subj','/CN=Sbarbase database CA','-out','ca.pem',
    '-addext','basicConstraints=critical,CA:TRUE,pathlen:0','-addext','keyUsage=critical,keyCertSign,cRLSign'],work);
  }else{
   writeFileSync(join(work,'ca.key'),readFileSync(file('ca.key')),{mode:0o600});
   writeFileSync(join(work,'ca.pem'),readFileSync(file('ca.pem')));
  }
  openssl(['ecparam','-name','prime256v1','-genkey','-noout','-out','server.key'],work);
  openssl(['req','-new','-key','server.key','-subj','/CN=Sbarbase database','-out','server.csr'],work);
  writeFileSync(join(work,'ext.cnf'),`subjectAltName=${subjectAltName(names)}\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n`);
  openssl(['x509','-req','-in','server.csr','-CA','ca.pem','-CAkey','ca.key','-CAcreateserial','-sha256','-days','397','-extfile','ext.cnf','-out','server.pem'],work);
  for(const name of ['ca.key','server.key'])chmodSync(join(work,name),0o600);
  writeFileSync(join(work,'names.txt'),names.join('\n')+'\n');
  // Key first, certificate last: a reader that sees the new certificate finds its key.
  for(const name of ['ca.key','ca.pem','server.key','server.pem','names.txt'])renameSync(join(work,name),file(name));
 }finally{rmSync(work,{recursive:true,force:true});}
 const made=read(true);
 if(!made)throw new Error('The database TLS certificate could not be created');
 return made;
}

/** Only the public part, for the console and `GET .../database/external`. */
export function databaseCa(directory='.secrets/upstream/database-tls'):string|undefined {
 try{return readFileSync(join(directory,'ca.pem'),'utf8');}catch{return undefined;}
}

/** Checks the certificate now and then (every 6 hours) and hands a renewed one to `renewed`, so a
 * process that runs for more than a year keeps serving a valid certificate. Returns the stop. */
export function watchDatabaseTls(current:DatabaseTls,renewed:(tls:DatabaseTls)=>void|Promise<void>,directory='.secrets/upstream/database-tls',
 names=current.names,everyMs=6*3600*1000,log:(line:string)=>void=()=>{}):()=>void {
 let last=current.cert,busy=false;
 const timer=setInterval(async()=>{
  if(busy)return;
  busy=true;
  try{
   const next=ensureDatabaseTls(directory,names);
   if(next.cert!==last){
    await renewed(next);
    // Only after the listeners took it: a failed reload is tried again at the next check.
    last=next.cert;log('database TLS certificate renewed');
   }
  }catch(error){log(`database TLS certificate renewal failed: ${(error as Error).message}`);}
  finally{busy=false;}
 },everyMs);
 timer.unref();
 return ()=>clearInterval(timer);
}
