"""Focused source callers, all daemon and filesystem effects use owned doubles."""
import unittest

MODULES = ('test_docker_profile', 'test_container_start', 'test_install_server',
           'test_install_lock', 'test_supervisor_unit', 'test_supervised_run',
           'test_resource_policy', 'test_resource_admission', 'test_runtime_build_inputs',
           'test_image_identity', 'test_durable_image_identity', 'test_runtime_reuse',
           'test_runtime_resume', 'test_supervisor')

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
