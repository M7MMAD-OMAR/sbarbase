"""Scheduled jobs and outgoing HTTP for one environment: the compatibility schemas `cron` and `net`, the
`<e>_cron` login that runs jobs, and their grants (docs/engineering/SCHEDULED-JOBS.md).

Every function here returns SQL; `lab/durable_runtime.py` runs it as the superuser. The schemas, tables,
types and functions belong to the superuser, so tenant code cannot attach a trigger, a rule or a policy to
them or replace a function, and no function is SECURITY DEFINER: each runs as its caller with its search
path pinned to pg_catalog, so none of them ever acts with the superuser's rights. The scheduler
(`lab/scheduler.ts`) reads and writes these tables as `<e>_cron` and runs each job as `<e>_developer`.
"""
import re

RUNTIME = re.compile(r'e_[a-f0-9]{24}')
# The bookkeeping session, two runs at once, and the connection that ends a run at its deadline
# (src/scheduler/runner.ts end).
CONNECTIONS = 4
MAX_JOBS = 100
MAX_COMMAND = 64 * 1024
# A tenant session can hold a lock these statements need (a developer may LOCK cron.job, which it may
# update): each wait is bounded, and so is the whole transaction, so a turn that runs under the
# installation's operation lock ends within this and reports instead.
BOUNDS = "SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s'; SET LOCAL transaction_timeout = '30s';"
SAFE = 'SET LOCAL search_path TO pg_catalog, pg_temp;'
# Whitespace as pg_cron's sscanf and parse_cron_entry read it; src/scheduler/cron.ts uses the same class.
SPACE = r'[ \t\n\r\f\v]'


def check(e):
    if not isinstance(e, str) or not RUNTIME.fullmatch(e):
        raise ValueError('Invalid environment runtime identifier')
    return e


