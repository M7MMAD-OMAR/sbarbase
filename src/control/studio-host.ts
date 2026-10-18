/** Where an environment's Studio is reached in the browser (docs/guides/studio.md).
 *
 * Every environment has its own address, so its Studio session cookie is never sent to another
 * environment's Studio and two Studio tabs never mix. `SBARBASE_STUDIO_HOST` is the address
 * pattern, with `{id}` for the environment's 24 hex characters:
 *
 * - `{id}.studio.localhost`, the default: through an SSH tunnel, since every browser sends
 *   `*.localhost` to its own machine.
 * - a public pattern such as `studio-{id}.example.com`: through the TLS proxy, with a wildcard
 *   DNS record and a certificate that cover it. The cookie is then Secure and host-only.
 *
 * Read by the console (lab/upstream-app.ts, src/control/studio.ts) and the TLS proxy
 * (deploy/console-tls-proxy.ts), which passes on only addresses that match it. */

export const DEFAULT_STUDIO_HOST='{id}.studio.localhost';
const LABEL=/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

export type StudioHosts={
 template:string;
 /** True for a public pattern: HTTPS through the proxy, a Secure cookie. */
 public:boolean;
 host(runtime:string):string;
 runtime(hostname:string):string|null;
};

export function studioHosts(value:string|undefined=process.env.SBARBASE_STUDIO_HOST):StudioHosts {
 const template=(value??'').trim().toLowerCase()||DEFAULT_STUDIO_HOST;
 const sample=template.replace('{id}','0'.repeat(24));
 if(template.split('{id}').length!==2||sample.length>253||!sample.split('.').every(label=>LABEL.test(label))||sample.split('.').length<2)
  throw new Error('SBARBASE_STUDIO_HOST must be a host name holding {id} once, such as studio-{id}.example.com');
 const [before,after]=template.split('{id}') as [string,string];
 const escape=(text:string)=>text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const pattern=new RegExp(`^${escape(before)}([a-f0-9]{24})${escape(after)}$`);
 return {
  template,
  public:!sample.endsWith('.localhost'),
  host(runtime) {
   if(!/^e_[a-f0-9]{24}$/.test(runtime))throw new Error('Invalid runtime');
   return template.replace('{id}',runtime.slice(2));
  },
  runtime(hostname) {
   const match=hostname.toLowerCase().match(pattern);
   return match?'e_'+match[1]:null;
  },
 };
}
