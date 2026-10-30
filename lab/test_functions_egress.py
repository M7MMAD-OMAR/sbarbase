"""Host egress rules for Edge Functions: what the generator emits, in order, in every format.

None of this needs Docker or root. The classes that parse the rules with the real `nft` or send real
packets through them do so inside a private user and network namespace (`unshare -Urn`), and are
skipped where that is unavailable.
"""
import contextlib
import io
import ipaddress
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import durable_runtime
import functions_egress as fe

NET = ipaddress.ip_network
# Built from code points so this file holds no long dash itself.
LONG_DASH = '[' + chr(0x2013) + chr(0x2014) + ']'
OUT4, OUT6 = NET('172.20.0.0/16'), NET('fd00:20::/64')
FN_A = fe.FunctionNet(NET('172.30.4.0/24'), ipaddress.ip_address('172.30.4.1'), 'sbarbase-durable-e_a-fn')
FN_B = fe.FunctionNet(NET('172.30.5.0/24'), ipaddress.ip_address('172.30.5.1'), 'sbarbase-durable-e_b-fn')
RESOLV = """# generated
nameserver 127.0.0.53
nameserver 192.168.1.1
nameserver ::1
nameserver fe80::1%eth0
nameserver 192.168.1.1
search lan
options edns0
"""


def plan(**kw):
    values = dict(outbound=[OUT4, OUT6], functions=[FN_A, FN_B], allow=[fe.parse_allow('192.168.1.10:587/tcp')],
                  deny=[], resolvers=fe.parse_resolv(RESOLV))
    values.update(kw)
    values.setdefault('interfaces_complete', bool(values.get('interfaces')))
    return fe.make_plan(**values)


def addresses(rendered):
    """Every address or range in the executable lines of one output, comments stripped."""
    found = set()
    for line in rendered.splitlines():
        if line.lstrip().startswith('#'):
            continue
        line = re.sub(r"comment '[^']*'|comment \"[^\"]*\"", '', line)
        for token in re.findall(r'[0-9A-Fa-f:.]+(?:/\d+)?', line):
            if '.' in token or ':' in token:
                try:
                    ipaddress.ip_network(token, strict=False)
                except ValueError:
                    continue
                found.add(token)
    return found


class OrderTests(unittest.TestCase):
    def test_every_drop_comes_after_every_accept_in_its_chain(self):
        p = plan()
        groups = {}
        for r in p.rules:
            groups.setdefault((r.chain, r.family), []).append(r.action)
        self.assertEqual(set(groups), {('input', 4), ('input', 6), ('forward', 4), ('forward', 6)})
        for key, actions in groups.items():
            first_drop = actions.index('drop')
            self.assertNotIn('accept', actions[first_drop:], key)
            self.assertIn('accept', actions[:first_drop], key)

    def test_the_allow_list_precedes_the_drops_in_both_chains_and_in_the_text(self):
        p = plan()
        self.assertEqual(fe.check(p), [])
        nft = fe.render_nft(p).splitlines()
        for chain_start in ('chain input', 'chain forward'):
            body = nft[next(i for i, l in enumerate(nft) if chain_start in l):]
            body = body[:body.index('\t}')]
            for family in ('ip saddr', 'ip6 saddr'):
                lines = [l for l in body if family + ' ' in l]
                allow = [i for i, l in enumerate(lines) if 'allow list' in l]
                drop = [i for i, l in enumerate(lines) if ' drop ' in l]
                self.assertTrue(allow and drop, (chain_start, family))
                self.assertLess(max(allow), min(drop), (chain_start, family))

    def test_check_flags_an_accept_placed_after_a_drop(self):
        p = plan()
        broken = p._replace(rules=p.rules + (fe.Rule('input', 4, 'accept', OUT4, (NET('10.0.0.5'),), 'tcp', 22, note='allow list'),))
        self.assertTrue(any('follows a drop' in problem for problem in fe.check(broken)))
        missing = p._replace(rules=tuple(r for r in p.rules if r.note != 'allow list'))
        self.assertTrue(any('is missing for' in problem for problem in fe.check(missing)))


class ResolverTests(unittest.TestCase):
    def test_nameservers_are_parsed_without_loopback_zone_ids_or_duplicates(self):
        found = fe.parse_resolv(RESOLV)
        self.assertEqual([str(a) for a in found], ['192.168.1.1', 'fe80::1'])

    def test_private_resolvers_are_allowed_over_udp_and_tcp_and_loopback_ones_are_not(self):
        p = plan(allow=[])
        nft = fe.render_nft(p)
        self.assertIn('ip daddr 192.168.1.1 udp dport 53 accept', nft)
        self.assertIn('ip daddr 192.168.1.1 tcp dport 53 accept', nft)
        self.assertIn('ip6 daddr fe80::1 udp dport 53 accept', nft)
        self.assertNotIn('127.0.0.53', nft.replace('127.0.0.0/8', ''))
        self.assertNotIn(' ::1 udp', nft)
        self.assertEqual([a.port for a in p.allow], [53] * 4)

    def test_resolvers_read_from_a_file_and_a_missing_file_gives_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'resolv.conf'
            path.write_text(RESOLV)
            self.assertEqual([str(a) for a in fe.read_resolvers([str(path)])], ['192.168.1.1', 'fe80::1'])
            self.assertEqual(fe.read_resolvers([str(Path(d)/'absent')]), [])

    def test_an_empty_allow_list_is_called_out_in_the_header(self):
        self.assertIn('WARNING: the allow list is empty', fe.render_nft(plan(allow=[], resolvers=[])))
        self.assertNotIn('WARNING: the allow list is empty', fe.render_nft(plan()))


class StorageListenerTests(unittest.TestCase):
    def accepts(self, p):
        return [r for r in p.rules if r.note == 'functions to Storage listener']

    def test_the_port_is_the_runtimes_constant(self):
        self.assertEqual(fe.STORAGE_PORT, durable_runtime.FUNCTIONS_STORAGE_PORT)

    def test_storage_is_reachable_only_at_each_networks_own_gateway_over_tcp(self):
        rules = self.accepts(plan())
        self.assertEqual({(r.src, r.dst) for r in rules}, {(FN_A.subnet, (NET('172.30.4.1'),)), (FN_B.subnet, (NET('172.30.5.1'),))})
        for r in rules:
            self.assertEqual((r.chain, r.proto, r.port, r.action), ('input', 'tcp', fe.STORAGE_PORT, 'accept'))
        nft = fe.render_nft(plan())
        self.assertIn(f'ip saddr 172.30.4.0/24 ip daddr 172.30.4.1 tcp dport {fe.STORAGE_PORT} accept', nft)
        self.assertNotIn('ip saddr 172.30.4.0/24 ip daddr 172.30.5.1', nft)
        self.assertNotIn(f'udp dport {fe.STORAGE_PORT}', nft)

    def test_each_functions_network_is_dropped_for_every_other_host_address_after_its_accept(self):
        p = plan()
        for fn in (FN_A, FN_B):
            mine = [r for r in p.rules if r.chain == 'input' and r.src == fn.subnet]
            self.assertEqual([r.action for r in mine], ['accept', 'accept', 'drop'])
            self.assertEqual(mine[-1].dst, ())

    def test_the_outbound_subnet_never_gets_the_storage_port(self):
        self.assertFalse([r for r in plan().rules if r.src in (OUT4, OUT6) and r.port == fe.STORAGE_PORT])


class CoverageTests(unittest.TestCase):
    def test_no_format_matches_by_interface_name(self):
        p = plan()
        for fmt, render in fe.RENDERERS.items():
            body = re.sub(r"comment '[^']*'|comment \"[^\"]*\"", '', '\n'.join(l for l in render(p).splitlines() if not l.startswith('#')))
            # The one allowed interface match is the link-local IPv6 source drop: no subnet can match those sources.
            for line in body.splitlines():
                if re.search(r'iifname|\bin on\b', line):
                    self.assertTrue('fe80::/10' in line or 'fib saddr . iif oif missing' in line or 'saddr :: ' in line or 'saddr 0.0.0.0/8 ' in line, (fmt, line))
            body = fe.FIB_RPF.sub('', fe.LINK_LOCAL_INTERFACE.sub('', body))
            self.assertIsNone(fe.INTERFACE_WORDS.search(body), fmt)
            self.assertNotRegex(body, r'\bbr-|docker0|iifname|oifname|ip(6)? saddr \S+ iif', fmt)

    def test_forward_drops_cover_every_requested_ipv4_and_ipv6_range(self):
        nft = fe.render_nft(plan())
        for r in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16', '127.0.0.0/8',
                  'fc00::/7', 'fe80::/10', '::1'):
            self.assertIn(r, nft)
        denied6 = next(r.dst for r in plan().rules if r.chain == 'forward' and r.family == 6 and r.action == 'drop')
        self.assertEqual(denied6, (NET('::1/128'), NET('fc00::/7'), NET('fe80::/10')))
        self.assertIn('ip6 saddr fd00:20::/64 ip6 daddr', nft)
        self.assertIn('ip6 saddr fd00:20::/64 counter drop', nft)

    def test_without_an_ipv6_subnet_no_ipv6_source_rule_exists_and_the_header_says_so(self):
        p = plan(outbound=[OUT4], functions=[FN_A])
        nft = fe.render_nft(p)
        self.assertEqual([l.strip().split(' ip6 saddr')[1].split()[0] for l in nft.splitlines() if 'ip6 saddr' in l and 'fib' not in l and 'saddr ::' not in l], ['fe80::/10'] * 3)
        self.assertNotIn('fd00', nft)
        self.assertIn('no IPv6 subnet source rules were generated', nft)

    def test_extra_denies_are_added_and_merged_with_the_defaults(self):
        nft = fe.render_nft(plan(deny=[NET('203.0.113.0/24'), NET('10.1.0.0/16')]))
        self.assertIn('203.0.113.0/24', nft)
        self.assertNotIn('10.1.0.0/16', nft)

    def test_all_formats_name_the_same_addresses(self):
        p = plan()
        sets = {fmt: addresses(render(p)) for fmt, render in fe.RENDERERS.items()}
        # The reverse-path check is nft only and exempts the unspecified addresses; nothing else is nft only.
        sets['nft'] -= {'0.0.0.0/8', '::'}
        self.assertTrue(sets['nft'])
        self.assertEqual(sets['ufw'], sets['nft'])
        self.assertEqual(sets['firewalld'], sets['nft'])
        for expected in ('192.168.1.10', '172.30.4.1', '172.20.0.0/16', 'fd00:20::/64', '100.64.0.0/10', 'fc00::/7'):
            self.assertIn(expected, sets['nft'])

    def test_translations_are_marked_and_the_header_lists_what_is_not_covered(self):
        p = plan()
        for fmt in ('ufw', 'firewalld'):
            self.assertIn('TRANSLATION', fe.RENDERERS[fmt](p))
        for fmt, render in fe.RENDERERS.items():
            self.assertIn('It does NOT cover', render(p), fmt)

    def test_priority_runs_before_dockers_chains(self):
        nft = fe.render_nft(plan())
        self.assertEqual(len(re.findall(r'priority -10; policy accept;', nft)), 3)
        self.assertLess(fe.PRIORITY, 0)

    def test_output_has_no_long_dashes(self):
        p = plan()
        for render in fe.RENDERERS.values():
            self.assertNotRegex(render(p), LONG_DASH)
        self.assertNotRegex(Path(fe.__file__).read_text(), LONG_DASH)