def password_ok(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


# The schedule check, the same rules as src/scheduler/cron.ts parseSchedule. Pure, IMMUTABLE, no table.
VALIDATOR = r"""
CREATE OR REPLACE FUNCTION cron._field_valid(field text, low integer, high integer, names text[])
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
  part text; m text[]; ends text[]; first integer; last integer; step integer; position integer;
BEGIN
  FOREACH part IN ARRAY pg_catalog.string_to_array(field, ',') LOOP
    m := pg_catalog.regexp_match(part, '^(\*|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:/([0-9]{1,9}))?$');
    IF m IS NULL THEN RETURN false; END IF;
    IF m[2] IS NOT NULL THEN
      step := m[2]::integer;
      IF step <= 0 THEN RETURN false; END IF;
    END IF;
    IF m[1] = '*' THEN
      first := low; last := high;
    ELSE
      ends := pg_catalog.string_to_array(m[1], '-');
      first := NULL; last := NULL;
      FOR position IN 1 .. pg_catalog.array_length(ends, 1) LOOP
        IF names IS NOT NULL AND pg_catalog.lower(ends[position]) = ANY (names) THEN
          last := pg_catalog.array_position(names, pg_catalog.lower(ends[position])) - 1 + low;
        ELSIF ends[position] ~ '^[0-9]{1,9}$' THEN
          last := ends[position]::integer;
        ELSE
          RETURN false;
        END IF;
        IF position = 1 THEN first := last; END IF;
      END LOOP;
      IF pg_catalog.array_length(ends, 1) = 1 AND m[2] IS NOT NULL THEN RETURN false; END IF;
    END IF;
    IF first < low OR first > high OR last < low OR last > high THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
END $f$;

CREATE OR REPLACE FUNCTION cron.schedule_valid(schedule text)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
  interval_text text; trimmed text; parts text[];
BEGIN
  IF schedule IS NULL OR pg_catalog.length(schedule) = 0 OR pg_catalog.length(schedule) >= 1000 THEN RETURN false; END IF;
  interval_text := pg_catalog.substring(pg_catalog.lower(schedule), '^SPACE*([0-9]+)SPACE*seconds?SPACE*$');
  IF interval_text IS NOT NULL THEN
    interval_text := pg_catalog.ltrim(interval_text, '0');
    RETURN pg_catalog.length(interval_text) BETWEEN 1 AND 2 AND interval_text::integer BETWEEN 1 AND 59;
  END IF;
  trimmed := pg_catalog.regexp_replace(schedule, '^SPACE+|SPACE+$', '', 'g');
  IF pg_catalog.left(trimmed, 1) = '@' THEN
    RETURN trimmed IN ('@yearly', '@annually', '@monthly', '@weekly', '@daily', '@midnight', '@hourly');
  END IF;
  parts := pg_catalog.regexp_split_to_array(trimmed, 'SPACE+');
  IF pg_catalog.array_length(parts, 1) IS DISTINCT FROM 5 THEN RETURN false; END IF;
  RETURN cron._field_valid(parts[1], 0, 59, NULL)
     AND cron._field_valid(parts[2], 0, 23, NULL)
     AND (parts[3] = '$' OR cron._field_valid(parts[3], 1, 31, NULL))
     AND cron._field_valid(parts[4], 1, 12, ARRAY['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'])
     AND cron._field_valid(parts[5], 0, 7, ARRAY['sun','mon','tue','wed','thu','fri','sat','sun']);
END $f$;
""".replace('SPACE', SPACE)


def shim_sql(e, login=None):
    """The two schemas, created or brought up to date, in one bounded transaction in the environment's
    database, then the grants (grants_sql, with `login`). A `cron` or `net` that something other than the
    superuser owns is refused, not adopted."""
    check(e)
    return f"""BEGIN;
{BOUNDS}
{SAFE}
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner
             WHERE n.nspname IN ('cron', 'net') AND NOT r.rolsuper) THEN
    RAISE EXCEPTION 'A cron or net schema belongs to another login';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             JOIN pg_catalog.pg_roles r ON r.oid = c.relowner WHERE n.nspname IN ('cron', 'net') AND NOT r.rolsuper) THEN
    RAISE EXCEPTION 'An object in the cron or net schema belongs to another login';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS cron;
CREATE SCHEMA IF NOT EXISTS net;
REVOKE ALL ON SCHEMA cron, net FROM PUBLIC;
{VALIDATOR}
CREATE SEQUENCE IF NOT EXISTS cron.jobid_seq;
CREATE SEQUENCE IF NOT EXISTS cron.runid_seq;
CREATE TABLE IF NOT EXISTS cron.job (
  jobid bigint NOT NULL DEFAULT pg_catalog.nextval('cron.jobid_seq'::pg_catalog.regclass),
  schedule text NOT NULL,
  command text NOT NULL,
  nodename text NOT NULL DEFAULT 'localhost'::text,
  nodeport integer NOT NULL DEFAULT pg_catalog.current_setting('port')::integer,
  database text NOT NULL DEFAULT pg_catalog.current_database(),
  username text NOT NULL DEFAULT CURRENT_USER,
  active boolean NOT NULL DEFAULT true,
  jobname text,
  CONSTRAINT job_pkey PRIMARY KEY (jobid),
  CONSTRAINT jobname_username_uniq UNIQUE (jobname, username),
  CONSTRAINT job_schedule_valid CHECK (cron.schedule_valid(schedule)),
  CONSTRAINT job_command_size CHECK (pg_catalog.octet_length(command) <= {MAX_COMMAND}),
  CONSTRAINT job_name_size CHECK (jobname IS NULL OR pg_catalog.octet_length(jobname) <= 1024),
  CONSTRAINT job_username_size CHECK (pg_catalog.octet_length(username) <= 128)
);
CREATE TABLE IF NOT EXISTS cron.job_run_details (
  jobid bigint,
  runid bigint NOT NULL DEFAULT pg_catalog.nextval('cron.runid_seq'::pg_catalog.regclass),
  job_pid integer,
  database text,
  username text,
  command text,
  status text,
  return_message text,
  start_time timestamp with time zone,
  end_time timestamp with time zone,
  CONSTRAINT job_run_details_pkey PRIMARY KEY (runid)
);

CREATE OR REPLACE FUNCTION cron._job_limit() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
  IF (SELECT pg_catalog.count(*) FROM cron.job) >= {MAX_JOBS} THEN
    RAISE EXCEPTION 'An environment can have at most {MAX_JOBS} scheduled jobs' USING ERRCODE = '54000';
  END IF;
  RETURN NEW;
END $f$;
-- What locks a table is done once, when it is missing: a tenant transaction left open on these tables then
-- holds up nothing a later turn or start writes.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_class s ON s.oid = d.objid
                 WHERE s.oid = 'cron.jobid_seq'::pg_catalog.regclass AND d.deptype = 'a') THEN
    ALTER SEQUENCE cron.jobid_seq OWNED BY cron.job.jobid;
    ALTER SEQUENCE cron.runid_seq OWNED BY cron.job_run_details.runid;
  END IF;
  IF pg_catalog.to_regclass('cron.job_run_details_end_time_idx') IS NULL THEN
    CREATE INDEX job_run_details_end_time_idx ON cron.job_run_details (end_time);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger WHERE tgname = 'job_limit' AND tgrelid = 'cron.job'::pg_catalog.regclass) THEN
    CREATE TRIGGER job_limit BEFORE INSERT ON cron.job FOR EACH ROW EXECUTE FUNCTION cron._job_limit();
  END IF;
END $$;

CREATE OR REPLACE FUNCTION cron._valid_or_raise(schedule text) RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
  IF NOT cron.schedule_valid(schedule) THEN
    RAISE EXCEPTION 'invalid schedule: %', schedule USING ERRCODE = '22023',
      HINT = 'Use cron format (e.g. 5 4 * * *), or interval format ''[1-59] seconds''';
  END IF;
END $f$;

CREATE OR REPLACE FUNCTION cron.schedule(schedule text, command text) RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE id bigint;
BEGIN
  IF $1 IS NULL THEN RAISE EXCEPTION 'schedule can not be NULL'; END IF;
  IF $2 IS NULL THEN RAISE EXCEPTION 'command can not be NULL'; END IF;
  PERFORM cron._valid_or_raise($1);
  INSERT INTO cron.job (schedule, command, database, username, active)
  VALUES ($1, $2, pg_catalog.current_database(), CURRENT_USER, true) RETURNING jobid INTO id;
  RETURN id;
END $f$;

CREATE OR REPLACE FUNCTION cron.schedule_in_database(job_name text, schedule text, command text, database text,
  username text DEFAULT NULL::text, active boolean DEFAULT true) RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE id bigint;
BEGIN
  IF $1 IS NULL THEN RAISE EXCEPTION 'job_name can not be NULL'; END IF;
  IF $2 IS NULL THEN RAISE EXCEPTION 'schedule can not be NULL'; END IF;
  IF $3 IS NULL THEN RAISE EXCEPTION 'command can not be NULL'; END IF;
  IF $4 IS NOT NULL AND $4 <> pg_catalog.current_database() THEN
    RAISE EXCEPTION 'Scheduled jobs run in their own database only (%)', pg_catalog.current_database() USING ERRCODE = '42501';
  END IF;
  IF $5 IS NOT NULL AND $5 <> CURRENT_USER THEN
    RAISE EXCEPTION 'must be superuser to create a job for another role' USING ERRCODE = '42501';
  END IF;
  PERFORM cron._valid_or_raise($2);
  INSERT INTO cron.job AS j (schedule, command, database, username, active, jobname)
  VALUES ($2, $3, pg_catalog.current_database(), CURRENT_USER, coalesce($6, true), $1)
  ON CONFLICT ON CONSTRAINT jobname_username_uniq
  DO UPDATE SET schedule = EXCLUDED.schedule, command = EXCLUDED.command, database = EXCLUDED.database
  RETURNING j.jobid INTO id;
  RETURN id;
END $f$;

CREATE OR REPLACE FUNCTION cron.schedule(job_name text, schedule text, command text) RETURNS bigint
LANGUAGE sql SET search_path = pg_catalog, pg_temp
AS $f$ SELECT cron.schedule_in_database($1, $2, $3, NULL::text, NULL::text, true) $f$;

CREATE OR REPLACE FUNCTION cron.unschedule(job_id bigint) RETURNS boolean
LANGUAGE plpgsql STRICT SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
  DELETE FROM cron.job j WHERE j.jobid = $1 AND j.username = CURRENT_USER;
  IF NOT FOUND THEN RAISE EXCEPTION 'could not find valid entry for job %', $1; END IF;
  RETURN true;
END $f$;

CREATE OR REPLACE FUNCTION cron.unschedule(job_name text) RETURNS boolean
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
  IF $1 IS NULL THEN RAISE EXCEPTION 'job_name can not be NULL'; END IF;
  DELETE FROM cron.job j WHERE j.jobname = $1 AND j.username = CURRENT_USER;
  IF NOT FOUND THEN RAISE EXCEPTION 'could not find valid entry for job ''%''', $1; END IF;
  RETURN true;
END $f$;

CREATE OR REPLACE FUNCTION cron.alter_job(job_id bigint, schedule text DEFAULT NULL::text, command text DEFAULT NULL::text,
  database text DEFAULT NULL::text, username text DEFAULT NULL::text, active boolean DEFAULT NULL::boolean) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
  IF $1 IS NULL THEN RAISE EXCEPTION 'job_id can not be NULL'; END IF;
  IF $2 IS NULL AND $3 IS NULL AND $4 IS NULL AND $5 IS NULL AND $6 IS NULL THEN
    RAISE EXCEPTION 'no updates specified' USING HINT = 'You must specify at least one job attribute to change when calling alter_job';
  END IF;
  IF $2 IS NOT NULL THEN PERFORM cron._valid_or_raise($2); END IF;
  IF $4 IS NOT NULL AND $4 <> pg_catalog.current_database() THEN
    RAISE EXCEPTION 'Scheduled jobs run in their own database only (%)', pg_catalog.current_database() USING ERRCODE = '42501';
  END IF;
  IF $5 IS NOT NULL AND $5 <> CURRENT_USER THEN
    RAISE EXCEPTION 'must be superuser to alter username' USING ERRCODE = '42501';
  END IF;
  UPDATE cron.job j SET schedule = coalesce($2, j.schedule), command = coalesce($3, j.command), active = coalesce($6, j.active)
  WHERE j.jobid = $1 AND j.username = CURRENT_USER;
  IF NOT FOUND THEN RAISE EXCEPTION 'Job % does not exist or you don''t own it', $1; END IF;
END $f$;

DO $$ BEGIN
  IF pg_catalog.to_regtype('net.http_method') IS NULL THEN
    CREATE DOMAIN net.http_method AS text CHECK (VALUE OPERATOR(pg_catalog.~~*) 'get' OR VALUE OPERATOR(pg_catalog.~~*) 'post'
                                                OR VALUE OPERATOR(pg_catalog.~~*) 'delete');
  END IF;
  IF pg_catalog.to_regtype('net.request_status') IS NULL THEN
    CREATE TYPE net.request_status AS ENUM ('PENDING', 'SUCCESS', 'ERROR');
  END IF;
  IF pg_catalog.to_regtype('net.http_response') IS NULL THEN
    CREATE TYPE net.http_response AS (status_code integer, headers jsonb, body text);
  END IF;
  IF pg_catalog.to_regtype('net.http_response_result') IS NULL THEN
    CREATE TYPE net.http_response_result AS (status net.request_status, message text, response net.http_response);
  END IF;
END $$;
CREATE SEQUENCE IF NOT EXISTS net.http_request_queue_id_seq;
CREATE UNLOGGED TABLE IF NOT EXISTS net.http_request_queue (
  id bigint NOT NULL DEFAULT pg_catalog.nextval('net.http_request_queue_id_seq'::pg_catalog.regclass),
  method net.http_method NOT NULL,
  url text NOT NULL,
  headers jsonb,
  body bytea,
  timeout_milliseconds integer NOT NULL
);
CREATE UNLOGGED TABLE IF NOT EXISTS net._http_response (
  id bigint,
  status_code integer,
  content_type text,
  headers jsonb,
  content text,
  timed_out boolean,
  error_msg text,
  created timestamp with time zone NOT NULL DEFAULT pg_catalog.now()
);
DO $$ BEGIN
  IF pg_catalog.to_regclass('net.http_request_queue_id_idx') IS NULL THEN
    ALTER SEQUENCE net.http_request_queue_id_seq OWNED BY net.http_request_queue.id;
    CREATE INDEX http_request_queue_id_idx ON net.http_request_queue (id);
  END IF;
  IF pg_catalog.to_regclass('net._http_response_created_idx') IS NULL THEN
    CREATE INDEX _http_response_created_idx ON net._http_response (created);
  END IF;
END $$;

-- curl_easy_escape: every byte but A-Z a-z 0-9 - . _ ~ as %XX.
CREATE OR REPLACE FUNCTION net._urlencode_string(string character varying) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $f$
  SELECT coalesce(pg_catalog.string_agg(CASE WHEN b BETWEEN 48 AND 57 OR b BETWEEN 65 AND 90 OR b BETWEEN 97 AND 122 OR b IN (45, 46, 95, 126)
                                             THEN pg_catalog.chr(b) ELSE '%' || pg_catalog.upper(pg_catalog.lpad(pg_catalog.to_hex(b), 2, '0')) END, '' ORDER BY i), '')
  FROM (SELECT i, pg_catalog.get_byte(pg_catalog.convert_to($1, 'UTF8'), i) AS b
        FROM pg_catalog.generate_series(0, pg_catalog.octet_length(pg_catalog.convert_to($1, 'UTF8')) - 1) AS i) AS bytes
$f$;

CREATE OR REPLACE FUNCTION net._encode_url_with_params_array(url text, params_array text[]) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $f$
  SELECT CASE WHEN pg_catalog.cardinality($2) = 0 THEN $1
              ELSE $1 || CASE WHEN pg_catalog.strpos($1, '?') > 0 THEN '&' ELSE '?' END || pg_catalog.array_to_string($2, '&') END
$f$;

CREATE OR REPLACE FUNCTION net.http_get(url text, params jsonb DEFAULT '{{}}'::jsonb, headers jsonb DEFAULT '{{}}'::jsonb,
  timeout_milliseconds integer DEFAULT 5000) RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE request_id bigint; params_array text[];
BEGIN
  SELECT coalesce(pg_catalog.array_agg(net._urlencode_string(key) || '=' || net._urlencode_string(value)), '{{}}')
    INTO params_array FROM pg_catalog.jsonb_each_text($2);
  INSERT INTO net.http_request_queue (method, url, headers, timeout_milliseconds)
  VALUES ('GET', net._encode_url_with_params_array($1, params_array), $3, $4) RETURNING id INTO request_id;
  RETURN request_id;
END $f$;

CREATE OR REPLACE FUNCTION net.http_post(url text, body jsonb DEFAULT '{{}}'::jsonb, params jsonb DEFAULT '{{}}'::jsonb,
  headers jsonb DEFAULT '{{"Content-Type": "application/json"}}'::jsonb, timeout_milliseconds integer DEFAULT 5000) RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE request_id bigint; params_array text[]; content_type text; request_headers jsonb := $4;
BEGIN
  SELECT header_value INTO content_type
    FROM pg_catalog.jsonb_each_text(coalesce(request_headers, '{{}}'::jsonb)) AS r(header_name, header_value)
   WHERE pg_catalog.lower(header_name) = 'content-type' LIMIT 1;
  IF content_type IS NULL THEN
    request_headers := request_headers || '{{"Content-Type": "application/json"}}'::jsonb;
  END IF;
  IF content_type <> 'application/json' THEN
    RAISE EXCEPTION 'Content-Type header must be "application/json"';
  END IF;
  SELECT coalesce(pg_catalog.array_agg(net._urlencode_string(key) || '=' || net._urlencode_string(value)), '{{}}')
    INTO params_array FROM pg_catalog.jsonb_each_text($3);
  INSERT INTO net.http_request_queue (method, url, headers, body, timeout_milliseconds)
  VALUES ('POST', net._encode_url_with_params_array($1, params_array), request_headers,
          pg_catalog.convert_to($2::text, 'UTF8'), $5) RETURNING id INTO request_id;
  RETURN request_id;
END $f$;

CREATE OR REPLACE FUNCTION net.http_delete(url text, params jsonb DEFAULT '{{}}'::jsonb, headers jsonb DEFAULT '{{}}'::jsonb,
  timeout_milliseconds integer DEFAULT 5000, body jsonb DEFAULT NULL::jsonb) RETURNS bigint
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE request_id bigint; params_array text[];
BEGIN
  SELECT coalesce(pg_catalog.array_agg(net._urlencode_string(key) || '=' || net._urlencode_string(value)), '{{}}')
    INTO params_array FROM pg_catalog.jsonb_each_text($2);
  INSERT INTO net.http_request_queue (method, url, headers, body, timeout_milliseconds)
  VALUES ('DELETE', net._encode_url_with_params_array($1, params_array), $3, pg_catalog.convert_to($5::text, 'UTF8'), $4)
  RETURNING id INTO request_id;
  RETURN request_id;
END $f$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cron, net FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA cron, net FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA cron, net FROM PUBLIC;
GRANT USAGE ON SCHEMA net TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION net.http_get(text, jsonb, jsonb, integer), net.http_post(text, jsonb, jsonb, jsonb, integer),
  net.http_delete(text, jsonb, jsonb, integer, jsonb), net._urlencode_string(character varying),
  net._encode_url_with_params_array(text, text[]) TO anon, authenticated, service_role;
-- RETURNING id needs the column: the request id, nothing else of anyone's requests.
GRANT INSERT, SELECT (id) ON net.http_request_queue TO anon, authenticated, service_role;
GRANT USAGE ON SEQUENCE net.http_request_queue_id_seq TO anon, authenticated, service_role;
GRANT SELECT, DELETE ON net._http_response TO service_role;
COMMIT;
{grants_sql(e, login)}"""


def grants_sql(e, login=None):
    """What the developer and `<e>_cron` hold on the two schemas, for whichever of them exists, and the
    membership that lets `<e>_cron` act as the developer. Run when the schemas are made, when database
    access makes the developer, and when Scheduled jobs are turned on or come back at a start. `login`
    True grants to `<e>_cron` whatever its login state (Scheduled jobs are on); None only when it can sign
    in, so database access turned on while Scheduled jobs are off grants it nothing a dump would name.
    Does nothing without the schemas."""
    check(e)
    condition = 'true' if login else 'rolcanlogin'
    developer, login = f'{e}_developer', f'{e}_cron'
    return f"""BEGIN;
{BOUNDS}
{SAFE}
DO $$ BEGIN
  IF pg_catalog.to_regnamespace('cron') IS NULL OR pg_catalog.to_regclass('net._http_response') IS NULL THEN
    RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{developer}') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA cron, net TO {developer}';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON cron.job TO {developer}';
    -- As `postgres` may on Supabase, and as much as the scheduler login may, so a job that runs RESET ROLE gains nothing.
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON cron.job_run_details TO {developer}';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE cron.runid_seq TO {developer}';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE cron.jobid_seq TO {developer}';
    EXECUTE 'GRANT SELECT, INSERT, DELETE ON net.http_request_queue TO {developer}';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE net.http_request_queue_id_seq TO {developer}';
    EXECUTE 'GRANT SELECT, INSERT, DELETE ON net._http_response TO {developer}';
    EXECUTE 'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA cron, net TO {developer}';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{login}' AND {condition}) THEN
    EXECUTE 'GRANT CONNECT ON DATABASE {e} TO {login}';
    EXECUTE 'GRANT USAGE ON SCHEMA cron, net TO {login}';
    EXECUTE 'GRANT SELECT ON cron.job TO {login}';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON cron.job_run_details TO {login}';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE cron.runid_seq TO {login}';
    EXECUTE 'GRANT SELECT, DELETE ON net.http_request_queue TO {login}';
    EXECUTE 'GRANT SELECT, INSERT, DELETE ON net._http_response TO {login}';
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{developer}') THEN
      EXECUTE 'GRANT {developer} TO {login} WITH INHERIT FALSE, SET TRUE';
    END IF;
  END IF;
END $$;
COMMIT;"""


# A job that runs RESET ROLE acts as `<e>_cron`, and any login may change its own password and role settings: in
# a transaction it leaves open (`set statement_timeout = 0` first), that holds the login's role rows, and an
# ALTER ROLE of the login under the operation lock would wait for it. So every statement that changes the login
# first ends its sessions, in the same bounded transaction (as durable_runtime.developer_login_sql does). Only
# `<e>_cron` itself can change those rows: the developer has no ADMIN on it and no CREATEROLE.
ROLE_BOUNDS = "SET LOCAL lock_timeout = '10s'; SET LOCAL statement_timeout = '30s';"


def _ended(login):
    return (f"SELECT pg_catalog.count(pg_catalog.pg_terminate_backend(pid)) FROM pg_catalog.pg_stat_activity "
            f"WHERE usename = '{login}' AND pid <> pg_catalog.pg_backend_pid();")


def login_sql(e, password):
    """`<e>_cron` created or reset, run as the superuser in `postgres`: its attributes, limit and password,
    and none of the role settings a job acting as it may have written (`ALTER ROLE ... SET`)."""
    check(e)
    if not password_ok(password):
        raise ValueError('Invalid scheduler password')
    login = f'{e}_cron'
    return f"""BEGIN;
{ROLE_BOUNDS}
SET LOCAL search_path TO pg_catalog, pg_temp;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{login}') THEN
    CREATE ROLE {login} NOLOGIN NOINHERIT;
  END IF;
END $$;
{_ended(login)}
-- PostgreSQL's default count, whatever the session or cluster says: the scheduler refuses a higher one.
SET LOCAL scram_iterations = 4096;
ALTER ROLE {login} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  CONNECTION LIMIT {CONNECTIONS} PASSWORD '{password}';
ALTER ROLE {login} RESET ALL;
DO $$ DECLARE target text; BEGIN
  FOR target IN SELECT d.datname FROM pg_catalog.pg_db_role_setting s JOIN pg_catalog.pg_database d ON d.oid = s.setdatabase
                WHERE s.setrole = '{login}'::pg_catalog.regrole LOOP
    EXECUTE pg_catalog.format('ALTER ROLE {login} IN DATABASE %I RESET ALL', target);
  END LOOP;
END $$;
GRANT CONNECT ON DATABASE {e} TO {login};
COMMIT;"""


def open_sql(e):
    """`<e>_cron` opened, after ending any session it still has (a closed login has none; bounded anyway)."""
    check(e)
    login = f'{e}_cron'
    return f"BEGIN; {ROLE_BOUNDS} {_ended(login)} ALTER ROLE {login} LOGIN; COMMIT;"


def close_sql(e):
    """`<e>_cron` closed and its sessions ended, run in `postgres`: ended first, in the same bounded transaction,
    so a session holding the login's role row cannot hold up the close, and again after the commit for one that
    signed in meanwhile. Nothing when it does not exist."""
    check(e)
    login = f'{e}_cron'
    return (f"BEGIN; {ROLE_BOUNDS} {_ended(login)} "
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{login}') THEN "
            f"ALTER ROLE {login} NOLOGIN; END IF; END $$; COMMIT; {_ended(login)}")


def revoke_sql(e):
    """What was granted to `<e>_cron` in the environment's database taken back, run there after closing it,
    so a dump taken while Scheduled jobs are off names no such role and restores anywhere."""
    check(e)
    login = f'{e}_cron'
    return f"""BEGIN;
{BOUNDS}
{SAFE}
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{login}') THEN RETURN; END IF;
  IF pg_catalog.to_regnamespace('cron') IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA cron FROM {login}';
    EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA cron FROM {login}';
    EXECUTE 'REVOKE ALL ON SCHEMA cron FROM {login}';
  END IF;
  IF pg_catalog.to_regnamespace('net') IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA net FROM {login}';
    EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA net FROM {login}';
    EXECUTE 'REVOKE ALL ON SCHEMA net FROM {login}';
  END IF;
  EXECUTE 'REVOKE CONNECT ON DATABASE {e} FROM {login}';
END $$;
COMMIT;"""


def limit_sql(e, limit):
    """The environment database's connection limit, bounded: a tenant session holding the database's catalog row
    (a no-op `GRANT ... ON DATABASE` left open in a transaction) would otherwise hold this, and with it the
    installation's operation lock, for as long as it likes. Fails after the bound instead."""
    check(e)
    if type(limit) is not int or limit < 0:
        raise ValueError('Invalid connection limit')
    return f"BEGIN; SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '15s'; ALTER DATABASE {e} CONNECTION LIMIT {limit}; COMMIT;"


def present_sql():
    """'t' when both schemas are in this database."""
    return "SELECT pg_catalog.to_regclass('cron.job') IS NOT NULL AND pg_catalog.to_regclass('net._http_response') IS NOT NULL;"
