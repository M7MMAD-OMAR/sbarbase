"""Configured target capture cannot silently recapture a replacement by name."""
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import atomic_hba
import hba_authority as authority
import hba_target as target


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.value={'Id':'a'*64,'Name':'/fixture-db','Image':'sha256:'+'b'*64,'Config':{'Labels':{'io.sbarbase.owner':'fixture'}},'State':{'Running':True}}
        self.prepared=atomic_hba.Prepared('a'*64,'c'*64,'local all all trust\n')
        self.snapshot=authority.Snapshot('a'*64,'unused','unused')

    def docker(self,value):return Mock(return_value=SimpleNamespace(stdout=json.dumps([value])))

    def test_captured_identity_is_immutable_and_recheck_uses_id(self):
        docker=self.docker(self.value)
        saved=target.capture(docker,'fixture-db','fixture',self.value['Image'])
        with self.assertRaises(FrozenInstanceError):saved.container_id='d'*64
        target.require(docker,saved,self.snapshot,self.prepared)
        self.assertEqual(docker.call_args.args,('inspect','a'*64))

    def test_wrong_name_owner_image_or_stopped_container_refuses_capture(self):
        for key,value in [('Name','/other'),('Image','sha256:'+'d'*64),('Config',{'Labels':{'io.sbarbase.owner':'other'}}),('State',{'Running':False})]:
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):target.capture(self.docker({**self.value,key:value}),'fixture-db','fixture',self.value['Image'])

    def test_mismatched_intent_and_replaced_identity_never_recapture(self):
        saved=target.Target('a'*64,'fixture-db','fixture',self.value['Image'])
        docker=self.docker(self.value)
        with self.assertRaises(RuntimeError):target.require(docker,saved,authority.Snapshot('d'*64,'unused','unused'),self.prepared)
        docker.assert_not_called()
        docker=self.docker({**self.value,'Id':'d'*64})
        with self.assertRaises(RuntimeError):target.require(docker,saved,self.snapshot,self.prepared)
        docker.assert_called_once_with('inspect','a'*64)


if __name__=='__main__':unittest.main()
