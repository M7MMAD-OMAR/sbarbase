import {Database} from 'bun:sqlite';
import {createHash, randomBytes, randomUUID} from 'node:crypto';
import {chmodSync} from 'node:fs';

export type KeyKind = 'publishable' | 'secret';
/** `environment` holds the opaque runtime id (`e_<24 hex>`), not the catalog's environment
 * UUID: the gateway resolves keys by runtime, and a project move keeps it. */
export type KeyRecord = {id:string;environment:string;kind:KeyKind;created_at:number;revoked_at:number|null};
const digest=(token:string)=>createHash('sha256').update(token).digest('hex');

/** Local key metadata, never signing secrets or raw API keys. Parent must be private.
 * The management key handler enforces scope; direct callers must authorize first.
 */
export class KeyStore {
  private db:Database;
  constructor(path:string) {
    this.db=new Database(path,{create:true,strict:true});
    if(path!==':memory:') chmodSync(path,0o600);
    this.db.exec(`PRAGMA journal_mode=DELETE; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS api_keys(
        id TEXT PRIMARY KEY, environment TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('publishable','secret')),
        digest TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL, revoked_at INTEGER
      );
      CREATE INDEX IF NOT EXISTS api_keys_environment ON api_keys(environment);`);
  }
  issue(environment:string,kind:KeyKind='publishable') {
    if(!/^[a-z][a-z0-9_]{1,30}$/.test(environment)) throw new Error('Invalid environment');
    if(kind!=='publishable' && kind!=='secret') throw new Error('Invalid key kind');
    const token=`sb_${kind}_${randomBytes(32).toString('base64url')}`;
    const id=randomUUID();
    this.db.query('INSERT INTO api_keys VALUES (?,?,?,?,?,NULL)').run(id,environment,kind,digest(token),Date.now());
    return {id,token};
  }
  resolve(environment:string,token:string):KeyKind|null {
    if(token.length>8192) return null;
    const row=this.db.query<{kind:KeyKind},[string,string]>(
      'SELECT kind FROM api_keys WHERE environment=? AND digest=? AND revoked_at IS NULL'
    ).get(environment,digest(token));
    return row?.kind ?? null;
  }
  revoke(environment:string,id:string):boolean {
    return this.db.query('UPDATE api_keys SET revoked_at=? WHERE id=? AND environment=? AND revoked_at IS NULL')
      .run(Date.now(),id,environment).changes===1;
  }
  list(environment:string):KeyRecord[] {
    return this.db.query<KeyRecord,[string]>('SELECT id,environment,kind,created_at,revoked_at FROM api_keys WHERE environment=? ORDER BY created_at,id').all(environment);
  }
  close(){this.db.close();}
}
