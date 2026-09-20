"""Read back complete exported database boundary metadata after restoration."""
def verify(payload, rows):
    e=payload['environment']
    if not __import__('re').fullmatch(r'e_[a-f0-9]{24}',e):
        raise ValueError('Invalid environment')
    selected=','.join("'"+e+'_'+kind+"'" for kind in ('auth','rest','storage'))
    queries={
        'roles':f"SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_roles WHERE rolname IN ({selected}) ORDER BY rolname",
        'memberships':f"SELECT parent.rolname AS parent,member.rolname AS member,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles member ON member.oid=m.member WHERE member.rolname IN ({selected}) ORDER BY 1,2",
        'database_acl':f"SELECT coalesce(grantee.rolname,'PUBLIC') AS grantee,grantor.rolname AS grantor,a.privilege_type,a.is_grantable FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a LEFT JOIN pg_roles grantee ON grantee.oid=a.grantee JOIN pg_roles grantor ON grantor.oid=a.grantor WHERE d.datname='{e}' ORDER BY 1,3",
        'settings':f"SELECT coalesce(r.rolname,'ALL') AS role,coalesce(d.datname,'ALL') AS database,s.setconfig FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid=s.setrole LEFT JOIN pg_database d ON d.oid=s.setdatabase WHERE d.datname='{e}' OR (s.setdatabase=0 AND r.rolname IN ({selected})) ORDER BY 1,2",
    }
    checks=[]
    for key,query in queries.items():
        if rows(query)!=payload[key]:
            raise RuntimeError('Restored boundary mismatch: '+key)
        checks.append('complete restored '+key+' matches encrypted export')
    return checks
