import {describe,expect,test} from 'bun:test';
import {shareError,secretErrors,signInErrors} from '../ui/settings-validation';

describe('settings input validation',()=>{
 test('gateway share requires an integer within the supplied ceiling',()=>{
  for(const value of ['',' ','0','-1','1.5','25','Infinity','NaN','1e309'])expect(shareError(value,24)).not.toBe('');
  for(const value of ['1','24'])expect(shareError(value,24)).toBe('');
  expect(shareError('9007199254740992',Number.MAX_VALUE)).not.toBe('');
 });
 test('function secret names use the backend reserved prefixes and limits',()=>{
  for(const name of ['SUPABASE_CUSTOM','SB_CUSTOM','1KEY','LOWER_case','A'.repeat(129)])expect(secretErrors(name,'value')['secret-name']).toBeDefined();
  for(const name of ['STRIPE_SECRET_KEY','SYSTEM_KEY','_KEY','A'.repeat(128)])expect(secretErrors(name,'value')).toEqual({});
  expect(secretErrors('KEY','x'.repeat(65536))).toEqual({});
  expect(secretErrors('KEY','x'.repeat(65537))['secret-value']).toBeDefined();
  expect(secretErrors('KEY','')['secret-value']).toBeDefined();
 });
 test('site addresses require HTTP or HTTPS without embedded credentials',()=>{
  for(const address of ['','example.com','javascript:alert(1)','https://user:pass@example.com','https://example.com/a b'])expect(signInErrors(address,'',{})['site-url']).toBeDefined();
  expect(signInErrors(' https://example.com/app ','',{})).toEqual({});
 });
 test('redirects retain custom schemes and wildcards while enforcing format and count',()=>{
  expect(signInErrors('https://example.com','https://example.com/**\nmyapp://callback',{})).toEqual({});
  expect(signInErrors('https://example.com','https://example.com,https://other.com',{}).redirects).toBeDefined();
  expect(signInErrors('https://example.com',Array(51).fill('myapp://callback').join('\n'),{}).redirects).toBeDefined();
 });
 test('enabled providers require credentials while a saved secret may remain empty',()=>{
  const provider={enabled:true,client_id:'client',secret:'',secret_set:true,url:''};
  expect(signInErrors('https://example.com','',{github:provider})).toEqual({});
  expect(signInErrors('https://example.com','',{github:{...provider,secret_set:false}})['github-secret']).toBeDefined();
  expect(signInErrors('https://example.com','',{github:{...provider,client_id:''}})['github-client']).toBeDefined();
  expect(signInErrors('https://example.com','',{keycloak:provider})['keycloak-url']).toBeDefined();
  expect(signInErrors('https://example.com','',{azure:provider})).toEqual({});
  expect(signInErrors('https://example.com','',{keycloak:{...provider,url:'https://identity.example.com'}})).toEqual({});
 });
 test('provider length and whitespace rules also apply when disabled',()=>{
  const provider={enabled:false,client_id:'client',secret:'',url:''};
  expect(signInErrors('https://example.com','',{github:{...provider,client_id:'c'.repeat(513)}})['github-client']).toBeDefined();
  expect(signInErrors('https://example.com','',{github:{...provider,secret:'s'.repeat(4097)}})['github-secret']).toBeDefined();
  expect(signInErrors('https://example.com','',{github:{...provider,secret:'has space'}})['github-secret']).toBeDefined();
  expect(signInErrors('https://example.com','',{azure:{...provider,url:'not a URL'}})['azure-url']).toBeDefined();
 });
});
