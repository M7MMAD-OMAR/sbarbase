-- Adapted from the pinned Supabase Postgres 17.6.1.166 image.
-- Preserve upstream queue backup compatibility; scope ownership to this database.
do $$
declare
  extoid oid := (select oid from pg_extension where extname = 'pgmq');
  extversion text := (select extversion from pg_extension where extname = 'pgmq');
  search_path text := (select current_setting('search_path'));
  -- Upstream targets one project's postgres role. Sbarbase has one owner per database.
  owner_role name := CASE WHEN current_database() ~ '^e_[a-f0-9]{24}$'
    THEN current_database() || '_developer' ELSE 'postgres' END;
  r record;
  cls pg_class%rowtype;
begin
  perform set_config('search_path', '', true);

/*
    Override the pgmq.drop_queue to check if relevant tables are owned
    by the pgmq extension before attempting to run
    `alter extension pgmq drop table ...`
    this is necessary becasue, to enable nightly logical backups to include user queues
    we automatically detach them from pgmq.

    this update is backwards compatible with version 1.4.4 but should be removed once we're on
    physical backups everywhere
*/
-- Detach and delete the official function
if extversion = '1.4.4' then
  alter extension pgmq drop function pgmq.drop_queue;
  drop function pgmq.drop_queue;
else -- 1.5.1+
  alter extension pgmq drop function pgmq.drop_queue(TEXT);
  drop function pgmq.drop_queue(TEXT);
end if;

-- Create and reattach the patched function
CREATE FUNCTION pgmq.drop_queue(queue_name TEXT)
RETURNS BOOLEAN AS $func$
DECLARE
    qtable TEXT := pgmq.format_table_name(queue_name, 'q');
    qtable_seq TEXT := qtable || '_msg_id_seq';
    fq_qtable TEXT := 'pgmq.' || qtable;
    atable TEXT := pgmq.format_table_name(queue_name, 'a');
    fq_atable TEXT := 'pgmq.' || atable;
    partitioned BOOLEAN;
BEGIN
    EXECUTE FORMAT(
        $QUERY$
        SELECT is_partitioned FROM pgmq.meta WHERE queue_name = %L
        $QUERY$,
        queue_name
    ) INTO partitioned;

    -- NEW CONDITIONAL CHECK
    if exists (
        select 1
        from pg_class c
        join pg_depend d on c.oid = d.objid
        join pg_extension e on d.refobjid = e.oid
        where c.relname = qtable and e.extname = 'pgmq'
    ) then

        EXECUTE FORMAT(
            $QUERY$
            ALTER EXTENSION pgmq DROP TABLE pgmq.%I
            $QUERY$,
            qtable
        );

    end if;

    -- NEW CONDITIONAL CHECK
    if exists (
        select 1
        from pg_class c
        join pg_depend d on c.oid = d.objid
        join pg_extension e on d.refobjid = e.oid
        where c.relname = qtable_seq and e.extname = 'pgmq'
    ) then
        EXECUTE FORMAT(
            $QUERY$
            ALTER EXTENSION pgmq DROP SEQUENCE pgmq.%I
            $QUERY$,
            qtable_seq
        );

    end if;

    -- NEW CONDITIONAL CHECK
    if exists (
        select 1
        from pg_class c
        join pg_depend d on c.oid = d.objid
        join pg_extension e on d.refobjid = e.oid
        where c.relname = atable and e.extname = 'pgmq'
    ) then

    EXECUTE FORMAT(
        $QUERY$
        ALTER EXTENSION pgmq DROP TABLE pgmq.%I
        $QUERY$,
        atable
    );

    end if;

    -- NO CHANGES PAST THIS POINT

    EXECUTE FORMAT(
        $QUERY$
        DROP TABLE IF EXISTS pgmq.%I
        $QUERY$,
        qtable
    );

    EXECUTE FORMAT(
        $QUERY$
        DROP TABLE IF EXISTS pgmq.%I
        $QUERY$,
        atable
    );

     IF EXISTS (
          SELECT 1
          FROM information_schema.tables
          WHERE table_name = 'meta' and table_schema = 'pgmq'
     ) THEN
        EXECUTE FORMAT(
            $QUERY$
            DELETE FROM pgmq.meta WHERE queue_name = %L
            $QUERY$,
            queue_name
        );
     END IF;

     IF partitioned THEN
        EXECUTE FORMAT(
          $QUERY$
          DELETE FROM %I.part_config where parent_table in (%L, %L)
          $QUERY$,
          pgmq._get_pg_partman_schema(), fq_qtable, fq_atable
        );
     END IF;

    RETURN TRUE;
END;
$func$ LANGUAGE plpgsql;