class InputTests(unittest.TestCase):
    def test_empty_and_malformed_subnets_are_refused(self):
        for bad in ('', '  ', 'not-a-net', '10.0.0.0/33', '172.20.0.1/16', '0.0.0.0/0', '::/0', '10.0.0.0/7', 'fd00::/8'):
            with self.assertRaises(fe.PlanError, msg=bad):
                fe.parse_subnet(bad)
        for bad in ('', '172.30.4.0/24', '172.30.4.0/24=', '172.30.4.0/24=172.31.0.1', '172.30.4.0/24=nope', '=172.30.4.1'):
            with self.assertRaises(fe.PlanError, msg=bad):
                fe.parse_function_network(bad)
        with self.assertRaises(fe.PlanError):
            fe.make_plan([], [FN_A])

    def test_malformed_allow_entries_are_refused(self):
        for bad in ('', 'nope', '10.0.0.0/8:99999/tcp', '10.0.0.5:0/tcp', '10.0.0.5:53', '10.0.0.5:53/icmp', '0.0.0.0/0', '10.0.0.5/8'):
            with self.assertRaises(fe.PlanError, msg=bad):
                fe.parse_allow(bad)

    def test_allow_forms(self):
        self.assertEqual(fe.parse_allow('192.168.1.0/24'), fe.Allow(NET('192.168.1.0/24')))
        self.assertEqual(fe.parse_allow('192.168.1.10:587/tcp'), fe.Allow(NET('192.168.1.10'), 'tcp', 587))
        self.assertEqual(fe.parse_allow('192.168.0.0/16:53/udp'), fe.Allow(NET('192.168.0.0/16'), 'udp', 53))
        self.assertEqual(fe.parse_allow('[fd00::/64]:53/udp'), fe.Allow(NET('fd00::/64'), 'udp', 53))
        self.assertEqual(fe.parse_allow('fd00::1:587/tcp'), fe.Allow(NET('fd00::1'), 'tcp', 587))

    def test_overlapping_subnets_are_refused(self):
        with self.assertRaises(fe.PlanError):
            fe.make_plan([NET('172.20.0.0/16')], [fe.FunctionNet(NET('172.20.4.0/24'), ipaddress.ip_address('172.20.4.1'))])

    def test_generation_is_deterministic_whatever_the_input_order(self):
        a = plan()
        b = plan(outbound=[OUT6, OUT4], functions=[FN_B, FN_A], resolvers=list(reversed(fe.parse_resolv(RESOLV))),
                 allow=[fe.parse_allow('192.168.1.10:587/tcp'), fe.parse_allow('192.168.1.10:587/tcp')])
        for fmt, render in fe.RENDERERS.items():
            self.assertEqual(render(a), render(b), fmt)
            self.assertEqual(render(a), render(plan()), fmt)


class DiscoveryTests(unittest.TestCase):
    def test_subnets_come_from_the_outbound_network_and_the_labelled_functions_networks(self):
        def fake(*args):
            if args[:2] == ('network', 'inspect') and args[2] == fe.OUTBOUND_NETWORK:
                return '[{"IPAM": {"Config": [{"Subnet": "172.20.0.0/16", "Gateway": "172.20.0.1"}, {"Subnet": "fd00:20::/64"}]}}]'
            if args[:2] == ('network', 'ls'):
                self.assertIn('label=' + fe.FUNCTIONS_LABEL, args)
                return 'aaa\nbbb\n'
            return ('[{"Name": "n-a", "IPAM": {"Config": [{"Subnet": "172.30.4.0/24", "Gateway": "172.30.4.1"}]}},'
                    '{"Name": "n-b", "IPAM": {"Config": null}}]')
        with patch.object(fe, 'docker', side_effect=fake):
            self.assertEqual(fe.discover_outbound(), [(OUT4, ipaddress.ip_address('172.20.0.1'), ''), (OUT6, None, '')])
            self.assertEqual(fe.discover_functions(), [fe.FunctionNet(NET('172.30.4.0/24'), ipaddress.ip_address('172.30.4.1'), 'n-a')])

    def test_a_missing_outbound_network_or_gateway_is_an_error_not_an_empty_plan(self):
        with patch.object(fe, 'docker', return_value='[]'):
            with self.assertRaises(fe.PlanError):
                fe.discover_outbound()
        with patch.object(fe, 'docker', side_effect=['x', '[{"Name": "n", "IPAM": {"Config": [{"Subnet": "172.30.4.0/24"}]}}]']):
            with self.assertRaises(fe.PlanError):
                fe.discover_functions()


