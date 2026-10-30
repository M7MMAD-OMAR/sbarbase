"""Host firewall rules that keep Edge Functions off the host and off private networks.

A function can open a connection to the Docker bridge gateway address (the host) and to any
address the host can route to. The worker's `deny_net` takes no CIDR range and a host name that
resolves to a denied address passes it (docs/engineering/plans/2026-10-30-edge-functions-workspace.md,
sandbox probe), so the boundary has to sit on the host network. This tool GENERATES those rules
from subnets. It never changes a firewall: `apply` only writes a file to the path given with
`--output` and prints the command an operator runs to activate it.

    functions_egress.py [plan] [options]        print the rules (default command)
    functions_egress.py verify-plan [options]   check the generated plan; needs no root
    functions_egress.py apply --output PATH     write the rules to PATH, print how to activate

Subnets are read with `docker network inspect`: the outbound network `sbarbase-durable-outbound`
by name and every environment functions network by its `io.sbarbase.functions` label. Pass
`--outbound-subnet CIDR` and `--function-network SUBNET=GATEWAY` (both repeatable) to name them
instead, or `--no-discover` to leave Docker out entirely. Rules match by subnet address, never by
interface name, because bridge names differ per network and per host.

What the rules do, for traffic whose source is the outbound subnet:

  - INPUT (addressed to the host itself): drop everything not on the host allow list.
  - FORWARD: drop traffic to link local (169.254.0.0/16, fe80::/10), loopback, RFC1918, CGNAT
    (100.64.0.0/10), IPv6 unique local (fc00::/7) and the subnet of every other Docker bridge
    network (read from Docker, for daemons whose address pools are not private ranges), unless
    the allow list names it.
  - Replies (established, related) pass.

For each environment functions network: in INPUT accept only TCP to the Storage listener port
(`--storage-port`, default durable_runtime.FUNCTIONS_STORAGE_PORT) at that network's own gateway
address, and drop everything else addressed to the host. In FORWARD the same drops apply with the
functions subnet as source, because a function can bind() its address on that network and still
send out through the outbound one; traffic inside its own subnet (function to its Auth and REST,
which br_netfilter shows to FORWARD) is accepted first.

Forged sources. Every drop above keys on the source address, so a packet whose source lies outside
every listed subnet matches none of them. An unprivileged process can do that for IPv6 (bind an
address with IPV6_FREEBIND), and a function that binds its functions-network address and sends out
through the outbound bridge does it for IPv4 too. A reverse-path check on the bridge interfaces
(`fib saddr . iif oif missing drop`, a prerouting chain, so it covers INPUT and FORWARD) drops a
packet whose source is not routed back through the bridge it arrived on, and the unspecified
sources :: and 0.0.0.0/8 are dropped outright (a raw IPv6 datagram from :: would otherwise reach a
host socket). It uses the same interface names and fallback as the link-local rule below. For
plain IPv4 forgery the container also needs CAP_NET_RAW or CAP_NET_ADMIN (IP_TRANSPARENT), which the
container hardening (cap-drop ALL, checkpoint `container`) removes; the checks here do not depend
on that. Neither translation (ufw, firewalld) carries them.

Link-local IPv6 (fe80::/10). A container reaches the host's bridge address from its own fe80::
address, which no subnet rule can match, and this holds on IPv4-only networks too. The one place
these rules match an interface name is therefore the INPUT drop of fe80::/10 sources arriving on
the outbound and functions bridges. Names are read from Docker, or given as `CIDR=INTERFACE`
(`--outbound-subnet`) and `SUBNET=GATEWAY@INTERFACE` (`--function-network`); `--bridge-interface
NAME` adds a further bridge. Whenever ANY network has no known name, every `br-*` bridge is
matched as well and the header and stderr say so (no option turns this off). Bridges with a custom
non br-* name that no one named stay uncovered; see the header. Neighbour
discovery (hop limit 255) is accepted first. Linux does not forward a link-local source off its
link (RFC 4291 section 2.5.6), so FORWARD has no such rule; that is reasoned, not run.

IPv6 INPUT: neighbour solicitation and advertisement (hop limit 255) from a subnet are accepted
before the drop, because conntrack does not track them and a container could not otherwise find
its gateway. Not run on a live host.

Name resolution. Docker's embedded resolver forwards to the host's upstream resolvers, often a
private address such as 192.168.1.1, so a blanket private-range drop would end all name
resolution. The allow list is placed before every drop. It holds the non-loopback nameservers of
/etc/resolv.conf (port 53, udp and tcp; when /etc/resolv.conf names only a local stub such as
127.0.0.53 and /run/systemd/resolve/resolv.conf exists, that file too, as Docker does) plus every
`--allow CIDR[:port/proto]`, repeatable, for example `--allow 192.168.1.10:587/tcp` for a mail
relay on a private address: environment Auth joins the outbound network for SMTP, so the relay
case uses the same mechanism. An `--allow` entry is emitted in FORWARD only, and is refused when
it overlaps a gateway address or a network subnet the tool knows. A relay or resolver that runs on
the host itself needs `--allow-host CIDR:port/proto` (a port is required), which also opens it in
INPUT and may name those addresses. The automatic resolvers are host allows, non-loopback only.
`--no-resolv-conf` drops the automatic resolvers; `--resolv-conf PATH` (repeatable) reads other
files. `--deny CIDR` adds a range to the FORWARD drops, for an internal range that is not private
by address.

Formats (`--format`). `nft` is the primary one: its own table `inet sbarbase_functions`, base
chains at priority -10. Docker's iptables rules and ufw's chains sit at priority 0 (firewalld
at 10), so ours run first. A drop is final across every base chain whatever Docker later does;
an accept here only ends our own chain, so these rules can narrow what Docker allows and never
widen it. `ufw` and `firewalld` are TRANSLATIONS of the same rule list into shell scripts of
`ufw` and `firewall-cmd` commands; the nft output is the one the checks and tests exercise.

Not run here: applying the rules on a server. The live proof is a separate step that needs
the owner's approval.
"""
import argparse
import fnmatch
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

try:
    import durable_runtime
except ValueError as _exc:
    # durable_runtime reads this variable as an integer at import; its message would be a traceback here.
    if 'SBARBASE_FUNCTIONS_STORAGE_PORT' in os.environ:
        print(f'error: SBARBASE_FUNCTIONS_STORAGE_PORT={os.environ["SBARBASE_FUNCTIONS_STORAGE_PORT"]!r} is not an integer port', file=sys.stderr)
        raise SystemExit(2)
    raise

