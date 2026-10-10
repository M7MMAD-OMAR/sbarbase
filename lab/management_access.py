"""Catalog-bound native authentication checks shared by lifecycle effect processes."""


def factor_matches(value, factors):
    mode = value.get('authorization')
    factor = value.get('factor')
    if mode == 'password':
        return (factor is None and value['identity']['aal'] == 'aal1'
                and value['identity']['verifiedAt'] == 0
                and not any(item.get('status') == 'verified' for item in factors))
    return (mode == 'mfa' and isinstance(factor, str)
            and any(item.get('id') == factor and item.get('status') == 'verified'
                    and item.get('factor_type') == 'totp' for item in factors))


def current_grant(connection, value, factors, now):
    identity = value['identity']
    actor = identity['actor']
    epoch = connection.execute('SELECT epoch FROM management_mfa_epoch WHERE actor=?', (actor,)).fetchone()
    policy = connection.execute('SELECT mfa_required,epoch FROM management_auth_policy WHERE singleton=1').fetchone()
    if (not policy or policy[1] != value.get('policyEpoch') or (epoch[0] if epoch else 0) != value['epoch']
            or not factor_matches(value, identity['factors']) or not factor_matches(value, factors)
            or identity['expiresAt'] <= now or not 0 < identity['passwordAt'] <= now
            or now - identity['passwordAt'] >= 43200):
        raise ValueError('Lifecycle authority revoked')
    if value['authorization'] == 'password':
        grant = connection.execute('''SELECT expires FROM management_password_grant
          WHERE actor=? AND session=? AND password=? AND epoch=? AND policy_epoch=?''',
          (actor, identity['session'], identity['passwordAt'], value['epoch'], policy[1])).fetchone()
        if policy[0] != 0 or not grant or grant[0] <= now:
            raise ValueError('Lifecycle authority revoked')
        return tuple(grant)
    grant = connection.execute('''SELECT g.factor,g.expires,g.epoch,COALESCE(e.epoch,0),COALESCE(e.revoked,0)
      FROM management_mfa_grant g LEFT JOIN management_mfa_epoch e ON e.actor=g.actor
      WHERE g.actor=? AND g.session=? AND g.verified=?''',
      (actor, identity['session'], identity['verifiedAt'])).fetchone()
    if (not grant or grant[0] != value['factor'] or grant[1] <= now
            or grant[2] != grant[3] or grant[2] != value['epoch'] or identity['verifiedAt'] <= grant[4]):
        raise ValueError('Lifecycle authority revoked')
    return tuple(grant)
