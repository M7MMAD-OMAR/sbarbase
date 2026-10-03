"""Identity and configuration proof for the shared file Storage writer barrier.

Native admission and stopping the returned exact CID belong to the orchestrator.
This component refuses a foreign, replaced, running or differently configured
Storage writer before object tree replacement. It cannot constrain host root or
an arbitrary external writer, which are outside the classified deployment scope.
"""
import re


class BarrierError(RuntimeError):
    pass


def admit_storage(record, *, name, owner, image_id, volume, tenant_parent,
                  container_id=None, stopped=False):
    if not isinstance(record, dict):
        raise BarrierError('Storage writer inspection is unverifiable')
    observed_id = record.get('Id')
    if not isinstance(observed_id, str) or not re.fullmatch(r'[a-f0-9]{64}', observed_id) \
            or record.get('Name') != '/' + name or record.get('Image') != image_id \
            or container_id is not None and observed_id != container_id:
        raise BarrierError('Storage writer native identity differs')
    config = record.get('Config')
    state = record.get('State')
    if not isinstance(config, dict) or not isinstance(config.get('Labels'), dict) \
            or config['Labels'].get('io.sbarbase.owner') != owner \
            or not isinstance(state, dict) or type(state.get('Running')) is not bool \
            or state.get('Restarting') is not False or state.get('Paused') is not False:
        raise BarrierError('Storage writer ownership or state is unverifiable')
    if stopped and state['Running']:
        raise BarrierError('Storage writer is not stopped')
    raw_env = config.get('Env')
    if not isinstance(raw_env, list) or any(not isinstance(item, str) or '=' not in item for item in raw_env):
        raise BarrierError('Storage writer configuration is unverifiable')
    env = {}
    for item in raw_env:
        key, value = item.split('=', 1)
        if key in env:
            raise BarrierError('Storage writer configuration is ambiguous')
        env[key] = value
    expected = {'STORAGE_BACKEND': 'file', 'GLOBAL_S3_BUCKET': tenant_parent,
                'FILE_STORAGE_BACKEND_PATH': '/tmp/storage-data', 'MULTI_TENANT': 'true',
                'S3_PROTOCOL_ENABLED': 'false', 'PG_QUEUE_ENABLE': 'false'}
    if any(env.get(key) != value for key, value in expected.items()):
        raise BarrierError('Storage object writer contract is unsupported')
    mounts = record.get('Mounts')
    if not isinstance(mounts, list):
        raise BarrierError('Storage object mount is unverifiable')
    target = [mount for mount in mounts if isinstance(mount, dict) and mount.get('Destination') == '/tmp/storage-data']
    if len(target) != 1 or target[0].get('Type') != 'volume' or target[0].get('Name') != volume \
            or target[0].get('RW') is not True:
        raise BarrierError('Storage object volume identity differs')
    return observed_id