TABLE = 'sbarbase_functions'
# Docker's iptables chains and ufw's sit at filter priority (0), firewalld's at filter + 10.
PRIORITY = -10
# The label lab/durable_runtime.py functions_start puts on each environment functions network.
# durable_runtime has no constant for it; test_functions_egress.py fails if the literal there changes.
FUNCTIONS_LABEL = 'io.sbarbase.functions'
STORAGE_PORT = durable_runtime.FUNCTIONS_STORAGE_PORT
OUTBOUND_NETWORK = durable_runtime.EGRESS
DNS_PORT = 53
SYSTEMD_RESOLV = '/run/systemd/resolve/resolv.conf'

# Ranges a function must not reach through FORWARD. Loopback is here for completeness: the kernel
# does not route it, so the rule only documents the intent.
DENY4 = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16', '127.0.0.0/8')
DENY6 = ('fc00::/7', 'fe80::/10', '::1/128')
MIN_PREFIX = {4: 8, 6: 16}
# Ranges an --allow entry may not touch without --allow-special: cloud metadata and link local, loopback,
# CGNAT (some providers put metadata there), "this network", and their IPv6 counterparts. The automatic
# resolvers are exempt: they are port 53 only.
SPECIAL = ('169.254.0.0/16', '127.0.0.0/8', '100.64.0.0/10', '0.0.0.0/8', 'fe80::/10', '::1/128')
LINK_LOCAL = ipaddress.ip_network('fe80::/10')
# Used when no bridge interface name is known: Docker names user-defined bridges br-<id>.
WILDCARD_INTERFACE = 'br-*'
INTERFACE_NAME = re.compile(r'[A-Za-z0-9_.@:+-]{1,14}\*?|[A-Za-z0-9_.@:+-]{1,15}')

NOT_COVERED = (
    'It does NOT cover:',
    '  - Public destinations. A function still reaches any public address, including third party services,',
    '    and nothing here limits rate, volume or ports to them. Other hosts in the same provider network',
    '    that use public addresses, and internal ranges that are not private by address, need --deny.',
    '  - Name lookups to the allowed resolvers (DNS tunnelling) or to anything on the allow list.',
    '  - Containers on other networks, containers on the host network, or processes on the host itself.',
    '  - A network created after this file was generated. Regenerate and reload whenever Functions are',
    '    turned on or off for an environment, or the outbound network is recreated.',
    '  - Connections already established when the rules load; they continue until they close.',
    '  - Forged sources beyond the bridges named above: the reverse-path check covers packets arriving on those',
    '    bridges only. Forging an IPv4 source also needs CAP_NET_RAW or CAP_NET_ADMIN (IP_TRANSPARENT), which',
    '    container hardening (cap-drop ALL) removes; an IPv6 source needs no capability, so the check is what stops it.',
    '  - Bridges with a custom, non br-* name that were not named: they match neither the link-local drop nor the',
    '    reverse-path check. Name them (CIDR=INTERFACE, SUBNET=GATEWAY@INTERFACE or --bridge-interface).',
    '  - Host processes that connect to a bridge gateway address: the kernel uses that address as the source, and',
    '    it lies inside the dropped subnet, so such a connection is dropped in INPUT. Use another host address.',
    '  - Link-local sources forwarded to other hosts: the kernel does not route them off their link (not verified).',
    '  - Addresses of the host that the tool cannot see (a LAN address of the host is only known if given',
    '    to --allow-host or found as a Docker gateway).',
    '  - Opening the host firewall. These rules only narrow what is allowed. A default-deny INPUT policy',
    '    (ufw) still needs its own allow for the Storage listener (docs/guides/server-deployment.md).',
    '  - The worker sandbox (file access, CPU, memory); that is configured in the runtime.',
    '  - IPv6 global or unique-local sources outside the given or discovered subnets. Link-local (fe80::/10)',
    '    sources to the host are dropped, matched by interface name because no subnet can match them.',
    'Generated rules are not proof. A live probe on the target host is the proof.',
)


class Allow(NamedTuple):
    net: object
    proto: str = ''
    port: int = 0
    # Also opened in INPUT, so the destination may be an address of the host itself.
    host: bool = False


class FunctionNet(NamedTuple):
    subnet: object
    gateway: object = None
    name: str = ''
    iface: str = ''


class Rule(NamedTuple):
    chain: str
    family: int
    action: str
    src: object
    dst: tuple = ()
    proto: str = ''
    port: int = 0
    state: bool = False
    note: str = ''
    # Interface names; only the link-local rules use them (nothing else can match those sources).
    iif: tuple = ()


class Plan(NamedTuple):
    outbound: tuple
    functions: tuple
    allow: tuple
    deny: tuple
    resolvers: tuple
    rules: tuple
    port: int = 0
    interfaces: tuple = ()


class PlanError(ValueError):
    pass


# ---------------------------------------------------------------- parsing

def reject_scope(text, what):
    """Python accepts an IPv6 scope id (fe80::1%eth0); nftables does not, so it never reaches a rule."""
    if isinstance(text, str) and '%' in text:
        raise PlanError(f'{what}: {text.strip()!r} has an IPv6 scope id (%); write the address without it, the rules match by subnet and not by interface')


def parse_network(text, what):
    """A strict network. Empty, malformed, host-bits-set, zero-length and scoped (%) prefixes are refused."""
    if not isinstance(text, str) or not text.strip():
        raise PlanError(f'{what}: empty value')
    reject_scope(text, what)
    try:
        net = ipaddress.ip_network(text.strip(), strict=True)
    except ValueError as err:
        raise PlanError(f'{what}: {text.strip()!r} is not a network address ({err})')
    if net.prefixlen == 0:
        raise PlanError(f'{what}: {text.strip()!r} matches every address')
    return net


def parse_subnet(text, what='subnet'):
    net = parse_network(text, what)
    if net.prefixlen < MIN_PREFIX[net.version]:
        raise PlanError(f'{what}: {text.strip()!r} is wider than a bridge network can be (/{MIN_PREFIX[net.version]} or narrower)')
    return net


