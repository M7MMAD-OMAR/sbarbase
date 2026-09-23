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
        memory,cpus,origin=install_server.planned_placement(inspect=lambda:[])
        self.assertEqual((memory,cpus),(1792,1.75))
        self.assertEqual(origin,'fresh placement')

    def test_retained_containers_are_counted_at_their_own_limits(self):
        # The fresh system rows plus two environments of Auth and REST each.
        items=[self.item(1024,1),self.item(512,.5),self.item(256,.25)]+[self.item(256,.25) for _ in range(4)]
        memory,cpus,origin=install_server.planned_placement(inspect=lambda:items)
        self.assertEqual((memory,cpus),(1792+4*256,2.75))
        self.assertIn('7 containers',origin)

    def test_a_container_without_a_finite_limit_falls_back_to_the_full_placement(self):
        items=[self.item(1024,1),{'HostConfig':{'Memory':0,'NanoCpus':0}}]
        memory,cpus,_=install_server.planned_placement(inspect=lambda:items)
        self.assertEqual((memory,cpus),(install_server.PLANNED_MIB,install_server.PLANNED_CPUS))

    def test_the_stated_composition_names_where_the_figure_came_from(self):
        needed,composition=install_server.headroom_requirement(False,None,1792,'fresh placement')
        self.assertEqual(needed,1792+install_server.RESERVE_MIB)
        self.assertEqual(composition,'1792 MiB fresh placement + 2560 MiB reserve')

    def test_limits_are_parsed_the_way_the_tier_table_writes_them(self):
        self.assertEqual(install_server.mib('1024m'),1024)
        self.assertEqual(install_server.mib('2g'),2048)
