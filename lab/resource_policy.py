"""Resource tier table for the sbarbase installation.

Design source: docs/RESOURCE-POLICY.md section 3.1 ("One table, one module"),
read at commit 46d8d46 on 2026-09-21. Every value below is copied from that
table. The table is a policy, not a measurement: section 3.1 says the numbers
must be measured before they are trusted, and section 7 step 8 is the run that
would do it. Nothing here has been calibrated.

Two values the design left open, and the smallest honest choice made here:

- The design gives the maintenance class "cpus: unchanged from today" and
  "memory: unchanged from today". Those two are represented as None, which means
  this table does not override the memory and CPU values the target runtime
  already launches with. Inventing a number here would have been a preference,
  not a policy.
- The design gives maintenance "pids 128 / 64" for containers and export
  helpers. Only the container figure (128) is in the table; the repository's
  durable runtime does not launch a maintenance export helper, so a second row
  would be an unused invention. It is an open item, not a silent omission.

The Docker mapping this table assumes is INFERRED and not yet verified: section
3.1 notes that --cpu-shares maps to the cgroup v2 cpu.weight and --blkio-weight
to io.weight, and that section 7 step 3 must read both from inside a created
container before the numbers are trusted. No such reading exists yet.

The class label is what a container carries in the Docker label
io.sbarbase.tier. Class names are exactly the five in the design: system,
production, experimental, operator, maintenance. A row id, not a class name, is
what a launch site passes here, because the design gives the system class three
different memory and CPU rows.

No per-environment class field exists in the runtime state yet, so an
environment's Auth and REST launch under the production row, which is the
worked example in section 3.2. Making the class configurable, and therefore
using the experimental row, is an open item.
"""
from collections import namedtuple

# Revision id for docs/RESOURCE-POLICY.md section 4.2. No evidence file records
# it yet because the evidence increment is not built.
POLICY_REVISION = 'resource-policy-2026-09-21'

Tier = namedtuple('Tier', 'label shares weight cpus memory pids')

# Class names, exactly as the design's table header names them.
CLASSES = ('system', 'production', 'experimental', 'operator', 'maintenance')

TIERS = {
    'system.db': Tier('system', 2048, 800, 1, '1024m', 128),
    'system.storage': Tier('system', 2048, 800, .5, '512m', 128),
    'system.management-auth': Tier('system', 1024, 500, .25, '256m', 128),
    'production': Tier('production', 512, 400, .25, '256m', 128),
    'experimental': Tier('experimental', 128, 100, .25, '256m', 128),
    'operator.studio': Tier('operator', 1024, 500, .5, '512m', 128),
    'operator.meta': Tier('operator', 1024, 500, .25, '128m', 128),
    'maintenance': Tier('maintenance', 512, 400, None, None, 128),
}


class ResourcePolicyError(RuntimeError):
    """A launch or an inspection asked for a tier the policy cannot answer."""

    def __init__(self, reason):
        super().__init__('resource policy refused: ' + reason)
        self.reason = reason


def refusal(tier):
    """Return the policy reason a tier cannot produce flags, or None."""
    entry = TIERS.get(tier)
    if entry is None:
        return 'unknown_tier'
    if type(entry.weight) is not int or entry.weight <= 0:
        return 'missing_weight'
    if type(entry.shares) is not int or entry.shares <= 0:
        return 'missing_shares'
    return None


def container_flags(tier):
    """Return the container flags for a tier, or raise ResourcePolicyError.

    cpus and memory are None for a tier the design leaves unchanged at the
    launch site; the caller keeps the value it already passes.
    """
    reason = refusal(tier)
    if reason:
        raise ResourcePolicyError(reason)
    entry = TIERS[tier]
    return {'tier': tier, 'label': entry.label, 'shares': entry.shares, 'weight': entry.weight,
            'cpus': entry.cpus, 'memory': entry.memory, 'pids': entry.pids}


def known_label(value):
    """True when a container's io.sbarbase.tier label names a class in the table."""
    return value in CLASSES


def labels(tier):
    """The placement flags for a container another module launches at its own limits.

    The recovery targets are created by their own scripts, which pass their own
    memory and CPU values, so the row supplies only what they cannot know: the
    class label and the two relative weights. A counted container without the
    label is refused by lab/combined_admission.py, so every owner labelled site
    has to carry it.
    """
    entry = TIERS.get(tier)
    if entry is None:
        raise ResourcePolicyError('unknown_tier')
    return ['--label', 'io.sbarbase.tier=' + entry.label,
            '--cpu-shares', str(entry.shares), '--blkio-weight', str(entry.weight)]
