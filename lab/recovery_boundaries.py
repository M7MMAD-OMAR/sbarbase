"""Read back complete exported database boundary metadata after restoration."""


def settings_query(e, selected):
    """The role settings an environment's export carries: those in its database, and the global
    ones of its own logins. Another environment's logins are left out: any login may set its own
    settings in any database (ALTER ROLE ... IN DATABASE), even one it cannot connect to, so a
    tenant could otherwise make this environment's restore refuse. Every other role, this
    environment's own other logins included, stays in, so a setting the restore would not
    reproduce is still refused."""
    return (f"SELECT coalesce(r.rolname,'ALL') AS role,coalesce(d.datname,'ALL') AS database,s.setconfig FROM pg_db_role_setting s "
            f"LEFT JOIN pg_roles r ON r.oid=s.setrole LEFT JOIN pg_database d ON d.oid=s.setdatabase "
            f"WHERE (d.datname='{e}' OR (s.setdatabase=0 AND r.rolname IN ({selected}))) "
            f"AND (r.rolname IS NULL OR r.rolname IN ({selected}) OR left(r.rolname, {len(e) + 1}) = '{e}_' "
            f"OR r.rolname !~ '^e_[a-f0-9]{{24}}_') ORDER BY 1,2")


PLATFORM_PINS = ('search_path', 'statement_timeout', 'transaction_timeout')


def without_pin(settings, e):
    """The role settings with the platform's own three settings of the REST login in its database left
    out. A restore target pins that login before it starts REST (durable_runtime.rest_pin_sql: removes
    every other setting and writes these three), and every start does the same, so what an archive carried
    for them, a tenant's included, is not what the target holds. Nothing else is allowed to differ."""
    result = []
    for row in settings:
        config = list(row['setconfig'])
        if row['role'] == e + '_rest' and row['database'] == e:
            config = [item for item in config if item.partition('=')[0] not in PLATFORM_PINS]
        if config:
            result.append({**row, 'setconfig': config})
    return result


def verify(payload, rows):
    e=payload['environment']
    if not __import__('re').fullmatch(r'e_[a-f0-9]{24}',e):
        raise ValueError('Invalid environment')
    selected=','.join("'"+e+'_'+kind+"'" for kind in ('auth','rest','storage'))
    queries={
        'roles':f"SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_roles WHERE rolname IN ({selected}) ORDER BY rolname",
        'memberships':f"SELECT parent.rolname AS parent,member.rolname AS member,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles member ON member.oid=m.member WHERE member.rolname IN ({selected}) ORDER BY 1,2",
        'database_acl':f"SELECT coalesce(grantee.rolname,'PUBLIC') AS grantee,grantor.rolname AS grantor,a.privilege_type,a.is_grantable FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a LEFT JOIN pg_roles grantee ON grantee.oid=a.grantee JOIN pg_roles grantor ON grantor.oid=a.grantor WHERE d.datname='{e}' ORDER BY 1,3",
        'settings':settings_query(e,selected),
    }
    checks=[]
    for key,query in queries.items():
        found=rows(query);expected=payload[key]
        if key=='settings':found,expected=without_pin(found,e),without_pin(expected,e)
        if found!=expected:
            raise RuntimeError('Restored boundary mismatch: '+key)
        checks.append('complete restored '+key+' matches encrypted export')
    return checks
