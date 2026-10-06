"""Adversarial immutable-reference admission and native-store identities."""
import json
import subprocess
import tempfile
import unittest
from host_test_fixture import IsolatedHostCase
from pathlib import Path
from unittest.mock import patch
import image_identity as identity
import install_server
import pinned_images_check

PIN='sha256:'+'a'*64
OTHER='sha256:'+'b'*64
REF='docker.io/library/postgres@'+PIN


def pin():
    return {'id':PIN,'tag':'postgres:17-alpine','digests':['postgres@'+PIN]}


def result(value,code=0):
    return subprocess.CompletedProcess([],code,json.dumps(value),'Error response from daemon: No such image: postgres@'+PIN+'\n' if code else '')


class ReferenceTests(IsolatedHostCase):
    def test_version_tag_is_never_used_as_runtime_reference(self):
        self.assertEqual(identity.reference(pin()),REF)

    def test_docker_hub_aliases_are_explicitly_normalized(self):
        for alias in ('postgres','library/postgres','docker.io/postgres','index.docker.io/library/postgres','registry-1.docker.io/library/postgres'):
            self.assertEqual(identity.immutable(alias+'@'+PIN),REF)

    def test_registry_port_is_preserved(self):
        value={'tag':'registry.example:5000/team/db:v1','id':PIN,'digests':['registry.example:5000/team/db@'+PIN]}
        self.assertEqual(identity.reference(value),value['digests'][0])

    def test_no_bare_digest_tag_or_missing_reference_fallback(self):
        for changes in ({'digests':[]},{'digests':None},{'digests':[PIN]},{'digests':['postgres:17']},{'tag':'postgres'},{'tag':'postgres:latest'},{'id':'sha256:a'}):
            with self.subTest(changes=changes),self.assertRaises(identity.IdentityError):
                identity.reference({**pin(),**changes})

    def test_wrong_repository_or_conflicting_lock_digest_refuses(self):
        for refs in (['other@'+PIN],['postgres@'+OTHER],['postgres@'+PIN,'other@'+PIN],['postgres@'+PIN,'postgres@'+OTHER]):
            with self.subTest(refs=refs),self.assertRaises(identity.IdentityError):
                identity.reference({**pin(),'digests':refs})

    def test_malformed_repository_and_digest_are_rejected(self):
        for value in ('postgres@'+PIN+'extra','POSTGRES@'+PIN,'https://example/db@'+PIN,'example/db@@'+PIN,'example/../db@'+PIN,'example/db@SHA256:'+'a'*64):
            with self.subTest(value=value),self.assertRaises(identity.IdentityError):identity.immutable(value)


class ResolutionTests(IsolatedHostCase):
    def test_index_and_config_shaped_daemon_ids_both_resolve(self):
        for daemon_id in (PIN,OTHER):
            self.assertEqual(identity.resolved_id(REF,{'Id':daemon_id,'RepoDigests':['postgres@'+PIN]}),daemon_id)

    def test_additional_valid_daemon_repository_is_not_authorization(self):
        self.assertEqual(identity.resolved_id(REF,{'Id':OTHER,'RepoDigests':['other@'+OTHER,REF]}),OTHER)
        with self.assertRaises(identity.IdentityError):identity.resolved_id(REF,{'Id':PIN,'RepoDigests':['other@'+PIN]})

    def test_local_id_tags_and_digest_substrings_never_authorize(self):
        for record in ({'Id':PIN,'RepoDigests':[]},{'Id':PIN,'RepoDigests':['postgres@'+PIN+'suffix']},{'Id':'prefix'+PIN,'RepoDigests':[REF]},{'Id':OTHER,'RepoDigests':['postgres@'+OTHER],'RepoTags':['postgres:17']}):
            with self.subTest(record=record):self.assertFalse(pinned_images_check.evaluate(REF,record)[0])

    def test_inspect_requires_single_complete_record(self):
        for value in ({},[],[{},{}],[None],None):
            with self.subTest(value=value),self.assertRaises(identity.IdentityError):identity.record(json.dumps(value))
        with self.assertRaises(identity.IdentityError):identity.record('bad JSON')

    def test_checker_retains_inspect_parse_error(self):
        with patch.object(pinned_images_check,'docker',return_value=(0,'[]','')):
            record,error=pinned_images_check.inspect_image(REF)
        self.assertIsNone(record)
        self.assertIn('exactly one',error)


