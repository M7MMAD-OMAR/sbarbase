import {randomBytes} from 'node:crypto';
import {closeSync,constants,fsyncSync,lstatSync,openSync,readFileSync,renameSync,unlinkSync,writeSync} from 'node:fs';
import {join} from 'node:path';

/** Operator notifications by Telegram, connected from the console.
 *
 * The owner pastes the bot token from @BotFather; it is checked with Telegram (getMe) and kept
 * in `.secrets/upstream/telegram.json`, mode 0600, the file lab/notify.py already reads. It is
 * never returned: the console sees the bot's name and the token's last four characters. The
 * chat is then bound by a one-time code: the console opens `t.me/<bot>?start=<code>`, the owner
 * presses Start, and only the chat that sent that exact code is taken, so a stranger writing to
 * the bot is never linked. The channel is written to `.lab/upstream/notifications.json`, other
 * channels in that file kept as they are, and the worker's drain rereads the file when it is
 * replaced. docs/engineering/OPERATOR-NOTIFICATIONS.md, section "Connecting Telegram". */

export const TOKEN=/^\d{5,16}:[A-Za-z0-9_-]{30,64}$/;
const TOKEN_FILE='.secrets/upstream/telegram.json';
const CONFIG_FILE='.lab/upstream/notifications.json';
const LINK_FILE='.lab/upstream/telegram-link.json';
const LINK_LIFETIME_MS=30*60*1000;
const TIMEOUT_MS=10_000;
const CHAT=/^-?\d{1,20}$/;

export type TelegramStatus={state:'off'|'pending'|'linked';bot?:string;tokenHint?:string;chat?:string;link?:string;expiresAt?:number};
export class TelegramRefusal extends Error {constructor(message:string,readonly status:number){super(message);}}

type Options={checkout?:string;apiBase?:string;fetch?:typeof fetch;now?:()=>number};

/** A whole private file, written under a temporary name, synced and renamed into place. */
function writePrivate(path:string,value:unknown) {
  const temporary=`${path}.${randomBytes(6).toString('hex')}.tmp`;
  const descriptor=openSync(temporary,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600);
  try {
    try {writeSync(descriptor,JSON.stringify(value,null,1)+'\n');fsyncSync(descriptor);}
    finally {closeSync(descriptor);}
    renameSync(temporary,path);
  } catch(error) {
    // A full disk must not leave a copy of the token behind under the temporary name.
    try{unlinkSync(temporary);}catch{}
    throw error;
  }
}

/** A regular file of this account and nothing else, parsed; absent is undefined. */
function readPrivate(path:string):Record<string,unknown>|undefined {
  let info;
  try {info=lstatSync(path);}
  catch(error) {if((error as {code?:string}).code==='ENOENT')return undefined;throw error;}
  if(!info.isFile()||info.uid!==process.getuid?.())throw new TelegramRefusal('A notification file is not a private file of this account',409);
  const value=JSON.parse(readFileSync(path,'utf8'));
  if(!value||typeof value!=='object'||Array.isArray(value))throw new TelegramRefusal('A notification file is malformed',409);
  return value as Record<string,unknown>;
}

function remove(path:string) {try{unlinkSync(path);}catch(error){if((error as {code?:string}).code!=='ENOENT')throw error;}}

export class TelegramNotifications {
  private readonly checkout:string;
  private readonly apiBase:string;
  private readonly request:typeof fetch;
  private readonly now:()=>number;
  constructor(options:Options={}) {
    this.checkout=options.checkout??'.';
    // Only the operator's own process environment can point this elsewhere (a test server);
    // nothing a request carries ever chooses where the token is sent.
    this.apiBase=(options.apiBase??process.env.SBARBASE_TELEGRAM_API_BASE??'https://api.telegram.org').replace(/\/+$/,'');
    this.request=options.fetch??fetch;
    this.now=options.now??Date.now;
  }
  private path(name:string) {return join(this.checkout,name);}

  /** One Bot API call. The token appears only in the URL of this request, and no error text
   * from Telegram or the network is passed on. */
  private async call(token:string,method:string,payload?:unknown):Promise<any> {
    let response:Response;
    try {
      response=await this.request(`${this.apiBase}/bot${token}/${method}`,{method:'POST',redirect:'error',
        headers:{'content-type':'application/json'},body:JSON.stringify(payload??{}),signal:AbortSignal.timeout(TIMEOUT_MS)});
    } catch {throw new TelegramRefusal('Telegram could not be reached from this server. Try again.',502);}
    if(response.status===401||response.status===404)throw new TelegramRefusal('Telegram refused this token. Copy it again from @BotFather.',400);
    if(response.status===409)throw new TelegramRefusal('This bot is connected to a webhook. Remove it in @BotFather, or use another bot.',409);
    let body:any;
    try {body=await response.json();} catch {body=undefined;}
    if(!response.ok||!body||body.ok!==true)throw new TelegramRefusal('Telegram did not accept the request. Try again.',502);
    return body.result;
  }

