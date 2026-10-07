/** Validation messages for the console's forms. Empty means valid. */
export function emailError(value:string):string {
 const email=value.trim();
 if(!email)return 'Enter your email address.';
 if(email.length>254)return 'Use an email address with 254 characters or fewer.';
 const parts=email.split('@'),local=parts[0],domain=parts[1];
 if(/[\x00-\x1f\x7f]/.test(value)||parts.length!==2||!local||!domain||!/^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+$/i.test(local)||
  !domain.split('.').every(label=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label)))
  return 'Enter a valid email address, such as name@example.com.';
 return '';
}

/** Invitations use the management API's email policy. */
export function invitationEmailError(value:string):string {
 const email=value.trim().toLowerCase();
 if(!email)return 'Enter an email address.';
 if(email.length>254)return 'Use an email address with 254 characters or fewer.';
 if(/[\x00-\x1f\x7f]/.test(value)||!/^[^\s@"<>()\[\],;:\\]+@[a-z0-9.-]+\.[a-z]{2,}$/.test(email))
  return 'Enter a valid email address, such as name@example.com.';
 return '';
}

export function loginPasswordError(value:string):string {
 return value?'':'Enter your password.';
}

export function nameError(value:string):string {
 const name=value.trim();
 if(!name)return 'Enter a name.';
 if(/[\x00-\x1f]/.test(value))return 'Use a name without control characters.';
 if(name.length>100)return 'Use a name with 100 characters or fewer.';
 return '';
}

export function newPasswordError(value:string):string {
 if(!value)return 'Enter a password.';
 if(value.length<12)return 'Choose a password with at least 12 characters.';
 if(value.length>256)return 'Use a password with 256 characters or fewer.';
 return '';
}

export function signInError(error:{status?:number;name?:string;code?:string}):string {
 if(error.status===429)return 'Too many sign-in attempts. Wait a moment and try again.';
 if((error.status??0)>=500||error.name==='AuthRetryableFetchError')return 'Unable to reach the server. Try again.';
 if(error.code==='email_not_confirmed')return 'Confirm your email before signing in.';
 return 'Unable to sign in. Check your email and password.';
}