class InstallerTests(IsolatedHostCase):
    def test_inventory_uses_qualified_reference_and_proves_config_id(self):
        calls=[]
        def docker(*args,**kwargs):
            calls.append(args)
            return result([{'Id':OTHER,'RepoDigests':[REF]}])
        with patch.object(install_server,'pinned_images',return_value=[('fixture',PIN,REF)]),patch.object(install_server,'docker',side_effect=docker):
            self.assertEqual(install_server.images(),[])
        self.assertEqual(calls,[('image','inspect',REF)])

    def test_invalid_existing_proof_is_blocker_and_never_pulls(self):
        with patch.object(install_server,'pinned_images',return_value=[('fixture',PIN,REF)]),patch.object(install_server,'docker',return_value=result([{'Id':PIN,'RepoDigests':['other@'+PIN]}])),patch.object(install_server.docker_profile,'configured',return_value=False),patch.object(install_server,'pull_image') as pull:
            self.assertEqual(install_server.images()[0][0],'blocker')
            with self.assertRaises(identity.IdentityError):install_server.ensure_images()
            pull.assert_not_called()

    def test_missing_exact_reference_pulls_exact_ref_then_proves_it(self):
        with patch.object(install_server,'pinned_images',return_value=[('fixture',PIN,REF)]),patch.object(install_server,'docker',return_value=result([],1)) as inspect,patch.object(install_server.docker_profile,'configured',return_value=False),patch.object(install_server,'pull_image') as pull,patch.object(pinned_images_check,'inspect_image',return_value=({'Id':OTHER,'RepoDigests':[REF]},None)):
            install_server.ensure_images()
        inspect.assert_called_once_with('image','inspect',REF,check=False)
        pull.assert_called_once_with('fixture',REF,'1/1')

    def test_pull_with_no_qualified_digest_proof_refuses(self):
        with patch.object(install_server,'pinned_images',return_value=[('fixture',PIN,REF)]),patch.object(install_server,'docker',return_value=result([],1)),patch.object(install_server.docker_profile,'configured',return_value=False),patch.object(install_server,'pull_image'),patch.object(pinned_images_check,'inspect_image',return_value=({'Id':PIN,'RepoDigests':[]},None)):
            with self.assertRaises(SystemExit):install_server.ensure_images()

    def test_malformed_lock_entry_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'lab').mkdir()
            path=root/'lab'/'fixture.json'
            for entry in ({'auth':pin(),'other':{}},{'id':PIN,'tag':'postgres:17'},{}):
                path.write_text(json.dumps(entry))
                with patch.object(install_server,'ROOT',root),patch.object(install_server,'LOCKS',('fixture.json',)),self.assertRaises(identity.IdentityError):install_server.pinned_images()


class InspectFailureTests(IsolatedHostCase):
    def test_nonmissing_failures_block_even_after_a_truly_missing_entry(self):
        missing=subprocess.CompletedProcess([],1,'[]\n','Error response from daemon: No such image: postgres@'+PIN+'\n')
        for error in ('permission denied','Cannot connect to the Docker daemon','',
                      'Error response from daemon: No such image: other@'+PIN,
                      'Error response from daemon: No such image: postgres@'+PIN+'\npermission denied'):
            failed=subprocess.CompletedProcess([],1,'[]\n',error)
            pins=[('first',PIN,REF),('second',PIN,REF)]
            with self.subTest(error=error),patch.object(install_server,'pinned_images',return_value=pins),patch.object(install_server,'docker',side_effect=[missing,failed]),patch.object(install_server.docker_profile,'configured',return_value=False),patch.object(install_server,'pull_image') as pull,patch.object(pinned_images_check,'inspect_image',return_value=({'Id':OTHER,'RepoDigests':[REF]},None)):
                with self.assertRaises(identity.IdentityError):install_server.ensure_images()
                pull.assert_not_called()

    def test_inventory_reports_nonmissing_native_error_as_blocker(self):
        native_error='permission denied opening Docker socket'
        failed=subprocess.CompletedProcess([],1,'[]\n',native_error)
        with patch.object(install_server,'pinned_images',return_value=[('fixture',PIN,REF)]),patch.object(install_server,'docker',return_value=failed):
            findings=install_server.images()
        self.assertEqual(findings[0][0],'blocker')
        self.assertIn(native_error,findings[0][1])


class MissingEvidenceTests(IsolatedHostCase):
    def test_native_missing_alias_is_bound_to_requested_reference(self):
        for alias in ('postgres','docker.io/library/postgres','index.docker.io/library/postgres'):
            self.assertTrue(identity.is_missing(REF,'[]\n','Error response from daemon: No such image: '+alias+'@'+PIN+'\n'))

    def test_absence_substring_or_unexpected_stdout_is_not_proof(self):
        error='Error response from daemon: No such image: postgres@'+PIN
        for output,detail in (('bad',error),('[{}]',error),('{}',error),('[]','prefix '+error),('[]',error+' suffix'),('[]',error+'\nother failure'),('[]',''),('[]',error.replace(PIN,OTHER))):
            with self.subTest(output=output,detail=detail):self.assertFalse(identity.is_missing(REF,output,detail))

    def test_subprocess_failure_blocks_inventory_and_accumulated_pulls(self):
        missing=subprocess.CompletedProcess([],1,'[]','Error response from daemon: No such image: postgres@'+PIN)
        for error in (OSError('native CLI absent'),subprocess.TimeoutExpired('docker',60)):
            with self.subTest(error=error),patch.object(install_server,'pinned_images',return_value=[('first',PIN,REF),('second',PIN,REF)]),patch.object(install_server,'docker',side_effect=[missing,error]),patch.object(install_server.docker_profile,'configured',return_value=False),patch.object(install_server,'pull_image') as pull:
                with self.assertRaises((OSError,subprocess.SubprocessError)):install_server.ensure_images()
                pull.assert_not_called()
            with patch.object(install_server,'pinned_images',return_value=[('fixture',PIN,REF)]),patch.object(install_server,'docker',side_effect=error):
                self.assertEqual(install_server.images()[0][0],'blocker')
