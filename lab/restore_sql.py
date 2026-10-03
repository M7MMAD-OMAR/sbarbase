"""Plan a native pg_restore SQL stream before a staged restore changes anything.

Only database identifiers in database-level DDL are rebased. COPY payload and
other SQL are copied verbatim. This is not a sanitizer for an untrusted archive:
PostgreSQL restoration executes trusted source SQL with administrator privileges.
The accepted psql commands are deliberately much narrower than general psql.
"""
from dataclasses import dataclass
import re


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Statement:
    text: str
    tokens: tuple
    directive: bool = False


@dataclass(frozen=True)
class ArchivePlan:
    create: str
    metadata: tuple
    original: str
    stage: str
    explicit_acl: bool
    roles: tuple = ()


class Reader:
    def __init__(self, source):
        self.source = source
        self.pending = ''

    def get(self):
        if self.pending:
            result, self.pending = self.pending[0], self.pending[1:]
            return result
        return self.source.read(1)

    def peek(self):
        if not self.pending:
            self.pending = self.source.read(1)
        return self.pending

    def line(self):
        result, self.pending = self.pending, ''
        return result + self.source.readline()


def statements(reader, limit=16 * 1024 * 1024):
    """Lex SQL statements, including nested comments and quoted function bodies."""
    chars, tokens = [], []

    def add(value):
        chars.append(value)
        if len(chars) > limit:
            raise PlanError('Archive SQL statement exceeds supported size')

    while True:
        char = reader.get()
        if not char:
            if tokens:
                raise PlanError('Archive SQL has an incomplete statement')
            if chars:
                yield Statement(''.join(chars), ())
            return
        start = len(chars)
        add(char)
        if char.isspace():
            continue
        if char == '-' and reader.peek() == '-':
            add(reader.get())
            while True:
                char = reader.get()
                if not char:
                    break
                add(char)
                if char == '\n':
                    break
            continue
        if char == '/' and reader.peek() == '*':
            add(reader.get())
            depth = 1
            while depth:
                char = reader.get()
                if not char:
                    raise PlanError('Archive SQL has an incomplete block comment')
                add(char)
                if char == '/' and reader.peek() == '*':
                    add(reader.get())
                    depth += 1
                elif char == '*' and reader.peek() == '/':
                    add(reader.get())
                    depth -= 1
            continue
        if char == '\\':
            if tokens or ''.join(chars[:-1]).rsplit('\n', 1)[-1].strip():
                raise PlanError('Unexpected psql command inside archive SQL')
            line = reader.line()
            for item in line:
                add(item)
            yield Statement(''.join(chars), (), True)
            chars, tokens = [], []
            continue
        if char in ('\'', '"'):
            quote = char
            escape = quote == "'" and bool(tokens) and tokens[-1].kind == 'word' \
                and tokens[-1].value.upper() == 'E' and tokens[-1].end == start
            decoded = []
            while True:
                char = reader.get()
                if not char:
                    raise PlanError('Archive SQL has an incomplete quote')
                add(char)
                if escape and char == '\\':
                    escaped = reader.get()
                    if not escaped:
                        raise PlanError('Archive SQL has an incomplete escape')
                    add(escaped)
                    decoded.extend((char, escaped))
                elif char == quote:
                    if reader.peek() == quote:
                        add(reader.get())
                        decoded.append(quote)
                    else:
                        break
                else:
                    decoded.append(char)
            tokens.append(Token('identifier' if quote == '"' else 'string', ''.join(decoded), start, len(chars)))
            continue
        if char == '$':
            tag = '$'
            while reader.peek() and (reader.peek().isalnum() or reader.peek() == '_'):
                part = reader.get()
                add(part)
                tag += part
            if reader.peek() != '$' or not re.fullmatch(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$', tag + '$'):
                raise PlanError('Unsupported dollar token in archive SQL')
            add(reader.get())
            tag += '$'
            tail = ''
            while True:
                char = reader.get()
                if not char:
                    raise PlanError('Archive SQL has an incomplete dollar quote')
                add(char)
                tail = (tail + char)[-len(tag):]
                if tail == tag:
                    break
            tokens.append(Token('body', '', start, len(chars)))
            continue
        if char.isalpha() or char == '_':
            value = char
            while reader.peek() and (reader.peek().isalnum() or reader.peek() in ('_', '$')):
                char = reader.get()
                add(char)
                value += char
            tokens.append(Token('word', value, start, len(chars)))
            continue
        if char.isdigit() or char == '-' and reader.peek().isdigit():
            value = char
            while reader.peek().isdigit():
                char = reader.get()
                add(char)
                value += char
            tokens.append(Token('number', value, start, len(chars)))
            continue
        tokens.append(Token('symbol', char, start, len(chars)))
        if char == ';':
            yield Statement(''.join(chars), tuple(tokens))
            chars, tokens = [], []


def keyword(token, value):
    return token.kind == 'word' and token.value.upper() == value


def identifier(token, expected=None):
    if token.kind not in ('word', 'identifier'):
        raise PlanError('Archive database identifier is malformed')
    value = token.value if token.kind == 'identifier' else token.value.lower()
    if expected is not None and value != expected:
        raise PlanError('Archive SQL names another database')
    return value


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def rebase(statement, offset, original, stage):
    token = statement.tokens[offset]
    identifier(token, original)
    return statement.text[:token.start] + quote_identifier(stage) + statement.text[token.end:]


def database_offset(statement):
    """Recognize only DB DDL emitted by pg_restore --create, not SQL substrings."""
    words = statement.tokens
    keys = [token.value.upper() if token.kind == 'word' else None for token in words]
    if keys[:2] in (['CREATE', 'DATABASE'], ['ALTER', 'DATABASE']):
        return 2
    if keys[:3] == ['COMMENT', 'ON', 'DATABASE']:
        return 3
    if keys[:2] == ['SECURITY', 'LABEL']:
        if any(keyword(token, 'DATABASE') for token in words):
            raise PlanError('Database security labels are not supported by staged restore')
    if keys[:2] in (['ALTER', 'ROLE'], ['ALTER', 'USER']):
        if len(words) >= 6 and keys[3:5] == ['IN', 'DATABASE']:
            return 5
        raise PlanError('Archive has an unsupported cluster role operation')
    if keys and keys[0] in ('GRANT', 'REVOKE'):
        for index in range(1, len(words) - 2):
            if keyword(words[index], 'ON') and keyword(words[index + 1], 'DATABASE'):
                return index + 2
    if keys[:2] == ['DROP', 'DATABASE']:
        raise PlanError('Archive may not drop a database')
    return None


def archived_roles(statement):
    tokens = statement.tokens
    roles = set()
    if not tokens:
        return roles
    def take(token):
        role = identifier(token)
        if not keyword(token, 'PUBLIC'):
            roles.add(role)
    if keyword(tokens[0], 'ALTER'):
        for index in range(1, len(tokens) - 2):
            if keyword(tokens[index], 'OWNER') and keyword(tokens[index + 1], 'TO'):
                take(tokens[index + 2])
            if keyword(tokens[index], 'FOR') and keyword(tokens[index + 1], 'ROLE'):
                take(tokens[index + 2])
        if len(tokens) > 2 and keyword(tokens[1], 'ROLE'):
            take(tokens[2])
    if keyword(tokens[0], 'CREATE') and len(tokens) > 2 and keyword(tokens[1], 'SCHEMA'):
        for index in range(2, len(tokens) - 1):
            if keyword(tokens[index], 'AUTHORIZATION'):
                take(tokens[index + 1])
    if keyword(tokens[0], 'GRANT') or keyword(tokens[0], 'REVOKE') or \
            len(tokens) > 2 and keyword(tokens[0], 'ALTER') and keyword(tokens[1], 'DEFAULT'):
        clause = 'TO' if any(keyword(token, 'GRANT') for token in tokens) else 'FROM'
        for index, token in enumerate(tokens):
            if keyword(token, clause):
                position = index + 1
                while position < len(tokens) and tokens[position].value != ';':
                    if keyword(tokens[position], 'WITH') or keyword(tokens[position], 'CASCADE') or keyword(tokens[position], 'RESTRICT'):
                        break
                    if tokens[position].value != ',':
                        take(tokens[position])
                    position += 1
                break
    return roles


def create_stage(statement, original, stage):
    """Keep archived locale/encoding, but admit the private stage as admin only."""
    tokens = statement.tokens
    identifier(tokens[2], original)
    position = 3
    if position < len(tokens) and keyword(tokens[position], 'WITH'):
        position += 1
    allowed = {'TEMPLATE', 'ENCODING', 'LOCALE_PROVIDER', 'LOCALE', 'LC_COLLATE', 'LC_CTYPE',
               'BUILTIN_LOCALE', 'ICU_LOCALE', 'ICU_RULES', 'COLLATION_VERSION', 'TABLESPACE'}
    seen, options, values = set(), [], {}
    while position < len(tokens) - 1:
        option = tokens[position]
        if option.kind != 'word' or option.value.upper() not in allowed or option.value.upper() in seen:
            raise PlanError('Archive CREATE DATABASE has unsupported properties')
        key = option.value.upper()
        seen.add(key)
        position += 1
        if position < len(tokens) and tokens[position].value == '=':
            position += 1
        if position >= len(tokens) - 1 or tokens[position].kind not in ('word', 'identifier', 'string'):
            raise PlanError('Archive database property is malformed')
        value = tokens[position]
        values[key] = value.value
        if key == 'TEMPLATE' and identifier(value) != 'template0':
            raise PlanError('Archive database must use template0')
        if key == 'TABLESPACE' and identifier(value) != 'pg_default':
            raise PlanError('Custom database tablespace is not supported')
        if key == 'LOCALE_PROVIDER' and identifier(value) not in ('libc', 'icu', 'builtin'):
            raise PlanError('Unsupported database locale provider')
        options.append(key + ' = ' + statement.text[value.start:value.end])
        position += 1
    if 'TEMPLATE' not in seen or tokens[-1].value != ';':
        raise PlanError('Archive database template is unavailable')
    if 'BUILTIN_LOCALE' in seen and (values.get('LOCALE_PROVIDER') != 'builtin'
            or values['BUILTIN_LOCALE'] not in ('C', 'C.UTF-8')):
        raise PlanError('Archive builtin locale requires the supported PostgreSQL 17 provider and locale')
    if values.get('LOCALE_PROVIDER') == 'builtin' and values.get('BUILTIN_LOCALE', values.get('LOCALE')) not in ('C', 'C.UTF-8'):
        raise PlanError('Archive builtin provider locale is unsupported')
    return 'CREATE DATABASE ' + quote_identifier(stage) + ' WITH ' + ' '.join(options) \
        + ' OWNER = supabase_admin CONNECTION LIMIT = 0;\n'


def validate_metadata(statement, offset):
    tokens = statement.tokens
    keys = [token.value.upper() if token.kind == 'word' else None for token in tokens]
    if offset + 1 >= len(tokens) or tokens[-1].value != ';':
        raise PlanError('Malformed archived database metadata')
    if keys[:2] == ['ALTER', 'DATABASE']:
        suffix = keys[offset + 1:]
        if suffix[:2] == ['OWNER', 'TO']:
            if len(tokens) != offset + 5:
                raise PlanError('Malformed archived database owner')
            identifier(tokens[offset + 3])
        elif suffix[:2] == ['CONNECTION', 'LIMIT']:
            values = tokens[offset + 3:-1]
            if values and values[0].value == '=':
                values = values[1:]
            if len(values) != 1 or values[0].kind != 'number' or int(values[0].value) < -1:
                raise PlanError('Malformed archived connection limit')
        elif suffix[:1] != ['SET']:
            raise PlanError('Unsupported archived database alteration')
    elif keys[:2] in (['ALTER', 'ROLE'], ['ALTER', 'USER']):
        identifier(tokens[2])
        if keys[offset + 1] != 'SET':
            raise PlanError('Unsupported archived role-in-database alteration')
    elif keys[:3] == ['COMMENT', 'ON', 'DATABASE']:
        if keys[offset + 1] != 'IS' or len(tokens) != offset + 4 \
                or not (tokens[offset + 2].kind == 'string' or keyword(tokens[offset + 2], 'NULL')):
            raise PlanError('Malformed archived database comment')
    elif keys[:1] not in (['GRANT'], ['REVOKE']):
        raise PlanError('Unsupported archived database metadata')
    # Any second database name could modify another live database. Native dump
    # emits individual DB metadata statements, never a comma-separated DB list.
    if offset + 1 < len(tokens) and tokens[offset + 1].value == ',':
        raise PlanError('Archive metadata names multiple databases')
    setting_offset = offset + 2
    if keys[offset + 1] == 'SET' and setting_offset < len(tokens) \
            and tokens[setting_offset].value.lower() in ('shared_preload_libraries', 'session_preload_libraries',
                                                       'local_preload_libraries', 'cron'):
        raise PlanError('Archive contains unsupported privileged worker configuration')


def plan_archive(source, body, original, stage):
    """Write replay SQL and return independently replayable archive DB metadata."""
    if not re.fullmatch(r'(?:e_[a-f0-9]{24}|storage_metadata)', original) \
            or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', stage) or stage == original:
        raise PlanError('Unsupported staged database identity')
    reader = Reader(source)
    create, metadata, connected, restriction, explicit_acl = None, [], False, None, False
    connection_limit = False
    restriction_key, restriction_count = None, 0
    connection_count, properties_since_connect = 0, False
    roles = set()
    for statement in statements(reader):
        if statement.directive:
            directive_start = statement.text.rfind('\\')
            body.write(statement.text[:directive_start])
            line = statement.text[directive_start:].strip()
            simple = '\\connect ' + original
            quoted = '\\connect ' + quote_identifier(original)
            libpq = '\\connect -reuse-previous=on "dbname=\'' + original + '\'"'
            if line in (simple, quoted, libpq):
                if create is None or restriction is not None or connection_count >= 2 \
                        or connected and not properties_since_connect:
                    raise PlanError('Archive reconnect order is unsupported')
                connected = True
                connection_count += 1
                properties_since_connect = False
            elif re.fullmatch(r'\\restrict [A-Za-z0-9]+', line):
                key = line.split()[1]
                if restriction is not None or restriction_count >= 3 or restriction_count != connection_count \
                        or (restriction_key is not None and key != restriction_key):
                    raise PlanError('Archive restriction order is unsupported')
                restriction = restriction_key = key
                restriction_count += 1
            elif line == '\\unrestrict ' + str(restriction) and restriction is not None:
                restriction = None
            else:
                raise PlanError('Unsupported psql command in archive')
            continue
        tokens = statement.tokens
        if not tokens:
            body.write(statement.text)
            continue
        roles.update(archived_roles(statement))
        if len(tokens) >= 3 and keyword(tokens[0], 'CREATE') and keyword(tokens[1], 'EXTENSION'):
            position = 2
            if len(tokens) > 5 and all(keyword(tokens[index], name) for index, name in ((2, 'IF'), (3, 'NOT'), (4, 'EXISTS'))):
                position = 5
            supported = {'plpgsql', 'pgcrypto', 'uuid-ossp', 'citext', 'hstore', 'ltree', 'pg_trgm',
                         'btree_gin', 'btree_gist', 'vector', 'pg_stat_statements', 'pgaudit'}
            if identifier(tokens[position]) not in supported:
                raise PlanError('Archive extension has no classified restore worker contract')
        if len(tokens) >= 2 and keyword(tokens[0], 'CREATE') and keyword(tokens[1], 'SUBSCRIPTION'):
            raise PlanError('Archive logical replication writer contract is unsupported')
        if len(tokens) > 2 and keyword(tokens[0], 'SET') \
                and tokens[1].value.lower() in ('shared_preload_libraries', 'session_preload_libraries', 'local_preload_libraries', 'cron'):
            raise PlanError('Archive contains unsupported restore-session worker configuration')
        offset = database_offset(statement)
        if offset is not None:
            if keyword(tokens[0], 'CREATE'):
                if create is not None or connected:
                    raise PlanError('Archive has multiple database definitions')
                create = create_stage(statement, original, stage)
            else:
                validate_metadata(statement, offset)
                metadata.append(rebase(statement, offset, original, stage))
                connection_limit = connection_limit or (len(tokens) > 4 and keyword(tokens[0], 'ALTER')
                    and keyword(tokens[1], 'DATABASE') and keyword(tokens[3], 'CONNECTION')
                    and keyword(tokens[4], 'LIMIT'))
                properties_since_connect = properties_since_connect or (connected and keyword(tokens[0], 'ALTER')
                    and (keyword(tokens[offset + 1], 'SET') or keyword(tokens[offset + 1], 'CONNECTION')))
                explicit_acl = explicit_acl or keyword(tokens[0], 'GRANT') or keyword(tokens[0], 'REVOKE')
            continue
        body.write(statement.text)
        if keyword(tokens[0], 'COPY'):
            if len(tokens) < 4 or not keyword(tokens[-3], 'FROM') or not keyword(tokens[-2], 'STDIN'):
                raise PlanError('Unsupported COPY transport in archive')
            # COPY text is not SQL. Do not tokenize semicolons, backslashes,
            # apparent DDL or dollar quotes inside rows.
            line = reader.line()
            if line not in ('\n', '\r\n'):
                raise PlanError('Malformed archive COPY boundary')
            body.write(line)
            while True:
                line = reader.line()
                if not line:
                    raise PlanError('Incomplete archive COPY data')
                body.write(line)
                if line in ('\\.\n', '\\.\r\n'):
                    break
    if create is None or not connected or restriction is not None:
        raise PlanError('Archive database definition or reconnect proof is incomplete')
    # pg_dump omits the default unlimited connection limit. A staging zero
    # limit must not accidentally become the restored database's default.
    if not connection_limit:
        metadata.append('ALTER DATABASE ' + quote_identifier(stage) + ' CONNECTION LIMIT = -1;\n')
    return ArchivePlan(create, tuple(metadata), original, stage, bool(explicit_acl), tuple(sorted(roles)))
