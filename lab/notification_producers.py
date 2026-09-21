"""Operator notification producers outside the TypeScript catalog.

Every producer in this tree that does not originate in a catalog method writes its
event through ``emit()`` here. Three boundaries are structural, not stylistic:

Placement. The outbox row is written in one ``emit()`` call at the point the durable
state change is recorded: the fence is verified, the descriptor is written, the
supervisor stage returned. It is never called from a print, a log line or an
after-the-fact observer, because those are not state changes.

Transaction. ``emit()`` opens one immediate SQLite transaction, writes the catalog's
own durable record of the state change (an ``audit_events`` row carrying the kind as
its action) and the ``notification_outbox`` row with its delivery rows inside it,
then commits. A refused enqueue rolls the record back with it, exactly as the
TypeScript ``notify()`` does inside the caller's transaction in
``src/control/catalog.ts``. Where the state change itself is a file or another
database (a fence descriptor, a restore descriptor, the environment database), the
file is written by its owner and this row is the catalog's record of it; the two are
not one transaction and the code does not pretend they are.

Blast radius. A producer that cannot reach the catalog changes nothing about its
operation. ``emit()`` catches every failure, including a missing, busy, locked,
read-only or truncated catalog, and returns ``None``. It never raises, so no
notification can alter an operation's outcome, its exit code protocol or the
``SystemExit`` sentence a script prints.

Nothing here is a second claimer. It writes one row and closes; the single claimant
of ``notification_delivery`` is still the drain inside the worker (``lab/notify.py``).
"""
import json
import re
import time
from pathlib import Path

import notify

CATALOG = Path(__file__).resolve().parents[1] / '.lab' / 'upstream' / 'control.sqlite'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
SUBJECT_FIELDS = ('organization', 'project', 'environment', 'runtime')
AUDIT_SQL = 'INSERT INTO audit_events(actor,action,subject,detail,at) VALUES (?,?,?,?,?)'


def emit(kind, severity, dedupe_key, subject, actor, reason, detail, catalog=None):
    """Record one durable operator event, or return None without raising.

    ``catalog`` names the control catalog to write into. The default is the upstream
    installation catalog, the same file ``lab/notify.py`` drains from the worker. A
    missing file is a refusal, never a silent creation of an empty catalog.
    """
    try:
        if kind not in notify.KINDS or severity not in notify.SEVERITIES:
            return None
        if reason not in notify.REASONS:
            return None
        if sorted(detail) != sorted(notify.DETAIL_KEYS[kind]):
            return None
        subject = dict(subject or {})
        for field, value in subject.items():
            if field not in SUBJECT_FIELDS:
                return None
            if not isinstance(value, str) or not value or len(value) > 64:
                return None
        if 'runtime' in subject and not RUNTIME.fullmatch(subject['runtime']):
            return None
        if not isinstance(actor, str) or not actor:
            return None
        path = Path(catalog) if catalog is not None else CATALOG
        if not path.is_file():
            # The producer cannot reach the catalog. The kind stays unemitted: it is
            # never emitted late from a log line or a retry outside the change.
            return None
        database = notify.connect(path)
    except Exception:
        return None
    try:
        with notify.immediate(database):
            database.execute(AUDIT_SQL, (actor, kind,
                                         subject.get('environment') or subject.get('runtime') or 'installation',
                                         json.dumps(detail, sort_keys=True), int(time.time() * 1000)))
            return notify.enqueue(database, kind, severity, dedupe_key, subject, actor, reason, detail)
    except Exception:
        # A refusal, a lock, a full disk or a truncated catalog: the operation is
        # unaffected and the event is not emitted elsewhere.
        return None
    finally:
        database.close()