if extversion = '1.4.4' then
  alter extension pgmq add function pgmq.drop_queue;
else -- 1.5.1+
  alter extension pgmq add function pgmq.drop_queue(TEXT);
end if;


  -- Keep ordinary queues in logical backups as independent objects. The
  -- upstream drop_queue patch above already accepts detached queue objects.
  CREATE OR REPLACE FUNCTION pgmq.create(queue_name TEXT) RETURNS VOID
  LANGUAGE plpgsql AS $create_queue$
  DECLARE member record;
  BEGIN
    PERFORM pgmq.create_non_partitioned(queue_name);
    FOR member IN
      SELECT c.oid, c.relkind, n.nspname, c.relname FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_catalog.pg_depend d ON d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = c.oid
      JOIN pg_catalog.pg_extension e ON e.oid = d.refobjid AND d.refclassid = 'pg_catalog.pg_extension'::regclass
      WHERE e.extname = 'pgmq' AND d.deptype = 'e' AND n.nspname = 'pgmq'
        AND c.relname IN (pgmq.format_table_name(queue_name, 'q'),
                         pgmq.format_table_name(queue_name, 'a'),
                         pgmq.format_table_name(queue_name, 'q') || '_msg_id_seq')
    LOOP
      IF EXISTS (SELECT 1 FROM pg_catalog.pg_depend d
                 WHERE d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = member.oid
                   AND d.refclassid = 'pg_catalog.pg_extension'::regclass AND d.deptype = 'e') THEN
        EXECUTE pg_catalog.format('ALTER EXTENSION pgmq DROP %s %I.%I',
          CASE WHEN member.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
          member.nspname, member.relname);
      END IF;
    END LOOP;
  END $create_queue$;

  update pg_extension set extowner = owner_role::regrole where extname = 'pgmq';

  for r in (select * from pg_depend where refobjid = extoid) loop


    if r.classid = 'pg_type'::regclass then

      -- store the type's relkind
      select * into cls from pg_class c where c.reltype = r.objid;

      if r.objid::regtype::text like '%[]' then
        -- do nothing (skipping array type)

      elsif cls.relkind in ('r', 'p', 'f', 'm') then
        -- table-like objects (regular table, partitioned, foreign, materialized view)
        execute format('alter table pgmq.%I owner to %I;', cls.relname, owner_role);

      else
        execute(format('alter type %s owner to %I;', r.objid::regtype, owner_role));

      end if;

    elsif r.classid = 'pg_proc'::regclass then
      execute(format('alter function %s(%s) owner to %I;', r.objid::regproc, pg_get_function_identity_arguments(r.objid), owner_role));

    elsif r.classid = 'pg_class'::regclass then
      execute(format('alter table %s owner to %I;', r.objid::regclass, owner_role));

    else
      raise exception 'error on pgmq after-create script: unexpected object type %', r.classid;

    end if;
  end loop;

  -- Preserve queue discovery metadata in native logical dumps as extension
  -- configuration data. This trusted after-create hook runs after the CREATE
  -- EXTENSION command, outside pg_extension_config_dump's creation context.
  update pg_catalog.pg_extension
     set extconfig = pg_catalog.array_append(coalesce(extconfig, array[]::oid[]), 'pgmq.meta'::regclass::oid),
         extcondition = pg_catalog.array_append(coalesce(extcondition, array[]::text[]), '')
   where extname = 'pgmq'
     and not ('pgmq.meta'::regclass::oid = any(coalesce(extconfig, array[]::oid[])));

  -- Existing ordinary queue relations also become independent dump objects.
  -- Inspect only catalogs here, never tenant-replaceable helper functions.
  for r in (
    select c.oid, c.relkind, n.nspname, c.relname from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    join pg_catalog.pg_depend d on d.classid = 'pg_catalog.pg_class'::regclass and d.objid = c.oid
    where d.refclassid = 'pg_catalog.pg_extension'::regclass and d.refobjid = extoid
      and d.deptype = 'e' and n.nspname = 'pgmq'
      and c.relkind in ('r', 'p', 'S') and pg_catalog.left(c.relname, 2) in ('q_', 'a_')
  ) loop
    if exists (select 1 from pg_catalog.pg_depend d
               where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = r.oid
                 and d.refclassid = 'pg_catalog.pg_extension'::regclass and d.deptype = 'e') then
      execute pg_catalog.format('ALTER EXTENSION pgmq DROP %s %I.%I',
        case when r.relkind = 'S' then 'SEQUENCE' else 'TABLE' end, r.nspname, r.relname);
    end if;
  end loop;

  -- restore configs
  perform set_config('search_path', search_path, true);
end $$;
