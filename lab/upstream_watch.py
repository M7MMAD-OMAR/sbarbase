"""Report upstream drift for every pinned component. Read-only: nothing is pulled or changed.

Usage:
  /usr/bin/python3 lab/upstream_watch.py          # table
  /usr/bin/python3 lab/upstream_watch.py --json   # one JSON object
  /usr/bin/python3 lab/upstream_watch.py --json-out report.json   # table, plus the JSON in a file

For each pin in the lock files (lab/pin_update.py LOCKS) it reports:
- the newest stable release in the pinned major line, and the newest overall,
  compared within the pinned tag's own scheme (numeric or dated), so release
  candidates, OrioleDB variants and retired naming schemes never count;
- the version the official Supabase self-hosting compose file pins, the bar
  upstream tests together;
- whether the pinned tag now resolves to a different digest (a rebuilt
  floating-major tag, or a retagged release, which needs attention either way);
- whether a dated review entry exists under docs/upstream/ for the newest release.

Exit status: 0 everything current or already reviewed, 3 drift that needs a
review, 2 a lookup failed, so a schedule can tell "act" from "retry".
Adoption still goes through docs/engineering/UPSTREAM-UPDATE-POLICY.md.
"""
import argparse
import http.client
import json
import re
import sys
import urllib.parse
import urllib.request

import image_identity
import pin_update

COMPOSE='https://raw.githubusercontent.com/supabase/supabase/master/docker/docker-compose.yml'
MANIFEST=', '.join(('application/vnd.oci.image.index.v1+json','application/vnd.docker.distribution.manifest.list.v2+json',
                    'application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'))
NUMERIC=re.compile(r'(v?)(\d+(?:\.\d+)+)')
DATED=re.compile(r'\d{4}\.\d{2}\.\d{2}-sha-[0-9a-f]+')
MAJOR=re.compile(r'(\d+)(-[a-z0-9][a-z0-9.]*)?')  # a moving major tag such as 17-alpine
TIMEOUT=20
EXIT_CURRENT,EXIT_FAILED,EXIT_DRIFT=0,2,3


class LookupFailed(Exception):
    """A registry or source could not be read; never reported as 'current'."""


def fetch(url,headers=None,method='GET'):
    """(headers, body) for one request; any transport or HTTP failure raises LookupFailed."""
    request=urllib.request.Request(url,headers=headers or {},method=method)
    try:
        with urllib.request.urlopen(request,timeout=TIMEOUT) as response:
            return dict(response.headers.items()),response.read()
    except (http.client.HTTPException,OSError,ValueError) as error:  # URLError is an OSError
        raise LookupFailed(f'{method} {url}: {error}') from None


def parse_tag(tag):
    """(registry, repository path, version) of a lock tag such as public.ecr.aws/supabase/gotrue:v2.196.0,
    normalized the way the runtime admits it (image_identity.repository)."""
    name,version=tag.rsplit(':',1)
    return (*image_identity.repository(name).split('/',1),version)


def lowered(headers):
    return {key.lower():value for key,value in headers.items()}


class Registry:
    """Anonymous pull access to public.ecr.aws and Docker Hub through the registry v2 API."""

    def __init__(self,get=fetch):
        self.get,self.tokens=get,{}

    def base(self,registry):
        return 'https://registry-1.docker.io' if registry=='docker.io' else 'https://'+registry

    def token(self,registry,repository):
        if (registry,repository) not in self.tokens:
            scope=urllib.parse.quote(f'repository:{repository}:pull')
            url=('https://auth.docker.io/token?service=registry.docker.io&scope='+scope if registry=='docker.io'
                 else f'https://{registry}/token/?scope='+scope)
            try:token=json.loads(self.get(url)[1])['token']
            except (ValueError,KeyError,TypeError) as error:raise LookupFailed(f'token for {repository}: {error}') from None
            if not isinstance(token,str) or not token:raise LookupFailed(f'token for {repository}: not a string')
            self.tokens[(registry,repository)]=token
        return self.tokens[(registry,repository)]

    def headers(self,registry,repository,**extra):
        return {'Authorization':'Bearer '+self.token(registry,repository),**extra}

    def tags(self,registry,repository):
        found,url=[],f'{self.base(registry)}/v2/{repository}/tags/list?n=1000'
        for _ in range(100):  # pagination follows Link headers; a hundred pages is far past any real repository
            headers,body=self.get(url,self.headers(registry,repository))
            try:page=json.loads(body).get('tags')
            except (ValueError,AttributeError) as error:raise LookupFailed(f'tags of {repository}: {error}') from None
            if not isinstance(page,list) or not all(isinstance(tag,str) for tag in page):
                raise LookupFailed(f'tags of {repository}: not a list of strings')
            found+=page
            link=re.search(r'<([^>]+)>',lowered(headers).get('link',''))
            if not link:return found
            url=urllib.parse.urljoin(self.base(registry),link[1])
        raise LookupFailed(f'tags of {repository}: too many pages')

    def digest(self,registry,repository,tag):
        headers,_=self.get(f'{self.base(registry)}/v2/{repository}/manifests/{tag}',
                           self.headers(registry,repository,Accept=MANIFEST),method='HEAD')
        value=lowered(headers).get('docker-content-digest','')
        if not image_identity.DIGEST.fullmatch(value):raise LookupFailed(f'digest of {repository}:{tag} missing')
        return value


