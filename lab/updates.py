"""The update channel on the supervisor's side: settings, the console's requests, the automatic
decision and the notifications an upgrade's outcome earns.

The console process cannot run an upgrade: `lab/upgrade.py start` moves the checkout under the
running console, and only the supervisor can stop everything and exit so the service manager
starts the new version. So the console (src/control/updates.ts) writes a request and the
supervisor (lab/dev.py `schedule_updates`) carries it out, checking everything again itself.

Files, all in .lab/upgrades, private (0600), each replaced atomically:

  settings.json      operator settings, written by the console: {check, automatic, window}
  request.json       the one request under way: created exclusively (a hard link that fails when
                     the file exists), so the console and the automatic mode never both win;
                     only the supervisor changes it afterwards
  last-request.json  the last finished request, kept for the console's progress view
  check.json         the last check: {attempted_at, error, failures}
  ledger.json        versions announced, tried automatically and rolled back:
                     {announced, attempted, rolled_back}, so automatic mode never retries one
  current.json       what runs now and whether the console may roll back, written by the
                     supervisor: {version, commit, written_at, rollback: {started_at, possible, reason}}
  available.json     the last check's result, written by lab/release_channel.py

A request is {id, kind: apply|rollback|check, version?, tag?, trigger: console|automatic,
state: requested|running|done|failed, requested_at, started_at?, finished_at?, detail?}.
"""
import datetime
import json
import os
import re
import uuid
from pathlib import Path

import notification_producers

ROOT = Path(__file__).resolve().parents[1]
UPGRADES = ROOT / '.lab' / 'upgrades'
DEFAULT_SETTINGS = {'check': True, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}}
CLOCK = re.compile(r'([01]\d|2[0-3]):[0-5]\d')
KINDS = ('apply', 'rollback', 'check')
OPEN = ('requested', 'running')
FINAL = ('done', 'failed')
# A request nobody picked up for this long (the supervisor was down) is not carried out later.
REQUEST_TTL = datetime.timedelta(hours=1)
CHECK_INTERVAL = datetime.timedelta(hours=6)
# The first check waits a little after the supervisor starts, so a start stays light.
FIRST_CHECK_DELAY = datetime.timedelta(minutes=5)
# After a failed check (an offline host): 30 minutes, then 1, 2 and 4 hours, then the interval.
RETRY_BASE = datetime.timedelta(minutes=30)
# release_channel.check() does not fail when the source is unreachable: it says so here.
UNREACHABLE = 'The release source could not be read'
LEDGER_KEEP = 50
# The sentences the console shows as they are. src/control/updates.ts answers 409 with the
# same words; lab/test_updates.py keeps the two in step.
MESSAGES = {
    'busy': 'Another update request is still in progress. Wait for it to finish.',
    'nothing': 'No newer release is available to install. Check for updates first.',
    'stale': 'The last check was made on another version of Sbarbase. Check for updates again.',
    'other': 'That version is not the release available now. Check for updates again.',
    'class': 'Only a safe release can be installed from the console. Follow the instructions on the Updates page to install this one on the server.',
    'unsigned': 'This release is not signed by a Sbarbase release key, so it cannot be installed.',
    'refused': 'The server cannot install this release now. The reasons are listed on the Updates page.',
    'pending': 'The last update has not finished starting yet. Wait for it to finish.',
    'no_rollback': 'There is no installed update to roll back.',
    'expired': 'The server did not pick up this request within an hour, so it was not carried out.',
    'interrupted': 'Sbarbase stopped while this request was running, before it finished.',
}


def path(name):
    return UPGRADES / name


def now():
    """Aware local time: the maintenance window is in the server's local time."""
    return datetime.datetime.now().astimezone()


def stamp(moment):
    return moment.isoformat(timespec='seconds')


def parse_time(value):
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.UTC)


def read(name):
    """A JSON file of this directory, or None when it is missing or not JSON."""
    try:
        return json.loads(path(name).read_text())
    except (OSError, ValueError):
        return None


def write(name, value):
    """Replaces a file atomically: private temporary file, fsync, rename, fsync the directory."""
    target = path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    sync(target.parent)


def sync(directory):
    handle = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


# ---------------------------------------------------------------- settings

