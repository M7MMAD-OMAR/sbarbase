import {test,expect} from 'bun:test';
import {mkdtempSync,readFileSync,statSync,writeFileSync,symlinkSync,rmSync} from 'node:fs';
import {createPrivateKey} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ensureSamlKey} from '../src/control/saml-key';

test('a key is made once, kept, private, and a PKCS#1 RSA key the pinned Auth accepts',()=>{
 const dir=mkdtempSync(join(tmpdir(),'saml-')),runtime='e_'+'a'.repeat(24),path=join(dir,runtime+'-saml.json');
 expect(ensureSamlKey(dir,runtime)).toBe(true);
 expect(statSync(path).mode&0o777).toBe(0o600);
 const file=JSON.parse(readFileSync(path,'utf8'));
 expect(file.version).toBe(1);
 const key=createPrivateKey({key:Buffer.from(file.private_key,'base64'),format:'der',type:'pkcs1'});
 expect(key.asymmetricKeyType).toBe('rsa');expect(key.asymmetricKeyDetails!.modulusLength).toBe(2048);
 // Turning SAML off and on again keeps the same key.
 expect(ensureSamlKey(dir,runtime)).toBe(false);
 expect(JSON.parse(readFileSync(path,'utf8')).private_key).toBe(file.private_key);
 // Each environment has its own.
 expect(ensureSamlKey(dir,'e_'+'b'.repeat(24))).toBe(true);
 expect(JSON.parse(readFileSync(join(dir,'e_'+'b'.repeat(24)+'-saml.json'),'utf8')).private_key).not.toBe(file.private_key);
 expect(()=>ensureSamlKey(dir,'../x')).toThrow();
 rmSync(path);symlinkSync('/etc/hostname',path);
 expect(()=>ensureSamlKey(dir,runtime)).toThrow();
 // A file that is open to others, or holds no key, is refused rather than accepted and failed at the apply.
 rmSync(path);writeFileSync(path,JSON.stringify({version:1,private_key:'AAAA'}),{mode:0o600});
 expect(()=>ensureSamlKey(dir,runtime)).toThrow();
 rmSync(path);writeFileSync(path,JSON.stringify(file),{mode:0o644});
 expect(()=>ensureSamlKey(dir,runtime)).toThrow();
});