def parse_gateway(text, subnet, what='gateway'):
    reject_scope(text, what)
    try:
        address = ipaddress.ip_address((text or '').strip())
    except ValueError:
        raise PlanError(f'{what}: {text!r} is not an address')
    if address not in subnet:
        raise PlanError(f'{what}: {address} is outside {subnet}')
    if address == subnet.network_address and subnet.prefixlen < subnet.max_prefixlen - 1:
        raise PlanError(f'{what}: {address} is the network address of {subnet}, not a host')
    if subnet.version == 4 and subnet.prefixlen < 31 and address == subnet.broadcast_address:
        raise PlanError(f'{what}: {address} is the broadcast address of {subnet}, not a host')
    return address


def parse_function_network(text):
    """SUBNET=GATEWAY[@INTERFACE]. An IPv4 network needs its gateway (the Storage listener's address); IPv6 may
    omit it. The bridge interface is optional; without it the br-* wildcard is matched as well."""
    subnet_text, _, rest = (text or '').partition('=')
    gateway_text, _, iface = rest.partition('@')
    subnet = parse_subnet(subnet_text, '--function-network')
    if subnet.version == 4 and not gateway_text.strip():
        raise PlanError(f'--function-network: {text!r} needs SUBNET=GATEWAY')
    gateway = parse_gateway(gateway_text, subnet, '--function-network') if gateway_text.strip() else None
    return FunctionNet(subnet, gateway, '', check_interface(iface, '--function-network') if '@' in rest else '')


def check_interface(name, what):
    if not INTERFACE_NAME.fullmatch(name or ''):
        raise PlanError(f'{what}: {name!r} is not an interface name')
    return name


def parse_outbound(text):
    """CIDR[=INTERFACE]: (subnet, interface or '')."""
    subnet_text, sep, iface = (text or '').partition('=')
    return parse_subnet(subnet_text, '--outbound-subnet'), (check_interface(iface, '--outbound-subnet') if sep else '')


PORT_PROTO = re.compile(r'(\d{1,5})/(tcp|udp)')


def parse_allow(text, host=False):
    """CIDR[:port/proto]. An IPv6 range with a port is written [fd00::/64]:53/udp or as an address
    with no prefix, fd00::1:53/udp. With host=True (--allow-host) a port is required."""
    flag = '--allow-host' if host else '--allow'
    reject_scope(text, flag)
    value = (text or '').strip()
    if not value:
        raise PlanError(f'{flag}: empty value')
    bracket = re.fullmatch(r'\[(.+)\]:(.+)', value)
    if bracket:
        head, tail = bracket.groups()
    else:
        try:
            ipaddress.ip_network(value, strict=True)
            head, tail = value, ''
        except ValueError:
            head, _, tail = value.rpartition(':')
            if not head or re.fullmatch(r'\d+/\d+', tail):
                # No port part at all: report what is wrong with the network itself (host bits set, bad prefix).
                parse_network(value, flag)
    net = parse_network(head, flag)
    if not tail:
        if host:
            raise PlanError(f'{flag}: {text!r} needs a port, write CIDR:port/tcp or CIDR:port/udp')
        return Allow(net)
    match = PORT_PROTO.fullmatch(tail)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise PlanError(f'{flag}: {text!r} has a malformed port, write CIDR:port/tcp or CIDR:port/udp')
    return Allow(net, match.group(2), int(match.group(1)), host)


def parse_deny(text):
    return parse_network(text, '--deny')


def parse_resolv(text):
    """Nameservers that are neither loopback nor unspecified, in order, without duplicates."""
    found = []
    for line in text.splitlines():
        parts = re.split(r'[#;]', line, maxsplit=1)[0].split()
        if len(parts) < 2 or parts[0] != 'nameserver':
            continue
        try:
            address = ipaddress.ip_address(parts[1].split('%', 1)[0])
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified or address in found:
            continue
        found.append(address)
    return found


def read_resolvers(paths):
    """Resolvers from these files; None means the default: /etc/resolv.conf, then systemd's file when
    the first names only a local stub."""
    def one(path):
        try:
            return Path(path).read_text(errors='replace')
        except OSError:
            return None
    if paths is not None:
        return [a for path in paths for a in parse_resolv(one(path) or '')]
    text = one('/etc/resolv.conf')
    found = parse_resolv(text or '')
    if not found and text is not None and re.search(r'^\s*nameserver\s+(127\.|::1\b)', text, re.M):
        found = parse_resolv(one(SYSTEMD_RESOLV) or '')
    return found


# ---------------------------------------------------------------- discovery

class DiscoveryError(PlanError):
    pass


def docker(*args):
    try:
        result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise DiscoveryError(f'docker is unavailable ({err}); pass --outbound-subnet and --function-network, or --no-discover')
    if result.returncode:
        raise DiscoveryError(f'docker {" ".join(args[:2])} failed: {result.stderr.strip() or result.returncode}')
    return result.stdout


def docker_json(*args):
    """The JSON list `docker network inspect` prints; anything else is a discovery error."""
    output = docker(*args)
    try:
        items = json.loads(output or '[]')
    except ValueError as err:
        raise DiscoveryError(f'docker {" ".join(args[:2])} printed something that is not JSON ({err})')
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise DiscoveryError(f'docker {" ".join(args[:2])} printed an unexpected shape')
    return items


def bridge_interface(item):
    """The Linux bridge device of a `docker network inspect` entry: its configured name, else br-<id>."""
    name = (item.get('Options') or {}).get('com.docker.network.bridge.name') or ''
    ident = item.get('Id') or ''
    name = name or (f'br-{ident[:12]}' if len(ident) >= 12 else '')
    return name if INTERFACE_NAME.fullmatch(name) else ''


def ipam(item):
    """(subnet, gateway or None) pairs of a `docker network inspect` entry."""
    out = []
    for entry in (item.get('IPAM') or {}).get('Config') or []:
        if isinstance(entry, dict) and entry.get('Subnet'):
            out.append((entry['Subnet'], entry.get('Gateway') or None))
    return out


def discover_outbound():
    """(subnet, gateway or None, bridge interface or '') triples of the outbound network."""
    items = docker_json('network', 'inspect', OUTBOUND_NETWORK)
    if not items:
        raise DiscoveryError(f'network {OUTBOUND_NETWORK} does not exist; start the runtime first or pass --outbound-subnet')
    found = []
    for subnet_text, gateway_text in ipam(items[0]):
        subnet = parse_subnet(subnet_text, f'{OUTBOUND_NETWORK} subnet')
        found.append((subnet, parse_gateway(gateway_text, subnet, f'{OUTBOUND_NETWORK} gateway') if gateway_text else None, bridge_interface(items[0])))
    if not found:
        raise DiscoveryError(f'network {OUTBOUND_NETWORK} has no subnet')
    return found


