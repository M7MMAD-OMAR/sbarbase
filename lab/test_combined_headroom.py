"""Combined-start headroom: the preflight must state what a start really needs."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_server


class HeadroomTests(unittest.TestCase):
    def test_a_fresh_host_needs_only_the_placement_and_reserve(self):
        needed,composition=install_server.headroom_requirement(False,None)
        self.assertEqual(needed,install_server.PLANNED_MIB+install_server.RESERVE_MIB)
        self.assertEqual(composition,'5888 MiB placement + 2560 MiB reserve')

    def test_a_moved_installation_adds_the_measured_running_stage(self):
        needed,composition=install_server.headroom_requirement(True,712)
        self.assertEqual(needed,5888+2560+712)
        self.assertIn('712 MiB measured for the running source stage',composition)

    def test_a_measurement_is_ignored_on_a_host_with_no_moved_installation(self):
        needed,_=install_server.headroom_requirement(False,712)
        self.assertEqual(needed,5888+2560)

    def test_the_measured_footprint_is_read_from_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'docs'/'evidence').mkdir(parents=True)
            (root/'docs'/'evidence'/'source-stage-footprint.json').write_text('{"total_mib": 934}')
            with patch.object(install_server,'ROOT',root):
                self.assertEqual(install_server.combined_stage_measured_mib(),934)
            (root/'docs'/'evidence'/'source-stage-footprint.json').write_text('{"total_mib": 0}')
            with patch.object(install_server,'ROOT',root):
                self.assertIsNone(install_server.combined_stage_measured_mib())
            (root/'docs'/'evidence'/'source-stage-footprint.json').unlink()
            with patch.object(install_server,'ROOT',root):
                self.assertIsNone(install_server.combined_stage_measured_mib())


class StatsParsingTests(unittest.TestCase):
    """Actual usage is read from the daemon, not assumed from limits."""

    def parse(self,text):
        import installation_runtime
        with patch.object(installation_runtime.lab,'docker') as docker:
            docker.return_value.stdout=text
            return installation_runtime.sample_usage(['a','b','c'])

    def test_mib_and_gib_are_parsed(self):
        usage=self.parse('db\t312.5MiB / 1GiB\nstorage\t1.25GiB / 2GiB\n')
        self.assertEqual(usage['db'],int(312.5*1024**2))
        self.assertEqual(usage['storage'],int(1.25*1024**3))

    def test_a_container_with_no_usage_is_omitted(self):
        usage=self.parse('db\t0B / 1GiB\n')
        self.assertNotIn('db',usage)

    def test_unparsable_lines_are_skipped_without_crashing(self):
        self.assertEqual(self.parse('not a stats line\n'),{})

    def test_sampling_targets_the_source_placement_only(self):
        source=(Path(__file__).resolve().parent/'installation_runtime.py').read_text()
        self.assertIn("name.startswith(runtime.PREFIX+'-') or name==runtime.DB",source)


if __name__=='__main__':unittest.main()

class DerivedPlacementTests(unittest.TestCase):
    """The requirement comes from what the next start runs, not from one constant."""

    @staticmethod
    def item(memory_mib,cpus):
        return {'HostConfig':{'Memory':memory_mib*1024**2,'NanoCpus':int(cpus*1e9)}}

    def test_an_empty_host_needs_the_three_system_containers_only(self):
        self.assertEqual(install_server.fresh_placement(),(1792,1.75))
        memory,cpus,origin=install_server.planned_placement(inspect=lambda:[],targets=lambda:[])
        self.assertEqual((memory,cpus),(1792,1.75))
        self.assertEqual(origin,'fresh placement')

    def test_retained_containers_are_counted_at_their_own_limits(self):
        # The fresh system rows plus two environments of Auth and REST each.
        items=[self.item(1024,1),self.item(512,.5),self.item(256,.25)]+[self.item(256,.25) for _ in range(4)]
        memory,cpus,origin=install_server.planned_placement(inspect=lambda:items,targets=lambda:[])
        self.assertEqual((memory,cpus),(1792+4*256,2.75))
        self.assertIn('7 containers',origin)

    def test_a_container_without_a_finite_limit_falls_back_to_the_full_placement(self):
        items=[self.item(1024,1),{'HostConfig':{'Memory':0,'NanoCpus':0}}]
        memory,cpus,_=install_server.planned_placement(inspect=lambda:items,targets=lambda:[])
        self.assertEqual((memory,cpus),(install_server.PLANNED_MIB,install_server.PLANNED_CPUS))

    def test_the_stated_composition_names_where_the_figure_came_from(self):
        needed,composition=install_server.headroom_requirement(False,None,1792,'fresh placement')
        self.assertEqual(needed,1792+install_server.RESERVE_MIB)
        self.assertEqual(composition,'1792 MiB fresh placement + 2560 MiB reserve')

    def test_limits_are_parsed_the_way_the_tier_table_writes_them(self):
        import resource_policy
        self.assertEqual(resource_policy.memory_mib('1024m'),1024)
        self.assertEqual(resource_policy.memory_mib('2g'),2048)

    def test_the_runtime_start_check_and_the_preflight_use_one_computation(self):
        # Found by the second empty-VM rehearsal: the preflight admitted a 6 GB
        # server at 4352 MiB and the runtime then refused below a fixed 6 GiB.
        import resource_policy
        self.assertEqual(resource_policy.start_placement(0),(1792,1.75))
        self.assertEqual(resource_policy.start_placement(2),(1792+4*256,2.75))
        self.assertEqual(install_server.RESERVE_MIB,resource_policy.START_RESERVE_MIB)
        source=(Path(install_server.__file__).parent/'durable_runtime.py').read_text()
        self.assertIn("resource_policy.start_placement(len(self.values['environments']), realtime_count(), functions_count())",source)
        self.assertNotIn('6*1024*1024',source)


class RestartHeadroomTests(unittest.TestCase):
    """A new environment must not leave the service unable to start after a reboot."""

    def test_a_small_server_admits_what_it_can_restart(self):
        import resource_policy
        mib=1024**2
        # 6 GB guest: about 4800 MiB available while three system containers use 200 MiB.
        placement,cpus=resource_policy.start_placement(1)
        self.assertTrue(resource_policy.restart_fits(placement,cpus,4800*mib,200*mib,4))
        placement,cpus=resource_policy.start_placement(2)
        self.assertFalse(resource_policy.restart_fits(placement,cpus,4800*mib,200*mib,4))

    def test_cpu_ceilings_are_checked_by_the_same_rule(self):
        import resource_policy
        mib=1024**2
        placement,cpus=resource_policy.start_placement(1)
        self.assertFalse(resource_policy.restart_fits(placement,cpus,64*1024*mib,0,2))
        self.assertTrue(resource_policy.restart_fits(placement,cpus,64*1024*mib,0,3))

    def test_the_provisioning_path_refuses_on_restart_headroom(self):
        source=(Path(install_server.__file__).parent/'durable_runtime.py').read_text()
        self.assertIn("raise AdmissionLimitError('Restart headroom unavailable')",source)


class RecoveryTargetPlacementTests(unittest.TestCase):
    """On an installation that moved an environment, the restart check and the
    preflight count the same containers: the source plus the current recovery target."""

    MIB=1024**2
    PREFIX='sbarbase-restore-abc'

    @staticmethod
    def item(memory_mib,cpus):
        return {'HostConfig':{'Memory':memory_mib*1024**2,'NanoCpus':int(cpus*1e9)}}

    def setUp(self):
        import json
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)
        (self.state/'recovery-target.json').write_text(json.dumps({'prefix':self.PREFIX}))
        # The source rows for two environments, retained at the limits the policy launches them with.
        self.source={'sbarbase-durable-db':self.item(1024,1),'sbarbase-durable-storage':self.item(512,.5),
                     'sbarbase-durable-management-auth':self.item(256,.25)}
        for index in range(2):
            for kind in ('auth','rest'):self.source[f'sbarbase-durable-e_{index}-{kind}']=self.item(256,.25)
        # The current target, and one historical generation the runtime does not start.
        self.target={self.PREFIX+'-db':self.item(1024,1),self.PREFIX+'-auth':self.item(256,.25),
                     self.PREFIX+'-rest':self.item(256,.25),self.PREFIX+'-storage':self.item(512,.5)}
        self.historical={'sbarbase-restore-old-db':self.item(1024,1)}
        self.running=set(self.source)|{self.PREFIX+'-db'}
        self.calls=[]

    def docker(self,*args,**kwargs):
        import json
        self.calls.append(args)
        out=lambda text:type('R',(),{'returncode':0,'stdout':text,'stderr':''})()
        if args[0]=='ps':
            owner=next(value.removeprefix('label=io.sbarbase.owner=') for value in args if value.startswith('label=io.sbarbase.owner='))
            pool=self.source if owner=='durable-upstream' else {**self.target,**self.historical}
            names=[name for name in pool if '-a' in args or name in self.running]
            return out('\n'.join(names)+'\n')
        if args[0]=='inspect':
            everything={**self.source,**self.target,**self.historical}
            return out(json.dumps([everything[args[1]]]))
        if args[0]=='stats':
            return out(''.join('100MiB / 1GiB\n' for _ in args[4:]))
        raise AssertionError('unexpected docker call '+repr(args))

    def test_the_target_listing_counts_the_current_generation_only(self):
        import resource_policy
        items=resource_policy.recovery_target_items(self.state,self.docker)
        self.assertEqual(len(items),4)
        self.assertEqual(resource_policy.retained_limits(items),(2048,2.0))

    def test_no_recorded_target_makes_no_docker_call(self):
        import resource_policy
        def refuse(*args,**kwargs):raise AssertionError('docker called without a recorded target')
        (self.state/'recovery-target.json').unlink()
        self.assertEqual(resource_policy.recovery_target_items(self.state,refuse),[])
        self.assertEqual(resource_policy.restart_placement((1792,1.75),[]),(1792,1.75))

    def test_the_restart_check_and_the_preflight_state_the_same_figure(self):
        import durable_runtime
        import resource_policy
        with patch.object(durable_runtime,'STATE',self.state),patch.object(durable_runtime.lab,'docker',self.docker):
            runtime_figure=durable_runtime.restart_placement(2)
        with patch.object(install_server,'STATE',self.state),patch.object(install_server,'docker',self.docker):
            memory,cpus,origin=install_server.planned_placement()
        self.assertEqual(runtime_figure,(memory,cpus))
        # The source rows alone, which is what the restart check counted before.
        self.assertEqual(resource_policy.start_placement(2),(2816,2.75))
        self.assertEqual(runtime_figure,(2816+2048,4.75))
        self.assertIn('plus 4 recovery target containers',origin)

    def test_a_fresh_source_beside_a_target_counts_both(self):
        # The preflight used to count only the target when no source container was retained.
        with patch.object(install_server,'STATE',self.state),patch.object(install_server,'docker',self.docker):
            memory,cpus,origin=install_server.planned_placement(inspect=lambda:[])
        self.assertEqual((memory,cpus),(1792+2048,3.75))
        self.assertIn('fresh placement plus 4',origin)

    def test_the_new_environment_check_refuses_what_only_fits_without_the_target(self):
        import durable_runtime
        import resource_policy
        with patch.object(durable_runtime,'STATE',self.state),patch.object(durable_runtime.lab,'docker',self.docker):
            placement,cpus=durable_runtime.restart_placement(2)
        source,source_cpus=resource_policy.start_placement(2)
        available=(source+resource_policy.START_RESERVE_MIB+500)*self.MIB
        self.assertTrue(resource_policy.restart_fits(source,source_cpus,available,0,8))
        self.assertFalse(resource_policy.restart_fits(placement,cpus,available,0,8))

    def test_the_running_target_is_counted_in_use_so_it_is_not_counted_twice(self):
        import durable_runtime
        with patch.object(durable_runtime,'STATE',self.state),patch.object(durable_runtime.lab,'docker',self.docker):
            in_use=durable_runtime.owned_usage_bytes()
        # Seven running source containers and the one running target container.
        self.assertEqual(in_use,8*100*self.MIB)
        stats=[call for call in self.calls if call[0]=='stats'][0]
        self.assertIn(self.PREFIX+'-db',stats)
        self.assertNotIn('sbarbase-restore-old-db',stats)

    def test_an_unbounded_target_container_is_refused_by_the_runtime_and_widened_by_the_preflight(self):
        import durable_runtime
        import resource_policy
        self.target[self.PREFIX+'-storage']={'HostConfig':{'Memory':0,'NanoCpus':0}}
        with patch.object(durable_runtime,'STATE',self.state),patch.object(durable_runtime.lab,'docker',self.docker):
            with self.assertRaises(resource_policy.ResourcePolicyError):durable_runtime.restart_placement(2)
        with patch.object(install_server,'STATE',self.state),patch.object(install_server,'docker',self.docker):
            memory,cpus,_=install_server.planned_placement()
        self.assertEqual((memory,cpus),(install_server.PLANNED_MIB,install_server.PLANNED_CPUS))

    def test_every_restart_check_counts_the_recovery_target(self):
        source=(Path(install_server.__file__).parent/'durable_runtime.py').read_text()
        checks=[line for line in source.splitlines() if 'placement, cpus = ' in line]
        self.assertEqual(len(checks),3)
        for line in checks:self.assertIn('restart_placement(',line)
        self.assertNotIn('placement, cpus = resource_policy.start_placement',source)