def numbers(version):
    return tuple(int(part) for part in NUMERIC.fullmatch(version)[2].split('.'))


def order(version):
    """Numeric order across tag shapes: upstream has changed segment counts (PostgREST v13.0.7 to
    v14.17) and prefixes (gotrue 2.140.0 beside v2.196.0), so pad instead of filtering by shape."""
    parts=numbers(version)
    return parts+(0,)*(8-len(parts)),version


def scheme(version):
    if NUMERIC.fullmatch(version):return 'numeric'
    if DATED.fullmatch(version):return 'dated'
    if MAJOR.fullmatch(version):return 'major'
    return None


def major(version):
    """The major line of a numeric or moving-major tag; None for dated builds."""
    kind=scheme(version)
    return numbers(version)[0] if kind=='numeric' else int(MAJOR.fullmatch(version)[1]) if kind=='major' else None


def candidates(version,tags):
    """Stable tags in the same scheme as the pinned version, oldest first; None when the scheme is not comparable."""
    kind=scheme(version)
    if kind=='numeric':
        return sorted({t for t in tags if NUMERIC.fullmatch(t)},key=order)
    if kind=='dated':return sorted({t for t in tags if DATED.fullmatch(t)})
    if kind=='major':
        suffix=MAJOR.fullmatch(version)[2]
        return sorted({t for t in tags if (m:=MAJOR.fullmatch(t)) and m[2]==suffix},key=major)
    return None


def change(old,new):
    """major, minor or patch for numeric versions; 'major' for a moving-major tag; 'newer' for dated builds."""
    kind=scheme(old)
    if kind=='major':return 'major'
    if kind!='numeric':return 'newer'
    a,b=numbers(old),numbers(new)
    return 'major' if a[0]!=b[0] else 'minor' if a[:2]!=b[:2] else 'patch'


def newer(version,ordered):
    """Releases after the pinned one. A moving-major tag tracks its own line through digest drift, so
    only later major lines count. Dated builds order by date only: a different build from the same day
    cannot be ordered by its commit hash, so it counts as a candidate to review."""
    kind=scheme(version)
    if kind=='numeric':return [t for t in ordered if order(t)[0]>order(version)[0]]
    if kind=='major':return [t for t in ordered if major(t)>major(version)]
    day=version[:10]
    return [t for t in ordered if t[:10]>day or (t[:10]==day and t!=version)]


def compose_pins(text):
    """Repository path (registry host dropped) to version, from the official compose file."""
    pins={}
    for match in re.finditer(r'^\s*image:\s*["\']?([^\s"\']+):([^\s"\'@]+)',text,re.M):
        try:pins[image_identity.repository(match[1]).split('/',1)[1]]=match[2]
        except image_identity.IdentityError:continue  # not an image reference the runtime could pin
    return pins


def compose_version(repository,compose):
    """The compose pin for this repository: the same path, else the same image name outside the
    Docker library, since compose pulls supabase/* from Docker Hub and postgrest from its own namespace."""
    if repository in compose:return compose[repository]
    if repository.startswith('library/'):return None
    image=repository.rsplit('/',1)[-1]
    found=[v for name,v in compose.items() if not name.startswith('library/') and name.rsplit('/',1)[-1]==image]
    return found[0] if len(found)==1 else None


def failed(component,error,tag=None):
    return {'component':component,'tag':tag,'version':'-','compose':None,'status':'failed','error':error}