class CommandTests(unittest.TestCase):
    ARGS = ['--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa', '--function-network', '172.30.4.0/24=172.30.4.1@br-bbbbbbbbbbbb',
            '--allow', '192.168.1.1:53/udp']

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = fe.run(list(argv), out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_plan_is_the_default_command_and_prints_the_nft_table(self):
        code, out, err = self.run_cli(*self.ARGS)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('table inet sbarbase_functions {', out)
        self.assertEqual(self.run_cli('plan', *self.ARGS)[1], out)

    def test_a_malformed_argument_exits_nonzero_with_a_message_and_prints_no_rules(self):
        code, out, err = self.run_cli('--no-discover', '--outbound-subnet', '', '--function-network', '172.30.4.0/24=172.30.4.1@br-bbbbbbbbbbbb')
        self.assertEqual((code, out), (2, ''))
        self.assertIn('error:', err)

    def test_verify_plan_passes_a_consistent_plan_without_root(self):
        code, out, _ = self.run_cli('verify-plan', *self.ARGS)
        self.assertEqual(code, 0, out)
        self.assertIn('plan consistent', out)
        self.assertRegex(out, r'nft syntax check: (ok|skipped)')

    def test_apply_writes_only_the_named_file_and_prints_the_activation_command(self):
        with tempfile.TemporaryDirectory() as d, patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            target = Path(d)/'egress.nft'
            code, out, err = self.run_cli('apply', '--output', str(target), *self.ARGS)
            self.assertEqual((code, err), (0, ''))
            # The only commands apply may run are the check-only nft parse inside a private namespace.
            for call in run.call_args_list:
                self.assertTrue(call.args[0][0].endswith('unshare') and '-c' in call.args[0] and '-f' in call.args[0], call.args[0])
            self.assertEqual(target.read_text(), fe.render_nft(fe.collect(fe.parser().parse_args(['plan', *self.ARGS]))))
            self.assertIn(f'sudo nft -c -f {target}', out)
            self.assertIn(f'sudo nft -f {target}', out)
            self.assertIn('Nothing was applied', out)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ['egress.nft'])
            self.assertEqual(self.run_cli('apply', '--output', str(Path(d)/'missing'/'x.nft'), *self.ARGS)[0], 2)
            code, out, _ = self.run_cli('apply', '--format', 'ufw', '--output', str(Path(d)/'egress.sh'), *self.ARGS)
            self.assertIn(f'sudo sh {Path(d)/"egress.sh"}', out)

    def test_apply_requires_an_output_path(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            fe.run(['apply', *self.ARGS])


class SyntaxTests(unittest.TestCase):
    def test_the_nft_output_parses_with_the_real_nft(self):
        for p in (plan(), plan(outbound=[OUT4], functions=[], allow=[], resolvers=[])):
            status, detail = fe.nft_syntax_check(fe.render_nft(p))
            if status == 'skipped':
                self.skipTest(detail)
            self.assertEqual((status, detail), ('ok', ''))


# ---------------------------------------------------------------- parsers of the generated text

NFT_LINE = re.compile(r'(?:iifname (?P<iif>\{[^}]*\}|"[^"]*") )?(?P<fam>ip6?) saddr (?P<src>\S+)'
                      r'(?: ip6? daddr (?P<dst>\{[^}]*\}|\S+))?'
                      r'(?: ip6 hoplimit 255 icmpv6 type \{[^}]*\})?'
                      r'(?: (?P<proto>tcp|udp) dport (?P<port>\d+))?'
                      r'(?P<state> ct state established,related)?'
                      r'(?: counter)? (?P<action>accept|drop) comment "(?P<note>[^"]*)"')


def parse_nft(rendered):
    """[(chain, action, src, dsts, proto, port, state, icmp)] in file order."""
    out, chain = [], None
    for line in rendered.splitlines():
        match = re.match(r'\tchain (\w+) \{', line)
        if match:
            chain = match.group(1)
            continue
        if not line.startswith(('\t\tip', '\t\tiifname')) or chain == 'rpf':
            continue
        m = NFT_LINE.fullmatch(line.strip())
        assert m, line
        dst = m.group('dst') or ''
        dsts = tuple(x.strip() for x in dst.strip('{}').split(',')) if dst else ()
        iif = tuple(x.strip().strip('"') for x in m.group('iif').strip('{}').split(',')) if m.group('iif') else ()
        out.append((chain, m.group('action'), m.group('src'), dsts, m.group('proto') or '', int(m.group('port') or 0),
                    bool(m.group('state')), 'icmpv6' in line, iif))
    return out


def expected_flat(p):
    """Per destination (chain, action, src, dst, proto, port) for the rules ufw and firewalld can express."""
    out = []
    for r in p.rules:
        if r.state or r.proto == 'icmpv6' or r.iif:
            continue
        for dst in r.dst or (None,):
            out.append((r.chain, r.action, fe.text(r.src), fe.text(dst) if dst is not None else 'any', r.proto, r.port))
    return out


def parse_ufw(rendered):
    out = []
    for line in rendered.splitlines():
        if ' in on ' in line:
            continue
        m = re.fullmatch(r"ufw( route)? (allow|deny)(?: proto (\w+))? from (\S+) to (\S+)(?: port (\d+))? comment '[^']*'", line)
        if line.startswith('ufw'):
            assert m, line
            out.append(('forward' if m.group(1) else 'input', 'accept' if m.group(2) == 'allow' else 'drop', m.group(4), m.group(5), m.group(3) or '', int(m.group(6) or 0)))
    return out


def parse_firewalld(rendered):
    out, priorities = [], []
    for line in rendered.splitlines():
        m = re.fullmatch(r"firewall-cmd --permanent --policy (\S+) --add-rich-rule='rule priority=\"(-?\d+)\" family=\"ipv[46]\" "
                         r"source address=\"([^\"]+)\"(?: destination address=\"([^\"]+)\")?(?: port port=\"(\d+)\" protocol=\"(\w+)\")? (accept|drop)'", line)
        if '--add-rich-rule' in line:
            assert m, line
            chain = {'sbarbase-fn-in': 'input', 'sbarbase-fn-fwd': 'forward'}[m.group(1)]
            out.append((chain, m.group(7), m.group(3), m.group(4) or 'any', m.group(6) or '', int(m.group(5) or 0)))
            priorities.append((m.group(1), m.group(7), int(m.group(2))))
    return out, priorities


def by_chain(entries):
    """Input rules, then forward rules, each chain in its own order: the order the files are read in."""
    return sorted(entries, key=lambda e: e[0] != 'input')


class SemanticTests(unittest.TestCase):
    def test_the_nft_text_says_exactly_what_the_rule_list_says(self):
        p = plan()
        want = [(r.chain, r.action, fe.text(r.src), tuple(fe.text(n) for n in r.dst), r.proto if r.proto != 'icmpv6' else '', r.port, r.state, r.proto == 'icmpv6', r.iif) for r in p.rules]
        self.assertEqual(parse_nft(fe.render_nft(p)), by_chain(want))

    def test_replies_are_accepted_by_connection_state_never_unconditionally(self):
        p = plan()
        text = parse_nft(fe.render_nft(p))
        for entry in text:
            chain, action, src, dsts, proto, port, state, icmp, iif = entry
            if action == 'accept':
                self.assertTrue(dsts or proto or state or icmp, f'unconditional accept: {entry}')
        replies = [r for r in p.rules if r.note == 'replies']
        self.assertTrue(replies and all(r.state and r.action == 'accept' for r in replies))
        broken = p._replace(rules=tuple(r._replace(state=False) if r.note == 'replies' else r for r in p.rules))
        self.assertTrue(any('unconditional accept' in problem for problem in fe.check(broken)))

    def test_every_source_has_a_replies_rule_in_input_and_forward(self):
        p = plan()
        sources = {n for n in p.outbound} | {f.subnet for f in p.functions}
        self.assertEqual({r.src for r in p.rules if r.chain == 'forward' and r.note == 'replies'}, sources)
        self.assertEqual({r.src for r in p.rules if r.chain == 'input' and r.note == 'replies'}, sources | {fe.LINK_LOCAL})
        without = p._replace(rules=tuple(r for r in p.rules if not (r.chain == 'forward' and r.note == 'replies')))
        self.assertTrue(any('no replies rule' in problem for problem in fe.check(without)))

    def test_ufw_translation_carries_the_same_destination_port_protocol_and_action(self):
        p = plan()
        self.assertEqual(sorted(parse_ufw(fe.render_ufw(p))), sorted(expected_flat(p)))
        actions = {entry[1] for entry in parse_ufw(fe.render_ufw(p))}
        self.assertEqual(actions, {'accept', 'drop'})

    def test_firewalld_translation_carries_the_same_rules_and_runs_accepts_before_drops(self):
        p = plan()
        rules, priorities = parse_firewalld(fe.render_firewalld(p))
        self.assertEqual(sorted(rules), sorted(expected_flat(p)))
        for policy in ('sbarbase-fn-in', 'sbarbase-fn-fwd'):
            accept = [n for pol, action, n in priorities if pol == policy and action == 'accept']
            drop = [n for pol, action, n in priorities if pol == policy and action == 'drop']
            self.assertTrue(accept and drop, policy)
            self.assertLess(max(accept), min(drop), policy)

    def test_translations_keep_the_rule_order_of_the_nft_output(self):
        p = plan()
        flat = expected_flat(p)
        self.assertEqual(by_chain(parse_ufw(fe.render_ufw(p))), by_chain(flat))
        self.assertEqual(by_chain(parse_firewalld(fe.render_firewalld(p))[0]), by_chain(flat))


class FunctionsSourceTests(unittest.TestCase):
    def test_functions_subnets_are_forward_sources_with_the_same_drops_as_the_outbound_subnet(self):
        p = plan()
        drops = {r.src: r.dst for r in p.rules if r.chain == 'forward' and r.action == 'drop'}
        self.assertEqual(set(drops), {OUT4, OUT6, FN_A.subnet, FN_B.subnet})
        self.assertEqual(drops[FN_A.subnet], drops[OUT4])
        self.assertIn(NET('169.254.0.0/16'), drops[FN_A.subnet])

    def test_a_functions_network_accepts_its_own_subnet_before_the_drops(self):
        rules = [r for r in plan().rules if r.chain == 'forward' and r.src == FN_A.subnet]
        self.assertEqual([r.note for r in rules][:2], ['replies', 'own network'])
        self.assertEqual(rules[1].dst, (FN_A.subnet,))
        self.assertEqual(rules[-1].action, 'drop')
        broken = plan()._replace(rules=tuple(r for r in plan().rules if r.note != 'own network'))
        self.assertTrue(any('its own subnet' in problem for problem in fe.check(broken)))

    def test_the_forward_allow_list_applies_to_functions_sources_too(self):
        p = plan()
        sources = {r.src for r in p.rules if r.chain == 'forward' and r.note == 'allow list' and r.dst == (NET('192.168.1.10'),)}
        self.assertEqual(sources, {OUT4, FN_A.subnet, FN_B.subnet})


class AllowGuardTests(unittest.TestCase):
    def test_an_allow_entry_is_forward_only_and_a_host_entry_is_also_in_input(self):
        p = plan(allow=[fe.parse_allow('192.168.1.10:587/tcp'), fe.parse_allow('192.168.1.5:25/tcp', host=True)], resolvers=[])
        inp = {r.dst for r in p.rules if r.chain == 'input' and r.note == 'allow list'}
        fwd = {r.dst for r in p.rules if r.chain == 'forward' and r.note == 'allow list' and r.src == OUT4}
        self.assertEqual(inp, {(NET('192.168.1.5'),)})
        self.assertEqual(fwd, {(NET('192.168.1.5'),), (NET('192.168.1.10'),)})
        self.assertEqual(fe.check(p), [])

    def test_resolvers_are_host_entries_so_a_resolver_on_the_host_can_be_asked(self):
        p = plan(allow=[])
        self.assertTrue(p.allow and all(a.host for a in p.allow))
        self.assertTrue([r for r in p.rules if r.chain == 'input' and r.note == 'allow list'])

    def test_an_allow_entry_covering_a_gateway_or_a_known_network_is_refused(self):
        for bad in ('172.30.4.1:22/tcp', '172.30.0.0/16', '172.20.0.0/16', '172.16.0.0/12', 'fd00::/8', '192.168.0.0/16'):
            with self.assertRaises(fe.PlanError, msg=bad):
                fe.make_plan([OUT4, NET('fd00:20::/64')], [FN_A], [fe.parse_allow(bad)], bridges=[NET('192.168.5.0/24')],
                             hosts=[ipaddress.ip_address('192.168.5.1')]) if bad != 'fd00::/8' else \
                    fe.make_plan([OUT4, OUT6], [FN_A], [fe.Allow(NET(bad))])

    def test_a_docker_gateway_is_refused_without_allow_host_and_accepted_with_it(self):
        gw = [ipaddress.ip_address('172.17.0.1')]
        with self.assertRaises(fe.PlanError):
            fe.make_plan([OUT4], [], [fe.parse_allow('172.17.0.1:9000/tcp')], hosts=gw)
        p = fe.make_plan([OUT4], [], [fe.parse_allow('172.17.0.1:9000/tcp', host=True)], hosts=gw)
        self.assertIn(NET('172.17.0.1'), {r.dst[0] for r in p.rules if r.chain == 'input' and r.note == 'allow list'})

    def test_allow_host_needs_a_port(self):
        with self.assertRaises(fe.PlanError):
            fe.parse_allow('192.168.1.5', host=True)
        with self.assertRaises(fe.PlanError):
            fe.make_plan([OUT4], [], [fe.Allow(NET('192.168.1.5'), host=True)])

    def test_a_clean_lan_entry_is_accepted(self):
        fe.make_plan([OUT4], [FN_A], [fe.parse_allow('192.168.1.10:587/tcp')], bridges=[NET('172.17.0.0/16')])


class BridgeAndPortTests(unittest.TestCase):
    def test_other_docker_bridges_are_dropped_even_outside_the_private_ranges(self):
        p = plan(bridges=[NET('203.0.113.0/24'), NET('2001:db8:1::/64')])
        nft = fe.render_nft(p)
        self.assertIn('203.0.113.0/24', nft)
        self.assertIn('2001:db8:1::/64', nft)
        self.assertEqual(fe.check(p), [])

    def test_discovery_collects_bridge_subnets_and_gateways(self):
        def fake(*args):
            if args[:2] == ('network', 'ls'):
                self.assertIn('driver=bridge', args)
                return 'a\nb\n'
            return ('[{"IPAM": {"Config": [{"Subnet": "203.0.113.0/24", "Gateway": "203.0.113.1"}]}},'
                    '{"IPAM": {"Config": [{"Subnet": "garbage"}, {"Subnet": "172.17.0.0/16", "Gateway": "172.17.0.1"}]}}]')
        with patch.object(fe, 'docker', side_effect=fake):
            nets, gateways = fe.discover_bridges()
        self.assertEqual(nets, [NET('203.0.113.0/24'), NET('172.17.0.0/16')])
        self.assertEqual([str(g) for g in gateways], ['203.0.113.1', '172.17.0.1'])

    def test_discovery_refuses_a_scope_id_in_a_bridge_subnet_or_gateway(self):
        def fake(subnet, gateway):
            def run(*args):
                if args[:2] == ('network', 'ls'):
                    return 'a\n'
                return '[{"IPAM": {"Config": [{"Subnet": "%s", "Gateway": "%s"}]}}]' % (subnet, gateway)
            return run
        for subnet, gateway in (('fd00:9::/64', 'fe80::1%eth0'), ('fe80::%eth0/64', 'fe80::1')):
            with patch.object(fe, 'docker', side_effect=fake(subnet, gateway)):
                with self.assertRaisesRegex(fe.PlanError, 'scope id'):
                    fe.discover_bridges()

    def test_ipv6_neighbour_discovery_is_accepted_before_the_drop_for_each_ipv6_source(self):
        p = plan(functions=[FN_A, fe.FunctionNet(NET('fd00:30::/64'))])
        nd = [r for r in p.rules if r.proto == 'icmpv6']
        self.assertEqual({r.src for r in nd}, {OUT6, NET('fd00:30::/64'), fe.LINK_LOCAL})
        nft = fe.render_nft(p)
        self.assertIn('icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert } accept', nft)
        self.assertNotIn('icmpv6 type echo', nft)
        self.assertEqual(fe.check(p), [])
        self.assertNotIn('icmpv6', fe.render_ufw(p) + fe.render_firewalld(p).replace('icmpv6 ', ''))

    def test_the_storage_port_is_a_flag_defaulting_to_the_runtimes_constant(self):
        args = fe.parser().parse_args(['plan'])
        self.assertEqual(args.storage_port, durable_runtime.FUNCTIONS_STORAGE_PORT)
        p = plan(port=31000)
        self.assertEqual(p.port, 31000)
        nft = fe.render_nft(p)
        self.assertIn('tcp dport 31000 accept comment "functions to Storage listener"', nft)
        self.assertNotIn(str(fe.STORAGE_PORT) + ' accept', nft.replace('31000', ''))
        for bad in (0, 70000, -1):
            with self.assertRaises(fe.PlanError):
                plan(port=bad)
        self.assertEqual(self.run_cli('--storage-port', '31000')[0], 0)

    def run_cli(self, *extra):
        out = io.StringIO()
        code = fe.run(['--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa', '--function-network', '172.30.4.0/24=172.30.4.1@br-bbbbbbbbbbbb', *extra], out=out, err=io.StringIO())
        return code, out.getvalue()


class ErrorHandlingTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        return fe.run(list(argv), out=out, err=err), out.getvalue(), err.getvalue()

    def test_bad_docker_output_is_an_error_message_not_a_traceback(self):
        for payload in ('not json', '{"a": 1}', '[1, 2]'):
            with patch.object(fe, 'docker', return_value=payload):
                code, out, err = self.run_cli('plan')
            self.assertEqual((code, out), (2, ''), payload)
            self.assertTrue(err.startswith('error:'), payload)

    def test_a_missing_docker_and_a_bad_docker_exit_are_not_reported_as_bad_json(self):
        with patch.object(fe.subprocess, 'run', side_effect=FileNotFoundError('docker')):
            code, _, err = self.run_cli('plan')
        self.assertEqual(code, 2)
        self.assertIn('docker is unavailable', err)
        self.assertNotIn('not JSON', err)
        failed = subprocess.CompletedProcess([], 1, '', 'daemon down')
        with patch.object(fe.subprocess, 'run', return_value=failed):
            code, _, err = self.run_cli('plan')
        self.assertEqual(code, 2)
        self.assertIn('failed: daemon down', err)
        self.assertNotIn('not JSON', err)
        with patch.object(fe, 'docker', return_value='nope'):
            self.assertIn('not JSON', self.run_cli('plan')[2])

    def test_a_hung_or_unrunnable_nft_is_a_skip_in_verify_plan_not_a_traceback(self):
        if not (shutil.which('nft') and shutil.which('unshare')):
            self.skipTest('nft or unshare is not installed')
        for failure in (subprocess.TimeoutExpired('unshare', 30), PermissionError('unshare')):
            with patch.object(fe.subprocess, 'run', side_effect=failure):
                status, detail = fe.nft_syntax_check(fe.render_nft(plan()))
                code, out, err = self.run_cli('verify-plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa')
            self.assertEqual(status, 'skipped')
            self.assertIn('could not be run', detail)
            self.assertEqual(code, 0, err)
            self.assertIn('nft syntax check: skipped', out)

    def test_an_unwritable_output_is_an_error_message_not_a_traceback(self):
        base = ['--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa']
        for target in ('/proc/x.nft', '/proc/self/nope/x.nft'):
            code, out, err = self.run_cli('apply', '--output', target, *base)
            self.assertEqual(code, 2, target)
            self.assertTrue(err.startswith('error:'), err)
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o500)
            try:
                if os.access(d, os.W_OK):
                    self.skipTest('running with rights that ignore directory modes')
                code, _, err = self.run_cli('apply', '--output', str(Path(d)/'x.nft'), *base)
            finally:
                os.chmod(d, 0o700)
            self.assertEqual(code, 2)
            self.assertTrue(err.startswith('error:'))

    def test_a_failed_replace_is_an_error_message_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as d, patch.object(fe.os, 'replace', side_effect=OSError(13, 'Permission denied')):
            code, out, err = self.run_cli('apply', '--output', str(Path(d)/'x.nft'), '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa')
            self.assertEqual(code, 2)
            self.assertIn('error: cannot write', err)
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_printed_activation_commands_quote_the_path(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)/'a file; touch x.nft'
            code, out, err = self.run_cli('apply', '--output', str(target), '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa')
            self.assertEqual(code, 0, err)
            self.assertIn("sudo nft -c -f '" + str(target) + "'", out)
            self.assertIn("sudo nft -f '" + str(target) + "'", out)
            code, out, _ = self.run_cli('apply', '--format', 'ufw', '--output', str(target), '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa')
            self.assertIn("sudo sh '" + str(target) + "'", out)

    def test_the_output_file_mode_is_0644_whatever_the_umask(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.umask(0o077)
            try:
                self.run_cli('apply', '--output', str(Path(d)/'x.nft'), '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa')
            finally:
                os.umask(old)
            self.assertEqual(stat.S_IMODE((Path(d)/'x.nft').stat().st_mode), 0o644)

    def test_a_world_writable_output_directory_draws_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            args = ['apply', '--output', str(Path(d)/'x.nft'), '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa']
            self.assertEqual(self.run_cli(*args)[2], '')
            os.chmod(d, 0o777)
            code, _, err = self.run_cli(*args)
            self.assertEqual(code, 0)
            self.assertIn('warning:', err)
            self.assertIn('writable by everyone', err)

    def test_other_networks_unreadable_is_a_warning_only_when_the_flags_named_everything(self):
        err = io.StringIO()
        args = fe.parser().parse_args(['plan', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa', '--function-network', '172.30.4.0/24=172.30.4.1@br-bbbbbbbbbbbb', '--no-resolv-conf'])
        with patch.object(fe, 'docker', side_effect=fe.DiscoveryError('daemon down')):
            p = fe.collect(args, err)
        self.assertIn('warning:', err.getvalue())
        self.assertEqual(len(p.outbound), 1)
        args = fe.parser().parse_args(['plan', '--no-resolv-conf'])
        with patch.object(fe, 'docker', side_effect=fe.DiscoveryError('daemon down')), self.assertRaises(fe.PlanError):
            fe.collect(args, io.StringIO())


class RepositoryTests(unittest.TestCase):
    ROOT = Path(fe.__file__).resolve().parent.parent

    def test_the_label_literal_is_still_the_one_the_runtime_puts_on_functions_networks(self):
        runtime = (self.ROOT/'lab'/'durable_runtime.py').read_text()
        self.assertIn("'io.sbarbase.functions='+e", runtime)
        self.assertEqual(fe.FUNCTIONS_LABEL, 'io.sbarbase.functions')

    def test_no_long_dash_in_the_module_the_tests_or_the_script(self):
        for path in (Path(fe.__file__), Path(__file__), self.ROOT/'deploy'/'functions-egress.sh'):
            self.assertNotRegex(path.read_text(), LONG_DASH, str(path))

    def test_a_non_integer_storage_port_variable_is_a_one_line_error_not_a_traceback(self):
        run = subprocess.run([sys.executable, str(self.ROOT/'lab'/'functions_egress.py'), 'plan', '--no-discover', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa'],
                             capture_output=True, text=True, env={**os.environ, 'SBARBASE_FUNCTIONS_STORAGE_PORT': 'abc'})
        self.assertEqual(run.returncode, 2)
        self.assertTrue(run.stderr.startswith('error: SBARBASE_FUNCTIONS_STORAGE_PORT'), run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(run.stdout, '')

    def test_the_script_is_executable_and_names_the_module(self):
        script = self.ROOT/'deploy'/'functions-egress.sh'
        self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
        self.assertIn('lab/functions_egress.py', script.read_text())

    def test_output_is_identical_across_processes_with_different_hash_seeds(self):
        with tempfile.TemporaryDirectory() as d:
            resolv = Path(d)/'resolv.conf'
            resolv.write_text('nameserver 192.168.1.1\nnameserver 192.168.1.2\nnameserver fd00:53::1\nnameserver 10.9.0.2\n')
            command = [sys.executable, str(self.ROOT/'lab'/'functions_egress.py'), 'plan', '--no-discover', '--resolv-conf', str(resolv),
                       '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa', '--outbound-subnet', 'fd00:20::/64',
                       '--function-network', '172.30.5.0/24=172.30.5.1@br-cccccccccccc', '--function-network', '172.30.4.0/24=172.30.4.1@br-bbbbbbbbbbbb',
                       '--allow', '192.168.1.10:587/tcp', '--allow', '192.168.1.10:25/tcp', '--allow', '192.168.1.11:587/udp',
                       '--allow-host', '192.168.1.5:25/tcp', '--allow-host', '192.168.1.5:25/udp']
            outputs = set()
            for seed in ('0', '1', '2', '3', '4', '5'):
                for fmt in ('nft', 'ufw', 'firewalld'):
                    run = subprocess.run(command + ['--format', fmt], capture_output=True, text=True, env={**os.environ, 'PYTHONHASHSEED': seed}, cwd=d)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    outputs.add((fmt, run.stdout))
            self.assertEqual(len(outputs), 3)




class LinkLocalTests(unittest.TestCase):
    def test_link_local_sources_are_dropped_to_the_host_by_interface_with_neighbour_discovery_first(self):
        p = plan(interfaces=['br-aaaaaaaaaaaa', 'br-bbbbbbbbbbbb'])
        rules = [r for r in p.rules if r.src == fe.LINK_LOCAL]
        self.assertEqual([(r.chain, r.note) for r in rules], [('input', 'replies'), ('input', 'neighbour discovery'), ('input', 'no other host access')])
        self.assertEqual({r.iif for r in rules}, {('br-aaaaaaaaaaaa', 'br-bbbbbbbbbbbb')})
        self.assertEqual([r.action for r in rules], ['accept', 'accept', 'drop'])
        nft = fe.render_nft(p)
        self.assertIn('iifname { "br-aaaaaaaaaaaa", "br-bbbbbbbbbbbb" } ip6 saddr fe80::/10 counter drop comment "no other host access"', nft)
        self.assertIn('ip6 hoplimit 255 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert } accept comment "neighbour discovery"', nft)
        self.assertEqual(fe.check(p), [])

    def test_it_applies_to_ipv4_only_networks_and_falls_back_to_every_bridge_with_a_warning(self):
        p = plan(outbound=[OUT4], functions=[FN_A])
        self.assertEqual(p.interfaces, (fe.WILDCARD_INTERFACE,))
        nft = fe.render_nft(p)
        self.assertIn('iifname "br-*" ip6 saddr fe80::/10 counter drop', nft)
        self.assertIn('WARNING: not every bridge interface name is known', nft)
        self.assertNotIn('WARNING: not every bridge interface name is known', fe.render_nft(plan(interfaces=['br-aaaaaaaaaaaa'])))

    def test_only_the_link_local_rules_name_an_interface_and_the_translations_say_what_they_do_with_it(self):
        p = plan(interfaces=['br-aaaaaaaaaaaa'])
        self.assertTrue(all(r.src == fe.LINK_LOCAL for r in p.rules if r.iif))
        self.assertIn("ufw deny in on br-aaaaaaaaaaaa from fe80::/10 to any", fe.render_ufw(p))
        self.assertIn("ufw deny in on br-+ from fe80::/10", fe.render_ufw(plan()))
        firewalld = fe.render_firewalld(p)
        self.assertIn('NOT translated: the drop of link-local IPv6', firewalld)
        self.assertNotIn('br-aaaaaaaaaaaa', '\n'.join(l for l in firewalld.splitlines() if not l.startswith('#')))

    def test_interface_names_come_from_docker_or_the_flag_and_bad_names_are_refused(self):
        self.assertEqual(fe.bridge_interface({'Id': 'a' * 64}), 'br-' + 'a' * 12)
        self.assertEqual(fe.bridge_interface({'Id': 'a' * 64, 'Options': {'com.docker.network.bridge.name': 'docker0'}}), 'docker0')
        self.assertEqual(fe.bridge_interface({'Id': 'short'}), '')
        self.assertEqual(fe.bridge_interface({'Options': {'com.docker.network.bridge.name': 'bad name"; drop'}}), '')
        for bad in ('has space', 'quote"', 'x' * 16, 'a*b'):
            with self.assertRaises(fe.PlanError, msg=bad):
                plan(interfaces=[bad])
        args = fe.parser().parse_args(['plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-feedfeedfeed'])
        self.assertEqual(fe.collect(args, io.StringIO()).interfaces, ('br-feedfeedfeed',))

    def test_discovery_carries_the_bridge_interfaces_into_the_plan(self):
        def fake(*args):
            if args[:2] == ('network', 'ls'):
                return 'f\n' if any('label=' in a for a in args) else 'b\n'
            if args[2] == fe.OUTBOUND_NETWORK:
                return '[{"Id": "' + 'c' * 64 + '", "IPAM": {"Config": [{"Subnet": "172.20.0.0/16", "Gateway": "172.20.0.1"}]}}]'
            return '[{"Name": "n", "Id": "' + 'd' * 64 + '", "IPAM": {"Config": [{"Subnet": "172.30.4.0/24", "Gateway": "172.30.4.1"}]}}]'
        args = fe.parser().parse_args(['plan', '--no-resolv-conf'])
        with patch.object(fe, 'docker', side_effect=fake):
            p = fe.collect(args, io.StringIO())
        self.assertEqual(p.interfaces, ('br-' + 'c' * 12, 'br-' + 'd' * 12))


class SpecialRangeTests(unittest.TestCase):
    def test_allow_entries_that_touch_metadata_loopback_cgnat_or_link_local_are_refused(self):
        bad = ('169.254.169.254:80/tcp', '169.254.0.0/16', '127.0.0.0/8', '127.0.0.1:22/tcp', '0.0.0.0/1', '128.0.0.0/1', '100.64.0.0/10',
               '100.100.100.200:80/tcp', '0.0.0.0/8', '::/1', '8000::/1', 'fe80::/10', '[fe80::1]:53/udp')
        for text in bad:
            allow = fe.parse_allow(text)
            for entry in (allow, allow._replace(proto='tcp', port=80, host=True)):
                with self.assertRaises(fe.PlanError, msg=(text, entry.host)) as ctx:
                    fe.make_plan([OUT4], [], [entry])
                self.assertIn('--allow-special', str(ctx.exception))

    def test_the_override_lifts_the_refusal_and_private_lan_entries_need_none(self):
        p = fe.make_plan([OUT4], [], [fe.parse_allow('169.254.169.254:80/tcp')], allow_special=True)
        self.assertIn('169.254.169.254', fe.render_nft(p))
        fe.make_plan([OUT4], [], [fe.parse_allow('10.1.0.0/16'), fe.parse_allow('192.168.1.10:587/tcp')])
        args = fe.parser().parse_args(['plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa', '--allow', '169.254.169.254:80/tcp'])
        with self.assertRaises(fe.PlanError):
            fe.collect(args, io.StringIO())
        args = fe.parser().parse_args(['plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa', '--allow', '169.254.169.254:80/tcp', '--allow-special'])
        self.assertIn(NET('169.254.169.254'), {r.dst[0] for r in fe.collect(args, io.StringIO()).rules if r.note == 'allow list'})

    def test_a_link_local_resolver_from_resolv_conf_is_still_allowed_on_port_53_only(self):
        p = plan(allow=[], resolvers=fe.parse_resolv('nameserver 169.254.169.253\n'))
        rules = [r for r in p.rules if r.note == 'allow list']
        self.assertTrue(rules and all(r.dst == (NET('169.254.169.253'),) and r.port == 53 for r in rules))


class SubnetDestinationTests(unittest.TestCase):
    def test_functions_and_outbound_subnets_are_destination_drops_even_when_not_private_and_not_discovered(self):
        a = fe.FunctionNet(NET('198.51.100.0/24'), ipaddress.ip_address('198.51.100.1'))
        b = fe.FunctionNet(NET('198.51.101.0/24'), ipaddress.ip_address('198.51.101.1'))
        p = fe.make_plan([NET('203.0.113.0/24')], [a, b])
        drops = {r.src: r.dst for r in p.rules if r.chain == 'forward' and r.action == 'drop'}
        for src in (a.subnet, b.subnet, NET('203.0.113.0/24')):
            for target in (a.subnet, b.subnet, NET('203.0.113.0/24')):
                self.assertTrue(any(target.subnet_of(d) for d in drops[src]), (src, target))
        self.assertEqual(fe.check(p), [])
        accepts = [r for r in p.rules if r.chain == 'forward' and r.note == 'own network']
        self.assertEqual({(r.src, r.dst) for r in accepts}, {(a.subnet, (a.subnet,)), (b.subnet, (b.subnet,))})


# ---------------------------------------------------------------- packets, in a private namespace

NAMESPACE_DRIVER = r"""
import socket, subprocess, sys
def sh(*a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(' '.join(a) + ': ' + r.stderr)
sh('ip', 'link', 'set', 'lo', 'up')
for a in sys.argv[2].split(','):
    sh('ip', 'addr', 'add', a, 'dev', 'lo', *(['nodad'] if ':' in a else []))
sh('nft', '-f', sys.argv[1])
idx = socket.if_nametoindex('lo')
def addr(ip, port):
    return (ip, port, 0, idx if ip.startswith('fe80') else 0) if ':' in ip else (ip, port)
def probe(src, dst, port):
    fam = socket.AF_INET6 if ':' in dst else socket.AF_INET
    srv = socket.socket(fam)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(addr(dst, port)); srv.listen(1)
    c = socket.socket(fam); c.settimeout(0.4)
    try:
        c.bind(addr(src, 0)); c.connect(addr(dst, port)); return 'open'
    except OSError:
        return 'blocked'
    finally:
        c.close(); srv.close()
for line in sys.argv[3:]:
    s, d, p = line.split(',')
    print(line, probe(s, d, int(p)))
"""

HOST = ['172.20.0.1', '172.30.4.1', '172.30.5.1', '192.168.1.5', 'fd00:20::1', 'fe80::1']
PROBES = [
    # (source, destination, port, reachable)
    ('172.30.4.5', '172.30.4.1', 25433, True),    # a function to its own Storage listener
    ('172.30.5.5', '172.30.5.1', 25433, True),
    ('172.30.4.5', '172.30.4.1', 22, False),      # any other port at its own gateway
    ('172.30.4.5', '172.30.5.1', 25433, False),   # one environment to another's listener address
    ('172.20.0.5', '172.20.0.1', 22, False),      # outbound network to the host
    ('172.20.0.5', '172.30.4.1', 25433, False),   # outbound network to a Storage listener
    ('172.20.0.5', '192.168.1.5', 25, True),      # the --allow-host entry
    ('172.20.0.5', '192.168.1.5', 26, False),
    ('fd00:20::5', 'fd00:20::1', 22, False),      # IPv6 unique local source
    ('fe80::5', 'fe80::1', 22, False),            # IPv6 link-local source: matched by interface only
    ('192.0.2.7', '172.20.0.1', 22, True),        # a source outside every given subnet is left alone
    ('172.30.4.1', '172.30.4.1', 22, False),      # the host to its own gateway address: dropped, and disclosed in NOT_COVERED
]


def in_namespace(rules, probes):
    """{(src, dst, port): 'open' or 'blocked'} from connections made inside `unshare -Urn`, with the rules
    loaded by real nft in that private namespace; None when it is not available here."""
    tools = [shutil.which(t) for t in ('unshare', 'nft', 'ip')]
    if not all(tools):
        return None
    with tempfile.TemporaryDirectory() as d:
        (Path(d)/'rules.nft').write_text(rules)
        addresses = sorted({x for s, t, _, _ in probes for x in (s, t)})
        command = [tools[0], '-Urn', sys.executable, '-c', NAMESPACE_DRIVER, str(Path(d)/'rules.nft'),
                   ','.join(a + ('/128' if ':' in a else '/32') for a in addresses)] + [f'{s},{t},{p}' for s, t, p, _ in probes]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if run.returncode:
        return None
    return {tuple(line.rsplit(' ', 1)[0].split(',')): line.rsplit(' ', 1)[1] for line in run.stdout.splitlines()}


class PacketTests(unittest.TestCase):
    """Real nft and real TCP connections over lo in a private network namespace. Interface names cannot
    be the bridges' there, so the link-local rules are generated for lo."""

    @classmethod
    def setUpClass(cls):
        cls.plan = plan(outbound=[OUT4, OUT6], functions=[FN_A, FN_B], allow=[fe.parse_allow('192.168.1.5:25/tcp', host=True)], resolvers=[], interfaces=['lo'])
        cls.rules = fe.render_nft(cls.plan)
        cls.result = in_namespace(cls.rules, PROBES)

    def setUp(self):
        if self.result is None:
            self.skipTest('a private network namespace with nft and ip is not available')

    def test_each_connection_is_open_or_blocked_as_the_rules_say(self):
        for src, dst, port, reachable in PROBES:
            self.assertEqual(self.result[(src, dst, str(port))], 'open' if reachable else 'blocked', (src, dst, port))

    def test_the_host_connecting_to_a_gateway_address_is_dropped_and_the_header_says_so(self):
        self.assertEqual(self.result[('172.30.4.1', '172.30.4.1', '22')], 'blocked')
        self.assertIn('Host processes that connect to a bridge gateway address', self.rules)

    def test_custom_named_bridges_are_disclosed_as_not_covered(self):
        self.assertIn('Bridges with a custom, non br-* name that were not named', self.rules)

    def test_the_harness_sees_the_link_local_hole_when_the_interface_rules_are_removed(self):
        without = '\n'.join(l for l in self.rules.splitlines() if 'iifname' not in l) + '\n'
        before = in_namespace(without, [('fe80::5', 'fe80::1', 22, False), ('fd00:20::5', 'fd00:20::1', 22, False)])
        self.assertEqual(before[('fe80::5', 'fe80::1', '22')], 'open')
        self.assertEqual(before[('fd00:20::5', 'fd00:20::1', '22')], 'blocked')


FORGERY_DRIVER = r"""
import re, socket, subprocess, sys, tempfile, time
def sh(*a, check=True):
    r = subprocess.run(a, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(' '.join(a) + ': ' + r.stderr)
    return r.stdout
child = subprocess.Popen(['unshare', '-n', 'sleep', '60'])
time.sleep(0.3)
pid = str(child.pid)
def c(*a, check=True): return sh('nsenter', '-t', pid, '-n', *a, check=check)
try:
    # H is this namespace (the host), C the child (a function). br-test0/veth1 stand for the bridge and its veth.
    sh('ip', 'link', 'add', 'br-test0', 'type', 'veth', 'peer', 'name', 'veth1', 'netns', pid)
    sh('ip', 'link', 'add', 'lan0', 'type', 'dummy')
    for dev in ('lo', 'br-test0', 'lan0'): sh('ip', 'link', 'set', dev, 'up')
    sh('ip', 'addr', 'add', '10.99.0.1/24', 'dev', 'br-test0'); sh('ip', 'addr', 'add', 'fd99::1/64', 'dev', 'br-test0', 'nodad')
    sh('ip', 'addr', 'add', '198.51.100.1/24', 'dev', 'lan0'); sh('ip', 'addr', 'add', '2001:db8:99::1/64', 'dev', 'lan0', 'nodad')
    open('/proc/sys/net/ipv4/ip_forward', 'w').write('1'); open('/proc/sys/net/ipv6/conf/all/forwarding', 'w').write('1')
    # No kernel reverse-path filtering, so the only defence under test is the generated rules.
    for name in ('all', 'default', 'br-test0', 'lan0'): open('/proc/sys/net/ipv4/conf/' + name + '/rp_filter', 'w').write('0')
    c('ip', 'link', 'set', 'lo', 'up'); c('ip', 'link', 'set', 'veth1', 'up')
    c('ip', 'addr', 'add', '10.99.0.2/24', 'dev', 'veth1'); c('ip', 'addr', 'add', 'fd99::2/64', 'dev', 'veth1', 'nodad')
    c('ip', 'route', 'add', 'default', 'via', '10.99.0.1'); c('ip', '-6', 'route', 'add', 'default', 'via', 'fd99::1')
    c('ip', 'addr', 'add', '203.0.113.9/32', 'dev', 'lo'); c('ip', 'addr', 'add', '10.98.0.5/32', 'dev', 'lo')
    time.sleep(1.2)
    # Resolve neighbours first, so no probe is lost to address resolution.
    c('ping', '-c1', '-W1', '10.99.0.1', check=False); c('ping', '-6', '-c1', '-W1', 'fd99::1', check=False)
    sh('nft', '-f', sys.argv[1])
    probes = (('legit4', 'ip', '10.99.0.2'), ('forged4', 'ip', '203.0.113.9'), ('fn4', 'ip', '10.98.0.5'),
              ('legit6', 'ip6', 'fd99::2'), ('forged6', 'ip6', '2001:db8:ffff::1'))
    obs = 'table inet obs {\nchain post {\ntype filter hook postrouting priority 100; policy accept;\n'
    obs += ''.join('oifname "lan0" ' + fam + ' saddr ' + src + ' counter comment "' + name + '"\n' for name, fam, src in probes)
    f = tempfile.NamedTemporaryFile('w', suffix='.nft', delete=False); f.write(obs + '}\n}\n'); f.close()
    sh('nft', '-f', f.name)
    l4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); l4.bind(('10.99.0.1', 9999)); l4.settimeout(0.4)
    l6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM); l6.bind(('fd99::1', 9999)); l6.settimeout(0.4)
    import json
    def link_local(dev, ns):
        for _ in range(40):    # wait out duplicate address detection
            out = (c if ns else sh)('ip', '-6', '-j', 'addr', 'show', 'dev', dev)
            found = [a['local'] for i in json.loads(out) for a in i['addr_info'] if a['scope'] == 'link' and not a.get('tentative')]
            if found:
                return found[0]
            time.sleep(0.25)
        raise SystemExit('no usable link-local address on ' + dev)
    h_ll, c_ll = link_local('br-test0', False), link_local('veth1', True)
    h_idx = socket.if_nametoindex('br-test0')
    ll = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM); ll.settimeout(0.4)
    for _ in range(40):    # the link-local address is tentative until duplicate address detection ends
        try:
            ll.bind((h_ll, 9999, 0, h_idx)); break
        except OSError:
            time.sleep(0.25)
    else:
        raise SystemExit('link-local address never became usable')
    # IPv6 uses IPV6_FREEBIND (option 78), which needs no capability; IPv4 binds an address held on C's lo.
    sender = ("import socket,sys\nsrc,dst,port=sys.argv[1:4]\nv6=':' in dst\n"
              "s=socket.socket(socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_DGRAM)\n"
              "v6 and s.setsockopt(41,78,1)\ns.bind((src,0))\ns.sendto(b'x',(dst,int(port)))\n")
    def got(sock):
        try:
            sock.recv(10); return 'reached'
        except OSError:
            return 'stopped'
    # A function reaches the host's bridge address from its own fe80:: address, which no subnet rule matches.
    c_idx = c('python3', '-c', 'import socket; print(socket.if_nametoindex("veth1"))').strip()
    llsender = ("import socket,sys\ns=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)\ni=int(sys.argv[3])\n"
                "s.bind((sys.argv[1],0,0,i))\ns.sendto(b'x',(sys.argv[2],9999,0,i))\n")
    c('python3', '-c', llsender, c_ll, h_ll, c_idx); print('input-ll', got(ll))
    # A raw IPv6 datagram whose source is the unspecified address :: (needs CAP_NET_RAW, which this namespace's root has).
    rawsender = "\n".join([
        "import socket, struct, sys",
        "dst = sys.argv[1]",
        "udp = struct.pack('!HHHH', 5555, 9999, 9, 0) + b'x'",
        "src, to = bytes(16), socket.inet_pton(socket.AF_INET6, dst)",
        "data = src + to + struct.pack('!II', len(udp), 17) + udp + bytes(1)",
        "total = sum(struct.unpack('!%dH' % (len(data) // 2), data))",
        "while total >> 16: total = (total & 0xffff) + (total >> 16)",
        "udp = udp[:6] + struct.pack('!H', ~total & 0xffff or 0xffff) + udp[8:]",
        "packet = struct.pack('!IHBB', 0x60000000, len(udp), 17, 64) + src + to + udp",
        "s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)",
        "s.setsockopt(41, 36, 1)",
        "s.sendto(packet, (dst, 0, 0, 0))"])
    c('python3', '-c', rawsender, 'fd99::1'); print('input-unspecified6', got(l6))
    for name, src, dst, sock in (('input-forged4', '203.0.113.9', '10.99.0.1', l4), ('input-forged6', '2001:db8:ffff::1', 'fd99::1', l6)):
        c('python3', '-c', sender, src, dst, '9999'); print(name, got(sock))
    for src, dst in (('10.99.0.2', '198.51.100.2'), ('203.0.113.9', '198.51.100.2'), ('10.98.0.5', '198.51.100.2'),
                     ('fd99::2', '2001:db8:99::2'), ('2001:db8:ffff::1', '2001:db8:99::2')):
        c('python3', '-c', sender, src, dst, '9')
    time.sleep(0.3)
    for n, name in re.findall(r'counter packets (\d+) bytes \d+ comment "(\w+)"', sh('nft', 'list', 'table', 'inet', 'obs')):
        print('forward-' + name, 'reached' if int(n) else 'stopped')
finally:
    child.kill()
"""


def forgery_run(rules):
    """{probe: 'reached' or 'stopped'} from a routed veth model in `unshare -Urn`; None when it cannot run here."""
    tools = [shutil.which(t) for t in ('unshare', 'nft', 'ip', 'nsenter', 'ping')]
    if not all(tools):
        return None
    with tempfile.TemporaryDirectory() as d:
        (Path(d)/'rules.nft').write_text(rules)
        try:
            run = subprocess.run([tools[0], '-Urn', sys.executable, '-c', FORGERY_DRIVER, str(Path(d)/'rules.nft')], capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if run.returncode:
        return None
    return dict(line.split() for line in run.stdout.splitlines())


class ForgeryTests(unittest.TestCase):
    """Source forgery in a routed model: an address outside every listed subnet, and a functions-network address
    used on the outbound bridge. The kernel's own reverse-path filter is switched off in the model."""

    @classmethod
    def setUpClass(cls):
        cls.plan = fe.make_plan([NET('10.99.0.0/24'), NET('fd99::/64')], [fe.FunctionNet(NET('10.98.0.0/24'), ipaddress.ip_address('10.98.0.1'))],
                                interfaces=['br-test0'], interfaces_complete=True)
        cls.rules = fe.render_nft(cls.plan)
        cls.result = forgery_run(cls.rules)

    def setUp(self):
        if self.result is None:
            self.skipTest('a routed network model needs unshare, nft, ip, nsenter and ping in a private namespace')

    def test_forged_and_misplaced_sources_are_stopped_and_legitimate_ones_still_forwarded(self):
        self.assertEqual(self.result, {'input-ll': 'stopped', 'input-unspecified6': 'stopped', 'input-forged4': 'stopped', 'input-forged6': 'stopped', 'forward-legit4': 'reached', 'forward-forged4': 'stopped',
                                       'forward-fn4': 'stopped', 'forward-legit6': 'reached', 'forward-forged6': 'stopped'})

    def test_without_the_reverse_path_check_every_forged_packet_gets_through(self):
        without = '\n'.join(l for l in self.rules.splitlines() if 'fib saddr' not in l) + '\n'
        before = forgery_run(without)
        self.assertEqual(before, {'input-ll': 'stopped', 'input-unspecified6': 'stopped', 'input-forged4': 'reached', 'input-forged6': 'reached', 'forward-legit4': 'reached', 'forward-forged4': 'reached',
                                  'forward-fn4': 'reached', 'forward-legit6': 'reached', 'forward-forged6': 'reached'})


class UnspecifiedSourceTests(ForgeryTests):
    """A raw IPv6 datagram from :: reaches a host socket unless the generated rules drop it."""

    def test_without_the_unspecified_source_drop_the_datagram_is_delivered(self):
        without = '\n'.join(l for l in self.rules.splitlines() if 'ip6 saddr :: ' not in l) + '\n'
        result = forgery_run(without)
        self.assertEqual(result['input-unspecified6'], 'reached')
        self.assertEqual((result['input-forged6'], result['input-ll']), ('stopped', 'stopped'))

    def test_the_unspecified_source_is_dropped_before_the_reverse_path_check_and_duplicate_address_detection_passes(self):
        chain = self.rules[self.rules.index('chain rpf'):].splitlines()
        wanted = ['duplicate address detection', 'unspecified source', 'unspecified source', 'source not routed back here', 'source not routed back here']
        self.assertEqual([re.search(r'comment "([^"]+)"', l).group(1) for l in chain if 'comment' in l], wanted)
        self.assertIn('ip6 saddr :: ip6 hoplimit 255 icmpv6 type nd-neighbor-solicit accept', chain[2])
        self.assertIn('ip saddr 0.0.0.0/8 counter drop', '\n'.join(chain))
        self.assertEqual(self.result['input-unspecified6'], 'stopped')


class ReversePathTests(unittest.TestCase):
    def test_the_check_is_a_prerouting_chain_on_the_bridges_for_both_families(self):
        p = plan(interfaces=['br-aaaaaaaaaaaa', 'br-bbbbbbbbbbbb'])
        nft = fe.render_nft(p)
        chain = nft[nft.index('chain rpf'):]
        self.assertIn('type filter hook prerouting priority -10; policy accept;', chain)
        self.assertEqual(len(re.findall(r'iifname \{ "br-aaaaaaaaaaaa", "br-bbbbbbbbbbbb" \} ip6? saddr != .* fib saddr \. iif oif missing counter drop', chain)), 2)
        self.assertIn('ip6 saddr != { ::, fe80::/10 }', chain)
        self.assertIn('ip saddr != 0.0.0.0', chain)
        self.assertEqual(fe.check(p), [])

    def test_the_check_uses_the_same_wildcard_fallback_and_is_present_without_any_ipv6_subnet(self):
        nft = fe.render_nft(plan(outbound=[OUT4], functions=[]))
        self.assertIn('iifname "br-*" ip6 saddr != { ::, fe80::/10 } fib saddr . iif oif missing', nft)

    def test_check_flags_a_plan_whose_nft_output_lost_the_check(self):
        with patch.object(fe, 'rpf_rules', return_value=[]):
            self.assertTrue(any('reverse-path' in problem for problem in fe.check(plan())))

    def test_the_docstring_and_the_header_state_what_forgery_needs(self):
        doc = fe.__doc__
        self.assertIn('CAP_NET_RAW', doc)
        self.assertIn('cap-drop ALL', doc)
        nft = fe.render_nft(plan())
        self.assertIn('Forging an IPv4 source also needs CAP_NET_RAW or CAP_NET_ADMIN (IP_TRANSPARENT)', nft)
        self.assertIn('an IPv6 source needs no capability', nft)
        self.assertIn('CAP_NET_ADMIN', fe.__doc__)


class TranslationWarningTests(unittest.TestCase):
    ARGS = ['--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa']

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        return fe.run(list(argv), out=out, err=err), out.getvalue(), err.getvalue()

    def test_plan_and_apply_warn_on_stderr_for_ufw_and_firewalld_but_not_for_nft(self):
        with tempfile.TemporaryDirectory() as d:
            for command in (['plan'], ['apply', '--output', str(Path(d)/'x')]):
                code, out, err = self.run_cli(*command, *self.ARGS)
                self.assertEqual((code, err), (0, ''), command)
                code, out, err = self.run_cli(*command, '--format', 'ufw', *self.ARGS)
                self.assertIn('warning: the ufw output is an untested translation', err)
                self.assertIn('probably inert', err)
                self.assertIn('not verified here', err)
                self.assertNotIn('warning', out)
                code, out, err = self.run_cli(*command, '--format', 'firewalld', *self.ARGS)
                self.assertIn('omits the link-local IPv6 drop and the reverse-path check', err)
                self.assertIn('target ACCEPT', err)


class IPv6GatewayTests(unittest.TestCase):
    def test_no_storage_accept_is_generated_for_an_ipv6_gateway_but_the_network_is_still_dropped(self):
        v6 = fe.FunctionNet(NET('fd00:31::/64'), ipaddress.ip_address('fd00:31::1'))
        p = fe.make_plan([OUT4, OUT6], [FN_A, v6])
        storage = [r for r in p.rules if r.note == 'functions to Storage listener']
        self.assertEqual([r.src for r in storage], [FN_A.subnet])
        self.assertTrue(any(r.action == 'drop' and r.chain == 'input' and r.src == v6.subnet for r in p.rules))
        self.assertNotIn('ip6 saddr fd00:31::/64 ip6 daddr fd00:31::1', fe.render_nft(p))
        self.assertEqual(fe.check(p), [])


class PartialInterfaceTests(unittest.TestCase):
    """The bridge name list may be partial; a bridge no one named must still be matched."""
    BLOCKED = {'input-ll': 'stopped', 'input-unspecified6': 'stopped', 'input-forged4': 'stopped', 'input-forged6': 'stopped', 'forward-legit4': 'reached', 'forward-forged4': 'stopped',
               'forward-fn4': 'stopped', 'forward-legit6': 'reached', 'forward-forged6': 'stopped'}
    SUBNETS = ([NET('10.99.0.0/24'), NET('fd99::/64')], [fe.FunctionNet(NET('10.98.0.0/24'), ipaddress.ip_address('10.98.0.1'), 'fn', 'other-fn')])

    def model(self, **kw):
        result = forgery_run(fe.render_nft(fe.make_plan(*self.SUBNETS, **kw)))
        if result is None:
            self.skipTest('a routed network model needs unshare, nft, ip, nsenter and ping in a private namespace')
        return result

    def test_a_plan_with_any_unnamed_network_always_adds_the_wildcard(self):
        outbound, functions = self.SUBNETS
        for names, complete, expect in ((['br-test0', 'other-fn'], True, ('br-test0', 'other-fn')), (['other-fn'], False, ('br-*', 'other-fn')),
                                        ([], False, ('br-*',)), (['br-aaaaaaaaaaaa'], False, ('br-*',)), ([], True, ('br-*',))):
            p = fe.make_plan(outbound, functions, interfaces=names, interfaces_complete=complete)
            self.assertEqual(p.interfaces, expect, (names, complete))
        nft = fe.render_nft(fe.make_plan(outbound, functions, interfaces=['other-fn']))
        self.assertIn('iifname { "br-*", "other-fn" } ip6 saddr fe80::/10 counter drop', nft)
        self.assertIn('iifname { "br-*", "other-fn" } ip saddr != 0.0.0.0/8 fib saddr', nft)
        self.assertIn('not every bridge interface name is known', nft)

    def test_collect_names_the_networks_without_an_interface_and_warns_and_no_option_removes_the_wildcard(self):
        err = io.StringIO()
        args = fe.parser().parse_args(['plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16', '--function-network', '172.30.4.0/24=172.30.4.1@br-bbbbbbbbbbbb',
                                       '--bridge-interface', 'br-aaaaaaaaaaaa'])
        p = fe.collect(args, err)
        self.assertEqual(p.interfaces, ('br-*',))
        self.assertIn('no bridge interface name known for 172.20.0.0/16', err.getvalue())
        self.assertNotIn('172.30.4.0/24', err.getvalue())
        err = io.StringIO()
        args = fe.parser().parse_args(['plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-aaaaaaaaaaaa'])
        self.assertEqual(fe.collect(args, err).interfaces, ('br-aaaaaaaaaaaa',))
        self.assertEqual(err.getvalue(), '')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            fe.parser().parse_args(['plan', '--interfaces-complete'])
        self.assertNotIn('interfaces-complete', fe.__doc__ + fe.parser().format_help())

    def test_the_interface_can_be_written_into_the_subnet_flags(self):
        self.assertEqual(fe.parse_outbound('10.0.0.0/16=br-x'), (NET('10.0.0.0/16'), 'br-x'))
        self.assertEqual(fe.parse_outbound('10.0.0.0/16'), (NET('10.0.0.0/16'), ''))
        fn = fe.parse_function_network('fd00:31::/64=@br-y')
        self.assertEqual((fn.subnet, fn.gateway, fn.iface), (NET('fd00:31::/64'), None, 'br-y'))
        self.assertEqual(fe.parse_function_network('10.1.0.0/24=10.1.0.1').iface, '')
        for bad in ('10.0.0.0/16=', '10.0.0.0/16=bad name', '10.0.0.0/16=' + 'x' * 16):
            with self.assertRaises(fe.PlanError, msg=bad):
                fe.parse_outbound(bad)
        with self.assertRaises(fe.PlanError):
            fe.parse_function_network('10.1.0.0/24=10.1.0.1@')

    def test_discovery_that_names_every_network_needs_no_wildcard(self):
        def fake(*args):
            if args[:2] == ('network', 'ls'):
                return 'f\n' if any('label=' in a for a in args) else 'b\n'
            if args[2] == fe.OUTBOUND_NETWORK:
                return '[{"Id": "' + 'c' * 64 + '", "IPAM": {"Config": [{"Subnet": "172.20.0.0/16", "Gateway": "172.20.0.1"}]}}]'
            return '[{"Name": "n", "Id": "' + 'd' * 64 + '", "IPAM": {"Config": [{"Subnet": "172.30.4.0/24", "Gateway": "172.30.4.1"}]}}]'
        err = io.StringIO()
        with patch.object(fe, 'docker', side_effect=fake):
            p = fe.collect(fe.parser().parse_args(['plan', '--no-resolv-conf']), err)
        self.assertEqual(p.interfaces, ('br-' + 'c' * 12, 'br-' + 'd' * 12))
        self.assertEqual(err.getvalue(), '')
        # The outbound network by flag, the functions networks discovered: the outbound one has no name.
        with patch.object(fe, 'docker', side_effect=fake):
            p = fe.collect(fe.parser().parse_args(['plan', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16']), io.StringIO())
        self.assertEqual(p.interfaces, ('br-*',))

    def test_outbound_by_flag_with_discovered_function_networks_still_covers_the_outbound_bridge(self):
        # The outbound subnet is given by flag (no name); the functions network is discovered with a name that is
        # not the model's bridge. Only the wildcard can cover the outbound bridge br-test0.
        def fake(*args):
            if args[:2] == ('network', 'ls'):
                return 'f\n' if any('label=' in a for a in args) else ''
            return ('[{"Name": "n", "Id": "' + 'd' * 64 + '", "Options": {"com.docker.network.bridge.name": "other-fn"}, '
                    '"IPAM": {"Config": [{"Subnet": "10.98.0.0/24", "Gateway": "10.98.0.1"}]}}]')
        args = fe.parser().parse_args(['plan', '--no-resolv-conf', '--outbound-subnet', '10.99.0.0/24', '--outbound-subnet', 'fd99::/64'])
        err = io.StringIO()
        with patch.object(fe, 'docker', side_effect=fake):
            p = fe.collect(args, err)
        self.assertEqual(p.interfaces, ('br-*', 'other-fn'))
        self.assertIn('no bridge interface name known for 10.99.0.0/24', err.getvalue())
        result = forgery_run(fe.render_nft(p))
        if result is None:
            self.skipTest('a routed network model needs unshare, nft, ip, nsenter and ping in a private namespace')
        self.assertEqual(result, self.BLOCKED)

    def test_all_named_none_named_and_partly_named_all_stop_link_local_and_forged_sources(self):
        self.assertEqual(self.model(interfaces=['br-test0', 'other-fn'], interfaces_complete=True), self.BLOCKED)
        self.assertEqual(self.model(), self.BLOCKED)
        self.assertEqual(self.model(interfaces=['other-fn']), self.BLOCKED)

    def test_a_partial_name_list_without_the_wildcard_left_the_unnamed_bridge_open(self):
        result = self.model(interfaces=['other-fn'], interfaces_complete=True)
        self.assertEqual((result['input-ll'], result['input-forged4'], result['input-forged6'], result['forward-forged6']), ('reached', 'reached', 'reached', 'reached'))


class DualStackTests(unittest.TestCase):
    def test_a_non_host_allow_with_ipv4_and_ipv6_networks_works_from_flags(self):
        args = fe.parser().parse_args(['plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-a', '--outbound-subnet', 'fd97::/64=br-a',
                                       '--function-network', '172.30.4.0/24=172.30.4.1@br-b', '--function-network', 'fd98::/64=fd98::1@br-b',
                                       '--allow', '192.168.1.10:587/tcp', '--allow', '[fd00:53::1]:53/udp'])
        p = fe.collect(args, io.StringIO())
        nft = fe.render_nft(p)
        self.assertIn('ip daddr 192.168.1.10 tcp dport 587 accept', nft)
        self.assertIn('ip6 daddr fd00:53::1 udp dport 53 accept', nft)
        self.assertEqual(fe.check(p), [])

    def test_a_non_host_allow_with_a_docker_shim_that_returns_both_families(self):
        def fake(*args):
            if args[:2] == ('network', 'ls'):
                return 'f\n' if any('label=' in a for a in args) else 'b\n'
            if args[2] == fe.OUTBOUND_NETWORK:
                return ('[{"Id": "' + 'c' * 64 + '", "IPAM": {"Config": [{"Subnet": "172.20.0.0/16", "Gateway": "172.20.0.1"}, '
                        '{"Subnet": "fd97::/64", "Gateway": "fd97::1"}]}}]')
            return ('[{"Name": "n", "Id": "' + 'd' * 64 + '", "IPAM": {"Config": [{"Subnet": "172.30.4.0/24", "Gateway": "172.30.4.1"}, '
                    '{"Subnet": "fd98::/64", "Gateway": "fd98::1"}, {"Subnet": "2001:db8:5::/64", "Gateway": "2001:db8:5::1"}]}}]')
        args = fe.parser().parse_args(['plan', '--no-resolv-conf', '--allow', '192.168.1.10:587/tcp', '--allow', '[2001:db8:77::1]:25/tcp'])
        with patch.object(fe, 'docker', side_effect=fake):
            p = fe.collect(args, io.StringIO())
        self.assertEqual(fe.check(p), [])
        self.assertIn('ip6 daddr 2001:db8:77::1 tcp dport 25 accept', fe.render_nft(p))
        # A clash is still found, and named, across families.
        clash = fe.parser().parse_args(['plan', '--no-resolv-conf', '--allow', 'fd98::/64'])
        with patch.object(fe, 'docker', side_effect=fake), self.assertRaises(fe.PlanError) as ctx:
            fe.collect(clash, io.StringIO())
        self.assertIn('fd98::', str(ctx.exception))

    def test_addresses_of_both_families_sort_without_error(self):
        mixed = [ipaddress.ip_address(x) for x in ('fd00::1', '10.0.0.1', '2001:db8::1', '192.168.0.1')]
        self.assertEqual([str(a) for a in sorted(mixed, key=fe.address_key)], ['10.0.0.1', '192.168.0.1', '2001:db8::1', 'fd00::1'])

    def test_the_gateway_may_not_be_the_network_or_broadcast_address(self):
        for bad in ('10.9.0.0/24=10.9.0.0', '10.9.0.0/24=10.9.0.255', 'fd98::/64=fd98::'):
            with self.assertRaises(fe.PlanError, msg=bad):
                fe.parse_function_network(bad)
        self.assertEqual(fe.parse_function_network('10.9.0.0/24=10.9.0.1').gateway, ipaddress.ip_address('10.9.0.1'))
        self.assertEqual(fe.parse_function_network('10.9.0.0/31=10.9.0.0').gateway, ipaddress.ip_address('10.9.0.0'))


class ScopeIdTests(unittest.TestCase):
    BASE = ['--no-discover', '--no-resolv-conf', '--outbound-subnet', '172.20.0.0/16=br-a']

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        return fe.run(list(argv), out=out, err=err), out.getvalue(), err.getvalue()

    def test_every_flag_refuses_an_ipv6_scope_id_with_a_clear_message(self):
        cases = (['--allow', '2001:db8::1%eth0'], ['--allow', '[2001:db8::1%eth0]:53/udp'], ['--allow', '2001:db8::/64%eth0'],
                 ['--allow-host', '[fe80::1%eth0]:53/udp'], ['--allow-host', '2001:db8::1%eth0:25/tcp'],
                 ['--deny', '2001:db8::%eth0/64'], ['--function-network', 'fd98::%eth0/64=fd98::1@br-b'],
                 ['--function-network', 'fd98::/64=fd98::1%eth0@br-b'])
        for extra in cases:
            code, out, err = self.run_cli('plan', *self.BASE, *extra)
            self.assertEqual((code, out), (2, ''), extra)
            self.assertIn('scope id', err, extra)
        code, out, err = self.run_cli('plan', '--no-discover', '--no-resolv-conf', '--outbound-subnet', 'fd00::%eth0/64=br-a')
        self.assertEqual((code, out), (2, ''))
        self.assertIn('scope id', err)

    def test_the_parsers_refuse_it_directly(self):
        for call, text in ((fe.parse_network, 'fd00::%x/64'), (fe.parse_subnet, 'fd00::%x/64'), (fe.parse_deny, 'fd00::%x/64'),
                           (fe.parse_allow, 'fe80::1%x'), (fe.parse_outbound, 'fd00::%x/64=br-a'), (fe.parse_function_network, 'fd00::%x/64=fd00::1')):
            with self.assertRaises(fe.PlanError, msg=text) as ctx:
                call(text, 'x') if call is fe.parse_network else call(text)
            self.assertIn('scope id', str(ctx.exception))
        with self.assertRaises(fe.PlanError):
            fe.parse_gateway('fd98::1%eth0', NET('fd98::/64'))

    def test_a_scoped_nameserver_is_used_without_the_scope_and_never_written_with_it(self):
        found = fe.parse_resolv('nameserver fe80::1%eth0\nnameserver 192.168.1.1\n')
        self.assertEqual([str(a) for a in found], ['fe80::1', '192.168.1.1'])
        self.assertNotIn('%', fe.render_nft(plan(resolvers=found)))

    def test_apply_never_writes_a_file_nft_rejects(self):
        with tempfile.TemporaryDirectory() as d, patch.object(fe, 'nft_syntax_check', return_value=('failed', 'Error: syntax error')):
            code, out, err = self.run_cli('apply', '--output', str(Path(d)/'x.nft'), *self.BASE)
            self.assertEqual((code, out), (2, ''))
            self.assertIn('refusing to write rules that nft rejects', err)
            self.assertEqual(list(Path(d).iterdir()), [])
        with tempfile.TemporaryDirectory() as d, patch.object(fe, 'nft_syntax_check', return_value=('skipped', 'nft is not installed')):
            code, out, err = self.run_cli('apply', '--output', str(Path(d)/'x.nft'), *self.BASE)
            self.assertEqual(code, 0)
            self.assertIn('was not checked with nft', err)
            self.assertTrue((Path(d)/'x.nft').exists())

    def test_the_real_nft_accepts_what_apply_writes_when_it_can_be_run(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, err = self.run_cli('apply', '--output', str(Path(d)/'x.nft'), *self.BASE, '--allow', '2001:db8::1:53/udp')
            self.assertEqual(code, 0, err)
            status, detail = fe.nft_syntax_check((Path(d)/'x.nft').read_text())
            if status == 'skipped':
                self.skipTest(detail)
            self.assertEqual(status, 'ok', detail)


class AllowMessageTests(unittest.TestCase):
    def test_host_bits_in_an_allow_entry_are_reported_as_such(self):
        for text in ('100.64.0.0/9', '192.168.1.5/24', 'fd00::1/64'):
            with self.assertRaises(fe.PlanError, msg=text) as ctx:
                fe.parse_allow(text)
            self.assertIn('host bits set', str(ctx.exception))
            self.assertNotIn('empty', str(ctx.exception))
        with self.assertRaises(fe.PlanError) as ctx:
            fe.parse_allow('not-an-address')
        self.assertNotIn('empty', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