def discover_functions():
    ids = docker('network', 'ls', '--quiet', '--no-trunc', '--filter', f'label={FUNCTIONS_LABEL}').split()
    if not ids:
        return []
    out = []
    for item in docker_json('network', 'inspect', *ids):
        name = item.get('Name') or ''
        for subnet_text, gateway_text in ipam(item):
            subnet = parse_subnet(subnet_text, f'{name} subnet')
            gateway = parse_gateway(gateway_text, subnet, f'{name} gateway') if gateway_text else None
            if subnet.version == 4 and gateway is None:
                raise DiscoveryError(f'network {name} has no IPv4 gateway')
            out.append(FunctionNet(subnet, gateway, name, bridge_interface(item)))
    return out


def discover_bridges():
    """(subnets, gateway addresses) of every Docker bridge network. A daemon whose address pools are not
    private ranges would otherwise leave its containers outside the private-range drops."""
    ids = docker('network', 'ls', '--quiet', '--no-trunc', '--filter', 'driver=bridge').split()
    subnets, gateways = [], []
    for item in docker_json('network', 'inspect', *ids) if ids else []:
        for subnet_text, gateway_text in ipam(item):
            try:
                subnets.append(ipaddress.ip_network(subnet_text, strict=False))
                if gateway_text:
                    gateways.append(ipaddress.ip_address(gateway_text))
            except ValueError:
                continue
    return subnets, gateways


# ---------------------------------------------------------------- planning

def address_key(address):
    """Orders addresses of both families (sorted() alone raises TypeError on a mix)."""
    return (address.version, int(address))


def sort_key(net):
    return (net.version, int(net.network_address), net.prefixlen)


def make_plan(outbound, functions, allow=(), deny=(), resolvers=(), bridges=(), hosts=(), port=STORAGE_PORT, interfaces=(), allow_special=False, interfaces_complete=False):
    """The rule list. Per chain and family the order is: replies, the allow list, the Storage and
    neighbour discovery accepts, then the drops, so every drop comes after everything it must not shadow.

    bridges are the subnets of other Docker networks (extra destination drops, and addresses an --allow
    entry may not cover); hosts are gateway addresses of the host the tool knows about; interfaces are
    the bridge device names of the outbound and functions networks (link-local sources can only be
    matched that way); allow_special lifts the refusal of --allow entries that touch SPECIAL.
    interfaces_complete (set by collect only when every network has a known bridge) leaves the br-* wildcard out;
    otherwise it is matched in addition, so a bridge no one named is never left out."""
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise PlanError(f'--storage-port: {port!r} is not a port')
    outbound = sorted(set(outbound), key=sort_key)
    functions = sorted({f.subnet: f for f in functions}.values(), key=lambda f: sort_key(f.subnet))
    if not outbound:
        raise PlanError('no outbound subnet: nothing to protect the host from')
    everything = outbound + [f.subnet for f in functions]
    for i, a in enumerate(everything):
        for b in everything[i+1:]:
            if a.version == b.version and a.overlaps(b):
                raise PlanError(f'subnets overlap: {a} and {b}')
    known_hosts = {ipaddress.ip_address(h) for h in hosts} | {f.gateway for f in functions if f.gateway}
    known_nets = set(everything) | set(bridges)
    names = sorted({n for n in interfaces if n})
    for n in names:
        if not INTERFACE_NAME.fullmatch(n):
            raise PlanError(f'--bridge-interface: {n!r} is not an interface name')
    if not interfaces_complete or not names:
        # Br-* subsumes the exact br-<id> names; others (a custom bridge name) stay listed beside it.
        names = [WILDCARD_INTERFACE] + [n for n in names if not fnmatch.fnmatchcase(n, WILDCARD_INTERFACE)]
    names = tuple(sorted(names))
    special = [ipaddress.ip_network(t) for t in SPECIAL]
    merged = {}
    for a in allow:
        touched = next((str(n) for n in special if a.net.version == n.version and a.net.overlaps(n)), None)
        if touched and not allow_special:
            raise PlanError(f'{"--allow-host" if a.host else "--allow"} {describe(a)} touches {touched}, a metadata, link local, loopback or CGNAT range the drops exist for; '
                            'pass --allow-special to allow it anyway')
        if a.host and not a.port:
            raise PlanError(f'--allow-host {describe(a)} needs a port')
        if not a.host:
            clash = next((str(h) for h in sorted(known_hosts, key=address_key) if h in a.net), None) or next((str(n) for n in sorted(known_nets, key=sort_key) if a.net.version == n.version and a.net.overlaps(n)), None)
            if clash:
                raise PlanError(f'--allow {describe(a)} covers {clash}, an address or network of this host; '
                                'use --allow-host CIDR:port/proto if the host itself must be reachable')
        merged[(a.net, a.proto, a.port)] = merged.get((a.net, a.proto, a.port), False) or a.host
    for r in resolvers:
        for proto in ('udp', 'tcp'):
            merged[(ipaddress.ip_network(r), proto, DNS_PORT)] = True
    allows = tuple(Allow(net, proto, number, host) for (net, proto, number), host in sorted(merged.items(), key=lambda kv: (sort_key(kv[0][0]), kv[0][1], kv[0][2])))
    denies = {4: [], 6: []}
    # The outbound and functions subnets are destinations too, so one environment cannot reach another
    # (or the outbound network) when the pools are not private ranges and nothing was discovered.
    for net in [ipaddress.ip_network(t) for t in (*DENY4, *DENY6)] + list(bridges) + list(deny) + everything:
        denies[net.version].append(net)
    denies = {fam: tuple(sorted(ipaddress.collapse_addresses(nets), key=sort_key)) for fam, nets in denies.items()}
    rules = []
    for family in (4, 6):
        sources = [n for n in outbound if n.version == family]
        fns = [f for f in functions if f.subnet.version == family]
        if family == 4 and not sources and not fns:
            continue
        every = sources + [f.subnet for f in fns]
        # Link-local sources match no subnet; they are matched on the bridge interfaces, IPv6 only, and also
        # when every network here is IPv4 only (the bridge carries an fe80:: address regardless).
        link = [LINK_LOCAL] if family == 6 else []
        for net in every + link:
            rules.append(Rule('input', family, 'accept', net, state=True, note='replies', iif=names if net in link else ()))
        if family == 6:
            for net in every + link:
                rules.append(Rule('input', 6, 'accept', net, proto='icmpv6', note='neighbour discovery', iif=names if net in link else ()))
        for net in sources:
            for a in allows:
                if a.host and a.net.version == family:
                    rules.append(Rule('input', family, 'accept', net, (a.net,), a.proto, a.port, note='allow list'))
        for f in fns:
            # The Storage listener binds an IPv4 gateway only (durable_runtime.functions_address), so an IPv6
            # gateway gets no accept: it would admit any host process listening on [::]:port.
            if f.gateway is not None and family == 4:
                rules.append(Rule('input', family, 'accept', f.subnet, (ipaddress.ip_network(f.gateway),), 'tcp', port, note='functions to Storage listener'))
        for net in every + link:
            rules.append(Rule('input', family, 'drop', net, note='no other host access', iif=names if net in link else ()))
        if not every:
            continue
        # A function can bind() its address on the functions network and send out through the outbound one,
        # so a functions subnet is a FORWARD source too; its own subnet (br_netfilter) is accepted first.
        for net in every:
            rules.append(Rule('forward', family, 'accept', net, state=True, note='replies'))
        for f in fns:
            rules.append(Rule('forward', family, 'accept', f.subnet, (f.subnet,), note='own network'))
        for net in every:
            for a in allows:
                if a.net.version == family:
                    rules.append(Rule('forward', family, 'accept', net, (a.net,), a.proto, a.port, note='allow list'))
        for net in every:
            rules.append(Rule('forward', family, 'drop', net, denies[family], note='no private or link local ranges'))
    return Plan(tuple(outbound), tuple(functions), allows, denies[4] + denies[6], tuple(resolvers), tuple(rules), port, names)


