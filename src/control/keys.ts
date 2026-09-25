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
  /** While an upgrade waits for its health checks, the supervisor's probe presents a per-start
   * token as its key (src/gateway/hold-bypass.ts). Asked only when no stored key matched, so the
   * store is read on every probe exactly as for an application. */
  confirmationProbe?:(environment:string,token:string)=>boolean;
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
    if(row)return row.kind;
    return this.confirmationProbe?.(environment,token)?'publishable':null;
  }
  /** Reads the store; throws when it cannot. For `GET /health`, which confirms an upgrade. */
  check() {
    this.db.query('SELECT count(*) AS n FROM api_keys').get();
  }
  revoke(environment:string,id:string):boolean {
    return this.db.query('UPDATE api_keys SET revoked_at=? WHERE id=? AND environment=? AND revoked_at IS NULL')
      .run(Date.now(),id,environment).changes===1;
  }
  /** Every active key of one runtime, for a deleted or moved environment. Returns how many. */
  revokeAll(environment:string):number {
    return this.db.query('UPDATE api_keys SET revoked_at=? WHERE environment=? AND revoked_at IS NULL')
      .run(Date.now(),environment).changes;
  }
  list(environment:string):KeyRecord[] {
    return this.db.query<KeyRecord,[string]>('SELECT id,environment,kind,created_at,revoked_at FROM api_keys WHERE environment=? ORDER BY created_at,id').all(environment);
  }
  close(){this.db.close();}
}
