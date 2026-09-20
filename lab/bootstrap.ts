import {readFileSync} from 'node:fs';
import {Catalog} from '../src/control/catalog';
import {bootstrapOperator} from '../src/control/bootstrap';
import {bootstrapAuth} from './bootstrap-auth';
import {bootstrapJournal} from './bootstrap-journal';

if(process.env.SBARBASE_BOOTSTRAP_LOCKED!=='1')throw new Error('Use /usr/bin/python3 lab/bootstrap.py');
const path='.secrets/upstream/bootstrap.json';
let catalog:Catalog|undefined;
try {
 const input=await Bun.stdin.text();if(Buffer.byteLength(input)>8192)throw new Error('Input too large');
 const values=JSON.parse(readFileSync('.secrets/upstream/runtime.json','utf8'));
 const management=JSON.parse(readFileSync('.lab/upstream/management.json','utf8'));
 catalog=new Catalog('.lab/upstream/control.sqlite');
 const result=await bootstrapOperator(catalog,bootstrapAuth(management.auth,values.management.jwt),bootstrapJournal(path),JSON.parse(input));
 console.log(JSON.stringify({state:'initialized',...result}));
}catch{
 // Do not print upstream errors, supplied passwords, tokens or configuration.
 console.error('Operator setup failed. Retained bootstrap intent can be retried with the original inputs.');process.exitCode=1;
}finally{catalog?.close();}