def collect(args, err=sys.stderr):
    """Subnets, allow list and denies from the command line, with Docker for what was not named."""
    pairs = [(net, None, iface) for net, iface in map(parse_outbound, args.outbound_subnet)]
    functions = [parse_function_network(t) for t in args.function_network]
    bridges, hosts, asked = [], [], False
    if not args.no_discover:
        if not pairs:
            pairs, asked = discover_outbound(), True
        if not functions:
            functions, asked = discover_functions(), True
        try:
            bridges, hosts = discover_bridges()
        except DiscoveryError as exc:
            if asked:
                raise
            print(f'warning: other Docker networks were not read, so only the private ranges are dropped: {exc}', file=err)
    allow = [parse_allow(t) for t in args.allow] + [parse_allow(t, host=True) for t in args.allow_host]
    deny = [parse_deny(t) for t in args.deny]
    resolvers = [] if args.no_resolv_conf else read_resolvers(args.resolv_conf)
    interfaces = list(args.bridge_interface) + [i for _, _, i in pairs] + [f.iface for f in functions]
    # Complete only when every network has a name (read from Docker) or the operator says the flags name them all.
    unnamed = [str(n) for n, _, i in pairs if not i] + [str(f.subnet) for f in functions if not f.iface]
    complete = not unnamed
    if not complete:
        print(f'warning: no bridge interface name known for {", ".join(unnamed)}, so link-local IPv6 sources and the reverse-path check '
              f'also match every {WILDCARD_INTERFACE} bridge. Name them as CIDR=INTERFACE (--outbound-subnet) or SUBNET=GATEWAY@INTERFACE (--function-network), or let Docker supply them.', file=err)
    return make_plan([n for n, _, _ in pairs], functions, allow, deny, resolvers, bridges, hosts + [g for _, g, _ in pairs if g],
                     args.storage_port, interfaces, args.allow_special, complete)


# ---------------------------------------------------------------- rendering

def text(net):
    """A host address bare, a range with its prefix: one spelling in every format."""
    return str(net.network_address) if net.prefixlen == net.max_prefixlen else str(net)


def header(plan):
    lines = ['Generated by lab/functions_egress.py. A set of rules, not a proof: nothing here has been applied.',
             'Keeps Edge Functions off the host and off private networks, by subnet address only.', '',
             'Outbound network subnets: ' + ', '.join(text(n) for n in plan.outbound)]
    for f in plan.functions:
        lines.append(f'Functions network {f.name or "(named by flag)"}: {text(f.subnet)}' + (f', gateway {f.gateway}' if f.gateway else ', no gateway'))
    if not plan.functions:
        lines.append('Functions networks: none (no environment has Functions on)')
    lines.append(f'Storage listener port: {plan.port}')
    lines.append('Allowed before any drop (outbound source; host = also opened to the host itself): ' + (', '.join(describe(a) for a in plan.allow) or 'nothing'))
    if not plan.allow:
        lines.append('WARNING: the allow list is empty. Name resolution through a private resolver and any private mail relay will fail.')
    if not any(n.version == 6 for n in plan.outbound):
        lines.append('No IPv6 outbound subnet given or found: no IPv6 subnet source rules were generated.')
    if WILDCARD_INTERFACE in plan.interfaces:
        lines.append(f'WARNING: not every bridge interface name is known, so link-local IPv6 sources and the reverse-path check also match every {WILDCARD_INTERFACE} bridge. Name each bridge (or let Docker supply the names) to narrow it.')
    lines.append('Link-local IPv6 (fe80::/10) sources are dropped to the host on interface: ' + ', '.join(plan.interfaces) + ' (the one match by interface name; no subnet can match them).')
    lines += ['', *NOT_COVERED]
    return lines


def describe(a):
    return text(a.net) + (f':{a.port}/{a.proto}' if a.proto else '') + (' (host)' if a.host else '')


def nft_rule(rule):
    fam = 'ip' if rule.family == 4 else 'ip6'
    parts = []
    if rule.iif:
        parts.append('iifname ' + (f'"{rule.iif[0]}"' if len(rule.iif) == 1 else '{ ' + ', '.join(f'"{n}"' for n in rule.iif) + ' }'))
    parts.append(f'{fam} saddr {text(rule.src)}')
    if rule.dst:
        parts.append(f'{fam} daddr ' + (text(rule.dst[0]) if len(rule.dst) == 1 else '{ ' + ', '.join(text(n) for n in rule.dst) + ' }'))
    if rule.proto == 'icmpv6':
        parts.append('ip6 hoplimit 255 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert }')
    elif rule.proto:
        parts.append(f'{rule.proto} dport {rule.port}')
    if rule.state:
        parts.append('ct state established,related')
    if rule.action == 'drop':
        parts.append('counter')
    parts += [rule.action, f'comment "{rule.note}"']
    return ' '.join(parts)