def validate_settings(value):
    """The settings exactly as the console may send them, or raises ValueError with a sentence."""
    if not isinstance(value, dict) or sorted(value) != ['automatic', 'check', 'window']:
        raise ValueError('Settings need exactly check, automatic and window.')
    if not isinstance(value['check'], bool) or not isinstance(value['automatic'], bool):
        raise ValueError('Check and automatic must each be on or off.')
    window = value['window']
    if not isinstance(window, dict) or sorted(window) != ['end', 'start']:
        raise ValueError('The maintenance window needs a start and an end.')
    if not all(isinstance(window[name], str) and CLOCK.fullmatch(window[name]) for name in ('start', 'end')):
        raise ValueError('The maintenance window needs a valid start and end time.')
    if window['start'] == window['end']:
        raise ValueError('The maintenance window needs different start and end times.')
    if value['automatic'] and not value['check']:
        raise ValueError('Automatic updates need checking for new releases turned on.')
    return {'check': value['check'], 'automatic': value['automatic'],
            'window': {'start': window['start'], 'end': window['end']}}


def load_settings():
    """The saved settings; the defaults when there are none, and when the file is not valid
    (so a damaged file can never turn automatic updates on)."""
    try:
        return validate_settings(read('settings.json'))
    except ValueError:
        return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(value):
    value = validate_settings(value)
    write('settings.json', value)
    return value


def minutes(clock):
    hours, minute = clock.split(':')
    return int(hours) * 60 + int(minute)


def in_window(window, moment):
    """True inside [start, end) of the local time of day. A window whose end is earlier than
    its start crosses midnight: 23:00 to 01:00 holds 23:30 and 00:30."""
    start, end, at = minutes(window['start']), minutes(window['end']), moment.hour * 60 + moment.minute
    if start < end:
        return start <= at < end
    return at >= start or at < end


# ---------------------------------------------------------------- ledger

def ledger():
    value = read('ledger.json')
    value = value if isinstance(value, dict) else {}
    return {name: [item for item in value.get(name) or [] if isinstance(item, str)]
            for name in ('announced', 'attempted', 'rolled_back')}


def remember(name, version):
    """Adds a version to one list of the ledger; True when it was not there yet."""
    record = ledger()
    if version in record[name]:
        return False
    record[name] = (record[name] + [version])[-LEDGER_KEEP:]
    write('ledger.json', record)
    return True


def label(state):
    """The version an upgrade record names: its release version, else the target commit."""
    release = (state or {}).get('release')
    if isinstance(release, dict) and isinstance(release.get('version'), str):
        return release['version']
    return str((state or {}).get('to', ''))[:12]


# ---------------------------------------------------------------- requests

def read_request():
    value = read('request.json')
    return value if isinstance(value, dict) and value.get('kind') in KINDS else None


