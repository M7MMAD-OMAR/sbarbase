"""SQLite tests for the installed Python password and MFA effect guard."""
import copy
import os
from unittest.mock import patch
import sqlite3
import unittest
from lab.management_access import current_grant, factor_matches


class OptionalMfaAuthority(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('''CREATE TABLE management_auth_policy(singleton,mfa_required,epoch);
          CREATE TABLE management_mfa_epoch(actor,epoch,revoked);
          CREATE TABLE management_password_grant(session,actor,password,epoch,policy_epoch,expires);
          CREATE TABLE management_mfa_grant(session,actor,factor,verified,epoch,expires);
          INSERT INTO management_auth_policy VALUES(1,0,3);
          INSERT INTO management_mfa_epoch VALUES('owner',2,800);
          INSERT INTO management_password_grant VALUES('session','owner',900,2,3,1200);''')
        self.value = {'authorization': 'password', 'factor': None, 'epoch': 2, 'policyEpoch': 3,
                      'identity': {'actor': 'owner', 'session': 'session', 'aal': 'aal1', 'verifiedAt': 0,
                                   'expiresAt': 1200, 'passwordAt': 900, 'factors': []}}

    def tearDown(self):
        self.db.close()

    def check(self, factors=None, now=1000):
        return current_grant(self.db, self.value, factors or [], now)

    def test_current_password_grant(self):
        self.assertEqual(self.check(), (1200,))

    def test_enrolled_factor_blocks_password(self):
        for kind in ('totp', 'phone'):
            with self.assertRaises(ValueError):
                self.check([{'id': 'factor', 'status': 'verified', 'factor_type': kind}])

    def test_policy_required_and_toggle_aba(self):
        self.db.execute('UPDATE management_auth_policy SET mfa_required=1')
        with self.assertRaises(ValueError):
            self.check()
        self.db.execute('UPDATE management_auth_policy SET mfa_required=0,epoch=5')
        with self.assertRaises(ValueError):
            self.check()

    def test_logout_epoch_and_missing_session_grant(self):
        self.db.execute('UPDATE management_mfa_epoch SET epoch=3')
        with self.assertRaises(ValueError):
            self.check()
        self.db.execute('UPDATE management_mfa_epoch SET epoch=2')
        self.db.execute('DELETE FROM management_password_grant')
        with self.assertRaises(ValueError):
            self.check()

    def test_expiry_and_password_freshness(self):
        with self.assertRaises(ValueError):
            self.check(now=1200)
        self.value['identity']['expiresAt'] = 60000
        self.db.execute('UPDATE management_password_grant SET expires=60000')
        with self.assertRaises(ValueError):
            self.check(now=44100)

    def test_configured_password_window_does_not_override_revocation(self):
        self.value['identity']['expiresAt'] = 100000
        self.db.execute('UPDATE management_password_grant SET expires=100000')
        with patch.dict(os.environ, {'SBARBASE_MANAGEMENT_SESSION_SECONDS': '43200'}):
            with self.assertRaises(ValueError):
                self.check(now=50900)
        with patch.dict(os.environ, {'SBARBASE_MANAGEMENT_SESSION_SECONDS': '2592000'}):
            self.assertEqual(self.check(now=50900), (100000,))
            self.db.execute('UPDATE management_mfa_epoch SET epoch=3')
            with self.assertRaises(ValueError):
                self.check(now=50900)

    def test_mode_cannot_be_inferred_or_substituted(self):
        original = copy.deepcopy(self.value)
        for mutation in ({'authorization': 'unknown'}, {'authorization': 'mfa'}, {'factor': 'factor'}):
            self.value = {**original, **mutation}
            with self.assertRaises(ValueError):
                self.check()

    def test_mfa_retains_exact_factor_and_revocation_rules(self):
        factors = [{'id': 'factor', 'status': 'verified', 'factor_type': 'totp'}]
        self.value.update(authorization='mfa', factor='factor')
        self.value['identity'].update(aal='aal2', verifiedAt=950, factors=factors)
        self.db.execute("INSERT INTO management_mfa_grant VALUES('session','owner','factor',950,2,1200)")
        self.assertTrue(factor_matches(self.value, factors))
        self.assertEqual(self.check(factors), ('factor', 1200, 2, 2, 800))
        with self.assertRaises(ValueError):
            self.check([])
        self.db.execute('UPDATE management_mfa_epoch SET revoked=950')
        with self.assertRaises(ValueError):
            self.check(factors)


if __name__ == '__main__':
    unittest.main()