def rpf_rules(plan):
    """Source checks on the bridge interfaces, in a prerouting chain (so INPUT and FORWARD both see them).

    The unspecified source :: and IPv4 0.0.0.0/8 are dropped outright; only duplicate address detection (neighbour
    solicitation from :: to a solicited-node multicast address, hop limit 255) is let through. Then a reverse-path
    check: a source address that is not routed back through the bridge the packet arrived on is dropped. Link-local
    sources (neighbour discovery) are left to their own rules."""
    iif = 'iifname ' + (f'"{plan.interfaces[0]}"' if len(plan.interfaces) == 1 else '{ ' + ', '.join(f'"{n}"' for n in plan.interfaces) + ' }')
    return [f'{iif} ip6 saddr :: ip6 hoplimit 255 icmpv6 type nd-neighbor-solicit accept comment "duplicate address detection"',
            f'{iif} ip6 saddr :: counter drop comment "unspecified source"',
            f'{iif} ip saddr 0.0.0.0/8 counter drop comment "unspecified source"',
            f'{iif} ip saddr != 0.0.0.0/8 fib saddr . iif oif missing counter drop comment "source not routed back here"',
            f'{iif} ip6 saddr != {{ ::, fe80::/10 }} fib saddr . iif oif missing counter drop comment "source not routed back here"']


def render_nft(plan):
    out = ['# ' + line if line else '#' for line in header(plan)]
    out += ['#',
            f'# Hooks run at priority {PRIORITY}, before Docker and ufw (0) and firewalld (10). A drop here is final whatever those add;',
            '# an accept here ends only this table\'s chain. Reload is atomic: the table is declared, deleted and defined in one file.',
            f'# Check:    nft -c -f FILE        Load:    nft -f FILE        Remove:    nft delete table inet {TABLE}',
            '', f'table inet {TABLE}', f'delete table inet {TABLE}', f'table inet {TABLE} {{']
    for chain in ('input', 'forward'):
        out += [f'\tchain {chain} {{', f'\t\ttype filter hook {chain} priority {PRIORITY}; policy accept;']
        out += ['\t\t' + nft_rule(r) for r in plan.rules if r.chain == chain]
        out.append('\t}')
    out += [f'\tchain rpf {{', f'\t\ttype filter hook prerouting priority {PRIORITY}; policy accept;']
    out += ['\t\t' + line for line in rpf_rules(plan)]
    out += ['\t}', '}']
    return '\n'.join(out) + '\n'


def ufw_rule(rule, interface=''):
    parts = ['ufw'] + (['route'] if rule.chain == 'forward' else []) + ['allow' if rule.action == 'accept' else 'deny']
    if interface:
        parts += ['in', 'on', interface]
    if rule.proto:
        parts += ['proto', rule.proto]
    parts += ['from', text(rule.src), 'to', ' '.join(text(n) for n in rule.dst) if rule.dst else 'any']
    if rule.port:
        parts += ['port', str(rule.port)]
    return ' '.join(parts) + f" comment 'sbarbase functions egress: {rule.note}'"


def expand(plan):
    """One rule per destination, the shape ufw and firewalld take; replies and neighbour discovery are
    left to those tools, which accept both on their own."""
    for rule in plan.rules:
        if rule.state or rule.proto == 'icmpv6' or rule.iif:
            continue
        for dst in rule.dst or (None,):
            yield rule._replace(dst=(dst,) if dst is not None else ())


def render_ufw(plan):
    out = ['#!/bin/sh'] + ['# ' + line if line else '#' for line in header(plan)]
    out += ['#',
            '# TRANSLATION into ufw commands, in rule order (ufw applies the first match). Not run, not verified: the nft',
            '# output is the reference. Replies to allowed traffic are ufw\'s own. Run with root: sh FILE',
            '# ufw filters forwarded Docker traffic only for rules that reach DOCKER-USER; if ufw route rules do not',
            '# change what a function reaches, load the nft output instead. Rules from an earlier run are not removed:',
            '# list them with `ufw status numbered`. `ufw allow` for the Storage listener also opens it through ufw\'s default deny.',
            'set -eu']
    out += [ufw_rule(r) for r in expand(plan)]
    # Link-local sources can only be matched by interface; ufw writes a wildcard with +. ufw accepts neighbour discovery itself.
    out += [ufw_rule(r._replace(iif=()), n.replace('*', '+')) for r in plan.rules if r.iif and r.action == 'drop' for n in r.iif]
    return '\n'.join(out) + '\n'


def firewalld_rule(rule, policy):
    fam = 'ipv4' if rule.family == 4 else 'ipv6'
    priority = -200 if rule.action == 'accept' else -100
    body = f'rule priority="{priority}" family="{fam}" source address="{text(rule.src)}"'
    if rule.dst:
        body += f' destination address="{text(rule.dst[0])}"'
    if rule.proto:
        body += f' port port="{rule.port}" protocol="{rule.proto}"'
    body += ' ' + rule.action
    return f"firewall-cmd --permanent --policy {policy} --add-rich-rule='{body}'"


FIREWALLD_POLICIES = (('input', 'sbarbase-fn-in', 'HOST'), ('forward', 'sbarbase-fn-fwd', 'ANY'))


def render_firewalld(plan):
    out = ['#!/bin/sh'] + ['# ' + line if line else '#' for line in header(plan)]
    out += ['#',
            '# TRANSLATION into firewalld policies with rich rules, accept before drop by rule priority. Not run, not',
            '# verified: the nft output is the reference. Established and related traffic is firewalld\'s own.',
            '# Needs firewalld 0.9 or later (policies, rich rule priority). Run with root: sh FILE',
            '# NOT translated: the drop of link-local IPv6 (fe80::/10) sources, which needs an interface match that rich rules',
            '# cannot express. Load the nft output, or set net.ipv6.conf.<bridge>.disable_ipv6=1 on the bridges listed above.',
            'set -eu']
    for chain, policy, egress in FIREWALLD_POLICIES:
        out += [f'firewall-cmd --permanent --delete-policy {policy} >/dev/null 2>&1 || true',
                f'firewall-cmd --permanent --new-policy {policy}',
                f'firewall-cmd --permanent --policy {policy} --add-ingress-zone ANY',
                f'firewall-cmd --permanent --policy {policy} --add-egress-zone {egress}']
        out += [firewalld_rule(r, policy) for r in expand(plan) if r.chain == chain]
    out.append('firewall-cmd --reload')
    return '\n'.join(out) + '\n'


