"""Shared public startup identity/projection primitives, without runtime imports."""
import re


class StartupRefusal(RuntimeError):
    """A fixed public code without Docker or private OS exception content."""


def require(value, reason):
    if not value:
        raise StartupRefusal(reason)


def identifier(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None



CONTAINER_PROJECTION = ('{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},'
    '"State":{"Status":{{json .State.Status}},"Running":{{json .State.Running}},'
    '"OOMKilled":{{json .State.OOMKilled}},"ExitCode":{{json .State.ExitCode}}},'
    '"User":{{json .Config.User}},"Entrypoint":{{json .Config.Entrypoint}},"Cmd":{{json .Config.Cmd}},'
    '"Healthcheck":{{json (index .Config "Healthcheck")}},"Labels":{{json .Config.Labels}},'
    '"Mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{"Type":{{json $m.Type}},'
    '"Name":{{json (index $m "Name")}},"Destination":{{json $m.Destination}},"RW":{{json $m.RW}},'
    '"Propagation":{{json (index $m "Propagation")}}}{{end}}],'
    '"HostConfig":{"ReadonlyRootfs":{{json .HostConfig.ReadonlyRootfs}},"NetworkMode":{{json .HostConfig.NetworkMode}},'
    '"PidMode":{{json .HostConfig.PidMode}},"IpcMode":{{json .HostConfig.IpcMode}},'
    '"Privileged":{{json .HostConfig.Privileged}},"CapAdd":{{json .HostConfig.CapAdd}},'
    '"CapDrop":{{json .HostConfig.CapDrop}},"SecurityOpt":{{json .HostConfig.SecurityOpt}},'
    '"Memory":{{json .HostConfig.Memory}},"MemorySwap":{{json .HostConfig.MemorySwap}},'
    '"NanoCpus":{{json .HostConfig.NanoCpus}},"PidsLimit":{{json .HostConfig.PidsLimit}},'
    '"ShmSize":{{json .HostConfig.ShmSize}},"RestartPolicy":{{json .HostConfig.RestartPolicy}},'
    '"PortBindings":{{json .HostConfig.PortBindings}},"Binds":{{json .HostConfig.Binds}},'
    '"Devices":{{json .HostConfig.Devices}},"LogConfig":{{json .HostConfig.LogConfig}}},'
    '"Networks":[{{$first := true}}{{range $i,$n := .NetworkSettings.Networks}}{{if not $first}},{{end}}{{json $i}}{{$first = false}}{{end}}]}')
