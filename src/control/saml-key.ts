import {existsSync,lstatSync,mkdirSync,readFileSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {generateKeyPairSync} from 'node:crypto';
import {join} from 'node:path';

/** The SAML signing key of one environment's Auth, made when SAML is first turned on (lab/saml_key.py reads it). It is kept when SAML is
 * turned off, because identity providers hold the matching certificate. Returns whether a key was made. The key is written with mode 0600
 * and is never returned, logged or sent anywhere. */
export function ensureSamlKey(directory:string,runtime:string):boolean {
 if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
 mkdirSync(directory,{recursive:true,mode:0o700});
 const path=join(directory,runtime+'-saml.json'),pending=path+'.pending';
 if(existsSync(path)){
  // A link, a file open to other users or one that holds no key is not what this console wrote: the save is refused with its own message,
  // not accepted and then failed at the apply.
  const status=lstatSync(path);
  if(!status.isFile()||(status.mode&0o077)!==0||status.size>16384)throw new Error('saml_key');
  try{const file=JSON.parse(readFileSync(path,'utf8')) as {version?:number;private_key?:unknown};
   if(file.version!==1||typeof file.private_key!=='string'||Buffer.from(file.private_key,'base64').length<600)throw new Error('saml_key');}
  catch{throw new Error('saml_key');}
  return false;
 }
 const {privateKey}=generateKeyPairSync('rsa',{modulusLength:2048,publicKeyEncoding:{type:'spki',format:'der'},privateKeyEncoding:{type:'pkcs1',format:'der'}});
 rmSync(pending,{force:true});
 writeFileSync(pending,JSON.stringify({version:1,private_key:Buffer.from(privateKey).toString('base64')}),{mode:0o600,flag:'wx'});
 renameSync(pending,path);
 return true;
}
