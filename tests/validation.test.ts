import {test,expect} from 'bun:test';
import {emailError,invitationEmailError,loginPasswordError,nameError,newPasswordError,signInError} from '../ui/validation';

test('email validation accepts aliases and padded addresses, but rejects malformed or oversized input',()=>{
 for(const email of ['person@example.com','  First.Last+console@example.co.uk  ','operator@localhost'])expect(emailError(email)).toBe('');
 for(const email of ['', '   ','person','person@@example.com','person@-example.com','person@example..com','person name@example.com','person\x00@example.com','\nperson@example.com','person@example.com\x7f'])expect(emailError(email)).not.toBe('');
 const boundary='a'.repeat(242)+'@example.com';
 expect(boundary.length).toBe(254);
 expect(emailError(boundary)).toBe('');
 expect(emailError('a'+boundary)).not.toBe('');
});

test('invitation email requires the management API domain suffix and retains supported address text',()=>{
 for(const email of [' Person+console@EXAMPLE.COM ','اسم@example.com'])expect(invitationEmailError(email)).toBe('');
 for(const email of ['','operator@localhost','person@example.c','person name@example.com','person@example.com\x00'])expect(invitationEmailError(email)).not.toBe('');
 const boundary='a'.repeat(242)+'@example.com';
 expect(invitationEmailError(boundary)).toBe('');
 expect(invitationEmailError('a'+boundary)).not.toBe('');
 expect(emailError('operator@localhost')).toBe('');
});

test('names retain international text and punctuation with only surrounding whitespace removed on submission',()=>{
 for(const name of [' فريق العمليات ',' 日本語のプロジェクト ','Project: research & development','Production / Europe'])expect(nameError(name)).toBe('');
 expect(nameError(' '+ 'x'.repeat(100)+' ')).toBe('');
 expect(nameError('x'.repeat(101))).not.toBe('');
 for(const name of ['', '   ', 'prod\nname','\tproduction','production\x00'])expect(nameError(name)).not.toBe('');
});

test('login does not apply the new-account password policy or trim existing credentials',()=>{
 expect(loginPasswordError('')).not.toBe('');
 for(const password of ['short',' ',' padded secret ', 'x'.repeat(257)])expect(loginPasswordError(password)).toBe('');
});

test('new passwords match invitation creation boundaries without trimming their contents',()=>{
 for(const password of ['', 'x'.repeat(11),'x'.repeat(257)])expect(newPasswordError(password)).not.toBe('');
 for(const password of ['x'.repeat(12),'x'.repeat(256),' 1234567890 '])expect(newPasswordError(password)).toBe('');
});

test('sign-in failures offer safe recovery guidance without exposing account existence',()=>{
 expect(signInError({status:429})).toContain('Wait a moment');
 expect(signInError({status:503})).toContain('reach the server');
 expect(signInError({name:'AuthRetryableFetchError'})).toContain('reach the server');
 expect(signInError({code:'email_not_confirmed'})).toContain('Confirm your email');
 const generic=signInError({code:'invalid_credentials'});
 expect(signInError({status:400,code:'user_not_found'})).toBe(generic);
 expect(signInError({status:400,code:'unexpected_failure'})).toBe(generic);
});