def inspect(label,entry,registry,compose,upstream=None):
    if (reason:=pin_update.problem(entry)):
        return failed(label,'invalid pin: '+reason,str(entry.get('tag')) if isinstance(entry,dict) else None)
    host,repository,version=parse_tag(entry['tag'])
    image=repository.rsplit('/',1)[-1]
    row={'component':label,'tag':entry['tag'],'version':version,'compose':compose_version(repository,compose)}
    try:
        current=registry.digest(host,repository,version)
        row['digest_moved']=current!=entry['id']
        tags=registry.tags(host,repository)
        # A tag list that omits the pinned version is incomplete or wrong; it can never prove "current".
        if version not in tags:raise LookupFailed(f'tags of {repository}: the pinned version {version} is not listed')
        ordered=candidates(version,tags)
    except (LookupFailed,ValueError,TypeError,KeyError,AttributeError) as error:
        return dict(row,status='failed',error=str(error) if isinstance(error,LookupFailed) else f'malformed response: {error!r}')
    if ordered is None:  # an unknown version scheme cannot be compared, so it is never reported current
        return dict(row,status='failed',error=f'version {version} has no comparable scheme (numeric, dated or moving major)')
    ahead=newer(version,ordered)
    line=[t for t in ahead if major(version) is None or major(t)==major(version)]
    row.update(scheme=scheme(version),newest_in_line=line[-1] if line else None,newest=ahead[-1] if ahead else None)
    if ahead:row['change']=change(version,ahead[-1])
    # Several builds on the newest day have no order between them; every one needs a review.
    same_day=[t for t in ahead if row['scheme']=='dated' and t[:10]==ahead[-1][:10]]
    if len(same_day)>1:row['same_day_builds']=same_day
    wanted={row['newest_in_line'],row['newest'],*row.get('same_day_builds',())}
    pending=[t for t in wanted if t and not pin_update.review_entries(f'{image}:{t}',upstream=upstream)]
    row['unreviewed']=sorted(pending)
    row['status']='moved' if row['digest_moved'] else 'behind' if pending else 'reviewed' if row['newest'] else 'current'
    return row


def report(get=fetch,upstream=None):
    registry=Registry(get)
    try:compose,compose_error=compose_pins(get(COMPOSE)[1].decode()),None
    except (LookupFailed,UnicodeDecodeError) as error:compose,compose_error={},str(error)
    if compose_error is None and not compose:compose_error='the compose file lists no images'  # an HTML page or a moved file
    rows,work=[],[]
    for relative in pin_update.LOCKS:
        # Each lock on its own: an unreadable or misshapen file fails its own row, never the whole report.
        try:lock=json.loads((pin_update.ROOT/relative).read_text())
        except (OSError,ValueError) as error:
            rows.append(failed(relative,'unreadable lock: '+str(error)));continue
        # Pin-level problems are reported by inspect on the pin's own row.
        rows+=[failed(relative,'invalid lock: '+line) for line in pin_update.shape_problems(relative,lock)]
        work+=[(pin_update.label(relative,key),entry) for (_,key),entry in sorted(pin_update.pins(relative,lock).items())]
    # One lookup at a time: public.ecr.aws answers parallel anonymous tag listings with HTTP 429.
    rows+=[inspect(label,entry,registry,compose,upstream) for label,entry in work]
    failing=compose_error is not None or any(r['status']=='failed' for r in rows)
    drift=any(r['status'] in ('behind','moved') for r in rows)
    return {'compose_source':COMPOSE,'compose_error':compose_error,'components':rows,
            'exit':EXIT_FAILED if failing else EXIT_DRIFT if drift else EXIT_CURRENT}


def table(result):
    lines=[f'{"component":10} {"pinned":22} {"in line":18} {"newest":18} {"compose":22} status']
    for r in result['components']:
        lines.append(f'{r["component"]:10} {r["version"]:22} {r.get("newest_in_line") or "-":18} {r.get("newest") or "-":18} '
                     f'{r.get("compose") or "-":22} {r["status"]}{" (digest moved)" if r.get("digest_moved") else ""}'
                     f'{"  "+r["error"] if r.get("error") else ""}')
    if result['compose_error']:lines.append('compose lookup failed: '+result['compose_error'])
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Read-only upstream drift report for pinned components')
    parser.add_argument('--json',action='store_true',help='print the report as JSON instead of the table')
    parser.add_argument('--json-out',metavar='PATH',help='also write the JSON report to PATH')
    args=parser.parse_args(argv)
    result=report()
    if args.json_out:
        with open(args.json_out,'w') as file:json.dump(result,file,indent=2)
    print(json.dumps(result,indent=2) if args.json else table(result))
    return result['exit']


if __name__=='__main__':sys.exit(main())