TRANSLATION_WARNINGS = {
    'ufw': ('the ufw output is an untested translation. On a Docker host the `ufw route` rules (the FORWARD part) are probably '
            'inert for container egress, because Docker\'s own FORWARD accepts come first; ufw\'s before-input accepts for ICMP also '
            'precede the INPUT rules. This was reasoned from how ufw and Docker order their chains, not verified here.',
            'the ufw output carries neither the reverse-path check nor neighbour discovery handling; load the nft output for the full set.'),
    'firewalld': ('the firewalld output is an untested translation. It omits the link-local IPv6 drop and the reverse-path check, '
                  'and Docker\'s firewalld zone has target ACCEPT, so those holes stay open. Not verified here; load the nft output for the full set.',),
}

RENDERERS = {'nft': render_nft, 'ufw': render_ufw, 'firewalld': render_firewalld}


def activation(fmt, path):
    if fmt == 'nft':
        quoted = shlex.quote(str(path))
        return [f'sudo nft -c -f {quoted}', f'sudo nft -f {quoted}',
                f'# To keep it across reboots, include {quoted} from /etc/nftables.conf and enable nftables.service after docker.service.']
    return [f'sudo sh {shlex.quote(str(path))}']


# ---------------------------------------------------------------- checks

# The rules that may name an interface: the link-local source drop and the reverse-path check (see the module docstring).
LINK_LOCAL_INTERFACE = re.compile(r'(?:iifname (?:\{[^}]*\}|"[^"]*")|in on \S+)(?= (?:ip6 saddr fe80::/10|from fe80::/10|ip6? saddr != [^\n]* fib saddr \. iif oif missing|ip6 saddr :: |ip saddr 0\.0\.0\.0/8 ))')
FIB_RPF = re.compile(r'fib saddr \. iif oif missing')
INTERFACE_WORDS = re.compile(r'\b(iifname|oifname|iif|oif|in on|out on|interface|br-[0-9a-f]+|docker0)\b|--in-interface|--out-interface')


def check(plan):
    """Problems with a plan, without root. An empty list means every static invariant holds."""
    problems = []
    groups = {}
    for r in plan.rules:
        groups.setdefault((r.chain, r.family), []).append(r)
        if r.action == 'accept' and not (r.dst or r.proto or r.state):
            problems.append(f'{r.chain}: unconditional accept for {r.src}')
        if r.state and r.action != 'accept':
            problems.append(f'{r.chain}: a {r.action} rule on connection state for {r.src}')
    outbound = set(plan.outbound)
    for (chain, family), rules in groups.items():
        first_drop = next((i for i, r in enumerate(rules) if r.action == 'drop'), len(rules))
        before = rules[:first_drop]
        if any(r.action == 'accept' for r in rules[first_drop:]):
            problems.append(f'{chain}/ipv{family}: an accept follows a drop')
        sources = {r.src for r in rules}
        for src in sources:
            if not any(r.state and r.src == src for r in before):
                problems.append(f'{chain}/ipv{family}: no replies rule for {src} before the drops')
            if not any(r.action == 'drop' and r.src == src for r in rules):
                problems.append(f'{chain}/ipv{family}: no drop for {src}')
        # The allow list is for the outbound subnet; INPUT carries only the host entries.
        for a in plan.allow:
            if a.net.version != family or (chain == 'input' and not a.host):
                continue
            for src in (sources if chain == 'forward' else sources & outbound):
                if not any(r.note == 'allow list' and r.src == src and r.dst == (a.net,) and (r.proto, r.port) == (a.proto, a.port) for r in before):
                    problems.append(f'{chain}/ipv{family}: allow entry {describe(a)} is missing for {src} before the drops')
        if chain == 'input' and any(r.note == 'allow list' for r in rules if not any(a.host and (a.net,) == r.dst and (a.proto, a.port) == (r.proto, r.port) for a in plan.allow)):
            problems.append(f'input/ipv{family}: an allow rule that is not a host entry')
        if chain == 'input' and family == 6:
            for src in sources:
                if not any(r.proto == 'icmpv6' and r.src == src for r in before):
                    problems.append(f'input/ipv6: no neighbour discovery accept for {src}')
        if chain == 'forward':
            for f in plan.functions:
                if f.subnet.version == family and not any(r.note == 'own network' and r.src == f.subnet and r.dst == (f.subnet,) for r in before):
                    problems.append(f'forward/ipv{family}: functions network {f.subnet} does not accept its own subnet before the drops')
    for r in plan.rules:
        if r.note == 'functions to Storage listener':
            fn = next((f for f in plan.functions if f.subnet == r.src), None)
            if fn is None or r.chain != 'input' or r.proto != 'tcp' or r.port != plan.port or r.dst != (ipaddress.ip_network(fn.gateway),):
                problems.append(f'Storage accept for {r.src} is not tcp {plan.port} at its own gateway')
    for f in plan.functions:
        for chain in ('input', 'forward'):
            if not any(r.action == 'drop' and r.chain == chain and r.src == f.subnet for r in plan.rules):
                problems.append(f'functions network {f.subnet} has no {chain} drop')
    for n in plan.outbound:
        for chain in ('input', 'forward'):
            if not any(r.action == 'drop' and r.chain == chain and r.src == n for r in plan.rules):
                problems.append(f'outbound subnet {n} has no {chain} drop')
    if render_nft(plan).count('fib saddr . iif oif missing') != 2:
        problems.append('nft: the reverse-path check is not present for both IPv4 and IPv6')
    for fmt, render in RENDERERS.items():
        body = '\n'.join(line for line in render(plan).splitlines() if not line.lstrip().startswith('#'))
        body = FIB_RPF.sub('', LINK_LOCAL_INTERFACE.sub('', re.sub(r"comment '[^']*'|comment \"[^\"]*\"", '', body)))
        hit = INTERFACE_WORDS.search(body)
        if hit:
            problems.append(f'{fmt}: matches by interface ({hit.group(0)})')
    return problems