  private token():string|undefined {
    const file=readPrivate(this.path(TOKEN_FILE));
    if(!file)return undefined;
    return file.schema===1&&typeof file.botToken==='string'&&TOKEN.test(file.botToken)?file.botToken:undefined;
  }
  private config():Record<string,unknown> {return readPrivate(this.path(CONFIG_FILE))??{schema:1} as Record<string,unknown>;}
  private telegramSection():Record<string,unknown>|undefined {
    const section=this.config().telegram;
    return section&&typeof section==='object'&&!Array.isArray(section)?section as Record<string,unknown>:undefined;
  }
  private writeConfig(telegram:Record<string,unknown>|undefined) {
    const config:Record<string,unknown>={...this.config(),schema:1};
    if(telegram)config.telegram=telegram;else delete config.telegram;
    const enabled=['email','webhook','telegram'].some(channel=>{
      const value=(config as Record<string,any>)[channel];return value&&typeof value==='object'&&value.enabled===true;});
    // With no channel left the file goes: the drain then simply waits, instead of refusing it.
    if(enabled)writePrivate(this.path(CONFIG_FILE),config);else remove(this.path(CONFIG_FILE));
  }
  private link():{code:string;bot:string;expiresAt:number}|undefined {
    const file=readPrivate(this.path(LINK_FILE));
    if(!file||file.schema!==1||typeof file.code!=='string'||typeof file.bot!=='string'||typeof file.expiresAt!=='number')return undefined;
    return file.expiresAt>this.now()?{code:file.code,bot:file.bot,expiresAt:file.expiresAt}:undefined;
  }

  /** `owner` false leaves out the one-time link: whoever opens it chooses the chat, so only an
   * owner, who may change the channel, ever sees it. */
  status(owner=true):TelegramStatus {
    const token=this.token();
    if(!token)return {state:'off'};
    const tokenHint=token.slice(-4),section=this.telegramSection(),pending=this.link();
    if(section?.enabled===true&&typeof section.chatId==='string')
      return {state:'linked',bot:typeof section.bot==='string'?section.bot:undefined,tokenHint,chat:'…'+section.chatId.slice(-4)};
    if(pending)return owner?{state:'pending',bot:pending.bot,tokenHint,link:`https://t.me/${pending.bot}?start=${pending.code}`,expiresAt:pending.expiresAt}
      :{state:'pending',bot:pending.bot,tokenHint};
    return {state:'off',tokenHint};
  }

  /** Check the token with Telegram, keep it, and open a link for the chat. A token replaced
   * here turns the old chat off until the new one is linked. */
  async connect(token:unknown):Promise<TelegramStatus> {
    if(typeof token!=='string'||!TOKEN.test(token.trim()))
      throw new TelegramRefusal('This is not a bot token. It looks like 123456789:AA… and comes from @BotFather.',400);
    token=token.trim();
    const me=await this.call(token as string,'getMe');
    if(!me||me.is_bot!==true||typeof me.username!=='string'||!/^[A-Za-z0-9_]{5,32}$/.test(me.username))
      throw new TelegramRefusal('Telegram did not confirm this token belongs to a bot.',400);
    // The old chat goes first, so a drain reading in between never pairs it with the new token.
    if(this.telegramSection())this.writeConfig(undefined);
    writePrivate(this.path(TOKEN_FILE),{schema:1,botToken:token});
    writePrivate(this.path(LINK_FILE),{schema:1,code:randomBytes(16).toString('hex'),bot:me.username,expiresAt:this.now()+LINK_LIFETIME_MS});
    return this.status();
  }

  /** Take the chat that sent this link's code, turn the channel on and say so in that chat. */
  async complete():Promise<TelegramStatus> {
    const token=this.token(),pending=this.link();
    if(!token||!pending)throw new TelegramRefusal('Paste the bot token first; a link lasts 30 minutes.',409);
    const updates=await this.call(token,'getUpdates',{timeout:0,allowed_updates:['message']});
    if(!Array.isArray(updates))throw new TelegramRefusal('Telegram did not accept the request. Try again.',502);
    const expected=new RegExp(`^/start(@${pending.bot})? ${pending.code}$`);
    const chats=new Set<string>();let last:number|undefined;
    for(const update of updates) {
      if(typeof update?.update_id==='number')last=Math.max(last??update.update_id,update.update_id);
      const message=update?.message;
      if(typeof message?.text==='string'&&expected.test(message.text)&&CHAT.test(String(message.chat?.id)))chats.add(String(message.chat.id));
    }
    // Updates read here are acknowledged, so the next attempt never reads them again.
    if(last!==undefined)await this.call(token,'getUpdates',{offset:last+1,timeout:0,allowed_updates:['message']}).catch(()=>undefined);
    if(chats.size>1) {
      // The link reached more than one chat; none is chosen, and the link is spent.
      remove(this.path(LINK_FILE));
      throw new TelegramRefusal('More than one chat pressed Start on this link. Paste the token again for a new link.',409);
    }
    const chat=[...chats][0];
    if(!chat)throw new TelegramRefusal('No Start from the link yet. Open the link, press Start in Telegram, then try again.',409);
    const telegram:Record<string,unknown>={enabled:true,chatId:chat,tokenFile:TOKEN_FILE,bot:pending.bot};
    if(this.apiBase!=='https://api.telegram.org')telegram.apiBase=this.apiBase;
    this.writeConfig(telegram);
    remove(this.path(LINK_FILE));
    await this.send(token,chat,'Sbarbase is connected. Operator notifications for this installation will arrive in this chat.').catch(()=>undefined);
    return this.status();
  }

  private send(token:string,chat:string,text:string) {
    return this.call(token,'sendMessage',{chat_id:chat,text,disable_web_page_preview:true});
  }

  async test():Promise<void> {
    const token=this.token(),section=this.telegramSection();
    if(!token||section?.enabled!==true||typeof section.chatId!=='string')throw new TelegramRefusal('Telegram is not connected.',409);
    await this.send(token,section.chatId,'Test from Sbarbase: notifications reach this chat.');
  }

  /** Turn the channel off and forget the token. Events already queued for it are not sent. */
  disconnect():TelegramStatus {
    if(this.telegramSection())this.writeConfig(undefined);
    remove(this.path(LINK_FILE));
    remove(this.path(TOKEN_FILE));
    return this.status();
  }
}
