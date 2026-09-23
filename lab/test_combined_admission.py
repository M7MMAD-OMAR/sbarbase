import unittest
from combined_admission import refusal,MIB


class CombinedAdmissionTests(unittest.TestCase):
    def test_retained_split_placement_fits_with_explicit_host_reserve(self):
        self.assertIsNone(refusal(5888*MIB,5.75,9*1024*MIB,24))

    def test_memory_shortage_and_excessive_limits_refuse(self):
        self.assertEqual(refusal(5888*MIB,5.75,7*1024*MIB,24),'host_memory_headroom')
        self.assertEqual(refusal(7*1024*MIB,5,16*1024*MIB,24),'installation_ceiling')
        self.assertEqual(refusal(5888*MIB,7,16*1024*MIB,24),'installation_ceiling')

    def test_cpu_reserve_and_unbounded_inputs_refuse(self):
        # 5.75 CPUs of ceilings need (cores - 1) * 2 >= 5.75, so four cores.
        self.assertEqual(refusal(5888*MIB,5.75,16*1024*MIB,3),'host_cpu_headroom')
        self.assertIsNone(refusal(5888*MIB,5.75,16*1024*MIB,4))
        self.assertEqual(refusal(512*MIB,.5,16*1024*MIB,1),'host_cpu_headroom')
        self.assertEqual(refusal(0,1,16*1024*MIB,24),'unbounded_limits')

    def test_cores_needed_matches_the_refusal_boundary(self):
        from combined_admission import cores_needed,cpu_headroom_refused
        for cpus in (.25,1,3.75,5.75,6):
            needed=cores_needed(cpus)
            self.assertFalse(cpu_headroom_refused(cpus,needed))
            self.assertTrue(cpu_headroom_refused(cpus,needed-1))

    def test_nonfinite_capacity_is_not_admitted(self):
        self.assertEqual(refusal(5888*MIB,float('nan'),16*1024*MIB,24),'measurement_unavailable')

    def test_source_shutdown_still_runs_when_target_metadata_invalid(self):
        from unittest.mock import patch
        import installation_runtime
        with patch.object(installation_runtime.runtime.STATE.__class__,'exists',return_value=True), patch.object(installation_runtime,'TargetRuntime',side_effect=RuntimeError('invalid target')), patch.object(installation_runtime.runtime,'stop') as stop:
            with self.assertRaises(RuntimeError):installation_runtime.main('stop')
            stop.assert_called_once()