def nft_syntax_check(rendered):
    """(status, detail): ok, failed or skipped. `nft -c` needs netlink access, so it runs in a private
    user and network namespace (unshare -Urn); nothing on the host is touched."""
    needed = ('nft', 'unshare')
    paths = [next((Path(d)/n for d in os.environ.get('PATH', '').split(':') if (Path(d)/n).exists()), None) for n in needed]
    if not all(paths):
        return 'skipped', 'nft or unshare is not installed'
    try:
        with tempfile.TemporaryDirectory() as directory:
            for name, body in (('probe.nft', 'table inet probe_only { }\n'), ('plan.nft', rendered)):
                (Path(directory)/name).write_text(body)
                result = subprocess.run([str(paths[1]), '-Urn', str(paths[0]), '-c', '-f', str(Path(directory)/name)], capture_output=True, text=True, timeout=30)
                if result.returncode and name == 'probe.nft':
                    return 'skipped', 'a private namespace is not available: ' + (result.stderr.strip() or 'unshare failed')
                if result.returncode:
                    return 'failed', result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 'skipped', f'nft could not be run ({exc})'
    return 'ok', ''


# ---------------------------------------------------------------- command line

def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--format', choices=sorted(RENDERERS), default='nft', help='nft (default, the reference) or a ufw or firewalld translation')
    common.add_argument('--outbound-subnet', action='append', default=[], metavar='CIDR[=INTERFACE]', help=f'outbound network subnet and, optionally, its bridge device; default: read from {OUTBOUND_NETWORK}')
    common.add_argument('--function-network', action='append', default=[], metavar='SUBNET=GATEWAY[@INTERFACE]', help=f'environment functions network; default: every network labelled {FUNCTIONS_LABEL}')
    common.add_argument('--allow', action='append', default=[], metavar='CIDR[:port/proto]', help='destination reachable through FORWARD before any drop (private resolver, mail relay); refused if it covers a gateway or Docker network; repeatable')
    common.add_argument('--allow-host', action='append', default=[], metavar='CIDR:port/proto', help='like --allow but also opened in INPUT, so it may be an address of this host (a relay or resolver running here); the port is required; repeatable')
    common.add_argument('--allow-special', action='store_true', help='let --allow and --allow-host entries touch 169.254.0.0/16, 127.0.0.0/8, 100.64.0.0/10, 0.0.0.0/8 and the IPv6 link-local and loopback ranges')
    common.add_argument('--bridge-interface', action='append', default=[], metavar='NAME', help='a further bridge device for the link-local IPv6 drop and the reverse-path check, beside the names read from Docker or given with the subnets; repeatable. Any network without a known name also adds the br-* wildcard')
    common.add_argument('--storage-port', type=int, default=STORAGE_PORT, metavar='PORT', help=f'Storage listener port at each functions network gateway (default {STORAGE_PORT}, durable_runtime.FUNCTIONS_STORAGE_PORT)')
    common.add_argument('--deny', action='append', default=[], metavar='CIDR', help='extra range dropped in FORWARD; repeatable')
    common.add_argument('--resolv-conf', action='append', default=None, metavar='PATH', help='read nameservers from this file instead of /etc/resolv.conf; repeatable')
    common.add_argument('--no-resolv-conf', action='store_true', help='do not allow the host\'s nameservers automatically')
    common.add_argument('--no-discover', action='store_true', help='do not ask Docker for subnets; use only the flags')
    main = argparse.ArgumentParser(prog='functions_egress.py', description=__doc__.split('\n\n')[0])
    sub = main.add_subparsers(dest='command')
    sub.add_parser('plan', parents=[common], help='print the rules (default)')
    sub.add_parser('verify-plan', parents=[common], help='check the generated plan; needs no root and applies nothing')
    apply = sub.add_parser('apply', parents=[common], help='write the rules to --output and print the activation command; changes no firewall')
    apply.add_argument('--output', required=True, metavar='PATH', help='file to write; it is replaced atomically')
    return main


def write_file(path, body):
    """Replace PATH atomically with 0644 content; returns warnings. OS errors become PlanError."""
    target = Path(path)
    directory = target.parent
    warnings = []
    if target.exists() and not target.is_file():
        raise PlanError(f'--output {path} exists and is not a regular file')
    if not directory.is_dir():
        raise PlanError(f'--output directory {directory} does not exist')
    try:
        if directory.stat().st_mode & 0o002:
            warnings.append(f'{directory} is writable by everyone: anyone could replace this file before it is loaded as root. Use a directory only root or the operator can write.')
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.functions-egress-')
    except OSError as exc:
        raise PlanError(f'cannot write in {directory}: {exc.strerror or exc}')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        raise PlanError(f'cannot write {path}: {exc.strerror or exc}')
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return warnings


def run(argv, out=sys.stdout, err=sys.stderr):
    argv = list(argv)
    if not argv or (argv[0].startswith('-') and argv[0] not in ('-h', '--help')):
        argv.insert(0, 'plan')
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help(out)
        return 0
    try:
        plan = collect(args, err)
        rendered = RENDERERS[args.format](plan)
        if args.format != 'nft' and args.command != 'verify-plan':
            for warning in TRANSLATION_WARNINGS[args.format]:
                print(f'warning: {warning}', file=err)
        if args.command == 'plan':
            out.write(rendered)
        elif args.command == 'verify-plan':
            problems = check(plan)
            for problem in problems:
                print('FAIL ' + problem, file=out)
            status, detail = nft_syntax_check(render_nft(plan))
            print(f'nft syntax check: {status}' + (f' ({detail})' if detail else ''), file=out)
            print(f'{len(plan.rules)} rules, {len(plan.outbound)} outbound and {len(plan.functions)} functions subnets, {len(plan.allow)} allow entries: ' + ('plan consistent' if not problems else f'{len(problems)} problem(s)'), file=out)
            return 1 if problems or status == 'failed' else 0
        else:
            problems = check(plan)
            if problems:
                raise PlanError('refusing to write an inconsistent plan: ' + '; '.join(problems))
            if args.format == 'nft':
                # Never leave a file the real parser rejects behind an "operator runs" instruction.
                status, detail = nft_syntax_check(rendered)
                if status == 'failed':
                    raise PlanError('refusing to write rules that nft rejects: ' + detail)
                if status == 'skipped':
                    print(f'warning: the file was not checked with nft ({detail}); run nft -c -f on it before loading', file=err)
            for warning in write_file(args.output, rendered):
                print(f'warning: {warning}', file=err)
            print(f'wrote {args.output} ({args.format}). Nothing was applied. To activate, an operator runs:', file=out)
            for line in activation(args.format, args.output):
                print('  ' + line, file=out)
        return 0
    except PlanError as exc:
        print(f'error: {exc}', file=err)
        return 2


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))