def create_request(kind, version=None, tag=None, trigger='console', moment=None):
    """Creates the one request, or returns None when another exists. The file is linked into
    place, which fails when it exists: nothing can overwrite a request under way."""
    if kind not in KINDS:
        raise ValueError('Unknown request kind')
    request = {'id': str(uuid.uuid4()), 'kind': kind, 'trigger': trigger, 'state': 'requested',
               'requested_at': stamp(moment or now())}
    if version:
        request.update(version=version, tag=tag or 'v' + version)
    target = path('request.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temporary = target.with_name(f'.request.{uuid.uuid4().hex}.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(request, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return None
    finally:
        temporary.unlink(missing_ok=True)
    sync(target.parent)
    return request


def update_request(request, **changes):
    """Records a step of the request that is under way; never touches another request."""
    current = read_request()
    if not current or current.get('id') != request.get('id'):
        return None
    current.update(changes)
    write('request.json', current)
    return current


def finish_request(request, state, detail=None, moment=None):
    """Records the outcome, keeps it as the last request and frees the slot for the next one."""
    if state not in FINAL:
        raise ValueError('Unknown final state')
    current = read_request()
    if current and current.get('id') == request.get('id'):
        request = current
    final = {**request, 'state': state, 'finished_at': stamp(moment or now())}
    if detail:
        final['detail'] = detail
    elif 'detail' in final:
        del final['detail']
    if current and current.get('id') == request.get('id'):
        write('request.json', final)
    write('last-request.json', final)
    if current and current.get('id') == request.get('id'):
        path('request.json').unlink(missing_ok=True)
        sync(path('request.json').parent)
    return final


def progressed(request, state):
    """Whether the upgrade state shows that a request which was running went through."""
    if not isinstance(state, dict):
        return False
    since = parse_time(request.get('started_at') or request.get('requested_at'))
    if since is None:
        return False
    if request['kind'] == 'apply':
        # upgrade.py saves the record right before the checkout moves; a start that stopped
        # before that point changed nothing, and one that failed says so.
        began = parse_time(state.get('started_at'))
        return began is not None and began >= since and state.get('phase') != 'failed'
    if request['kind'] == 'rollback':
        went = parse_time(state.get('rollback_at'))
        return went is not None and went >= since
    return False


def settle(upgrade_state, moment=None):
    """Called once when the supervisor starts. A request that was running when the previous
    process ended is done when the upgrade state shows it went through (an apply that moved the
    checkout, then the restart), else failed. A request nobody picked up within REQUEST_TTL is
    failed rather than carried out long after it was asked for. A finished request still in the
    slot (a crash between the two writes of finish_request) is moved out."""
    moment = moment or now()
    request = read_request()
    if request is None:
        if path('request.json').exists():
            # Not a request this version understands: keep it as the last one and free the slot.
            path('request.json').unlink(missing_ok=True)
        return None
    if request.get('state') in FINAL:
        return finish_request(request, request['state'], request.get('detail'), moment)
    if request.get('state') == 'running':
        if progressed(request, upgrade_state):
            return finish_request(request, 'done', None, moment)
        return finish_request(request, 'failed', MESSAGES['interrupted'], moment)
    asked = parse_time(request.get('requested_at'))
    if asked is None or moment - asked > REQUEST_TTL:
        return finish_request(request, 'failed', MESSAGES['expired'], moment)
    return request


# ---------------------------------------------------------------- decisions

def fresh(document, current):
    """The check result when it was made on the running commit; a check made before an upgrade
    still names the release that was just installed."""
    if not isinstance(document, dict) or not isinstance(current, dict):
        return None
    made_on = document.get('current')
    if not isinstance(made_on, dict) or made_on.get('commit') != current.get('commit'):
        return None
    return document


def apply_refusal(version, document, current, upgrade_state):
    """Why an apply of `version` must not go ahead, or None. Asked by the supervisor before it
    runs `upgrade.py start --release`, which then verifies everything again from the source."""
    if isinstance(upgrade_state, dict) and upgrade_state.get('phase') in ('applied', 'rolling_back'):
        return MESSAGES['pending']
    if not isinstance(document, dict) or not isinstance(document.get('available'), dict):
        return MESSAGES['nothing']
    if fresh(document, current) is None:
        return MESSAGES['stale']
    release = document['available']
    if release.get('version') != version:
        return MESSAGES['other']
    if release.get('class') != 'safe':
        return MESSAGES['class']
    if release.get('signed') is not True:
        return MESSAGES['unsigned']
    if document.get('refusals'):
        return MESSAGES['refused']
    return None


def automatic_release(settings, document, current, upgrade_state, record, moment, backup_running):
    """The release automatic mode applies now, or None. Safe, signed and without refusals only,
    inside the window, never while a backup runs, and never a version tried before or rolled
    back (at most one attempt per version)."""
    if not (settings['check'] and settings['automatic']) or backup_running:
        return None
    if not in_window(settings['window'], moment):
        return None
    release = (document or {}).get('available') if isinstance(document, dict) else None
    if not isinstance(release, dict) or not isinstance(release.get('version'), str):
        return None
    version = release['version']
    if version in record['attempted'] or version in record['rolled_back']:
        return None
    if isinstance(upgrade_state, dict) and label(upgrade_state) == version \
            and upgrade_state.get('phase') in ('rolling_back', 'rolled_back', 'rollback_failed'):
        return None
    if apply_refusal(version, document, current, upgrade_state):
        return None
    return release


def rollback_verdict(upgrade_state):
    """(possible, reason) for the console's "roll back": only a confirmed upgrade, and only when
    `upgrade.py rollback` itself would not refuse (upgrade.rollback_refusal)."""
    if not isinstance(upgrade_state, dict) or upgrade_state.get('phase') != 'confirmed':
        return False, MESSAGES['no_rollback']
    import upgrade
    try:
        refusal = upgrade.rollback_refusal(upgrade_state)
    except upgrade.UpgradeError as error:
        refusal = str(error)
    if refusal:
        return False, refusal if refusal.endswith('.') else refusal + '.'
    return True, None


def check_due(record, moment, since, document=None, current=None):
    """Whether a periodic check is due. Never within FIRST_CHECK_DELAY of the supervisor start.
    Then every CHECK_INTERVAL, sooner after a failure (backing off) and as soon as the last result
    was made on another version (right after an upgrade)."""
    if moment < since + FIRST_CHECK_DELAY:
        return False
    record = record if isinstance(record, dict) else {}
    last = parse_time(record.get('attempted_at'))
    if last is None:
        return True
    if document is not None and current is not None and fresh(document, current) is None:
        return True
    failures = record.get('failures') if isinstance(record.get('failures'), int) else 0
    wait = min(CHECK_INTERVAL, RETRY_BASE * 2 ** (failures - 1)) if failures > 0 else CHECK_INTERVAL
    return moment >= last + wait


def meaningful(text, limit=400):
    """The sentence a failed child leaves for the console: its refusals when it printed any,
    else its last line, without the bare "Nothing was changed" that closes most refusals."""
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    unchanged = any(line.rstrip('.') == 'Nothing was changed' for line in lines)
    lines = [line for line in lines if line.rstrip('.') != 'Nothing was changed']
    refused = [line[len('refused'):].strip() for line in lines if line.startswith('refused ')]
    detail = '; '.join(refused) if refused else lines[-1] if lines else 'It stopped without saying why'
    sentence = detail.rstrip('.') + '.'
    if unchanged and 'nothing was changed' not in sentence.lower():
        sentence += ' Nothing was changed.'
    return sentence if len(sentence) <= limit else sentence[:limit - 3].rstrip() + '...'


# ---------------------------------------------------------------- check results

def finish_check(status, output, previous, moment, current):
    """Records a check that ended. Returns (error, document). An unreachable source exits 0 and
    writes an empty result: that is a failure too, and the last good result is put back so an
    offline host keeps showing the release it knew of."""
    document = read('available.json')
    error = None
    if status != 0:
        error = meaningful(output)
    elif not isinstance(document, dict):
        error = 'The check left no result.'
    else:
        unreachable = [item for item in document.get('refusals') or [] if isinstance(item, str) and item.startswith(UNREACHABLE)]
        if unreachable and document.get('available') is None:
            error = unreachable[0]
    if error and previous is not None:
        target = path('available.json')
        temporary = target.with_name(f'.available.{uuid.uuid4().hex}.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        document = read('available.json')
    record = read('check.json')
    before = record.get('failures') if isinstance(record, dict) and isinstance(record.get('failures'), int) else 0
    failures = before + 1 if error else 0
    write('check.json', {'attempted_at': stamp(moment), 'error': error, 'failures': failures})
    return error, document


def announce_available(document, current, catalog=None):
    """One update.available per version, ever: the ledger remembers it past the dedupe window."""
    document = fresh(document, current)
    release = document.get('available') if document else None
    if not isinstance(release, dict) or not isinstance(release.get('version'), str):
        return None
    version = release['version']
    if version in ledger()['announced']:
        return None
    remember('announced', version)
    return notification_producers.emit('update.available', 'info', 'update.available|' + version, {},
                                       'system:supervisor', 'update_available',
                                       {'version': version, 'class': str(release.get('class'))}, catalog=catalog)


def announce_outcome(before, after, catalog=None):
    """The notification a change of the upgrade state earns, from the process that made it:
      applied -> confirmed                 update.applied (info)
      applied -> rolling_back              update.rolled_back (warning), the automatic way back
      applied|rolling_back -> rollback_failed  update.rollback_failed (critical)
    Every way back also goes into the ledger, so automatic mode never tries that version again.
    Returns the kind emitted, or None."""
    if not isinstance(after, dict):
        return None
    was = before.get('phase') if isinstance(before, dict) else None
    phase, version = after.get('phase'), label(after)
    same = isinstance(before, dict) and before.get('started_at') == after.get('started_at')
    if not same or was == phase:
        return None
    if phase in ('rolling_back', 'rolled_back', 'rollback_failed') and version:
        remember('rolled_back', version)
    kind = None
    if was == 'applied' and phase == 'confirmed':
        kind, severity, detail = 'update.applied', 'info', {'version': version, 'trigger': str(after.get('trigger') or 'cli')}
    elif was == 'applied' and phase == 'rolling_back' and after.get('automatic'):
        kind, severity, detail = 'update.rolled_back', 'warning', {'version': version}
    elif was in ('applied', 'rolling_back') and phase == 'rollback_failed':
        kind, severity, detail = 'update.rollback_failed', 'critical', {'version': version}
    if kind is None:
        return None
    notification_producers.emit(kind, severity, f'{kind}|{version}', {}, 'system:supervisor',
                                kind.replace('.', '_'), detail, catalog=catalog)
    return kind


# ---------------------------------------------------------------- what runs now

def publish_current(upgrade_state, moment=None):
    """Writes current.json for the console: the running version and whether it may offer
    "roll back". Tied to the upgrade record it judged by its start time."""
    import release_channel
    running = release_channel.current_version()
    possible, reason = rollback_verdict(upgrade_state)
    record = {'version': running['version'], 'commit': running['commit'], 'written_at': stamp(moment or now()),
              'rollback': {'started_at': (upgrade_state or {}).get('started_at') if isinstance(upgrade_state, dict) else None,
                           'possible': possible, 'reason': reason}}
    write('current.json', record)
    return record
