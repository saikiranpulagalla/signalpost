"""Registry-anchored, context-aware website identity decisions (stdlib only).

No fuzzy-name-only publication. Group pages stay on review, never on exact.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
import hashlib
import ipaddress
import re
import unicodedata
from datetime import datetime, timezone

from v0_kernel.contracts import is_valid_orgnr
from v1_official.sources import SourceResponse, endpoint, verified_payload, sha

_MAX_HTML = 512 * 1024
_ORG_RE = re.compile(r'(?i)(?:\borg\.?\s*nr\.?|\borganisasjonsnummer\b|\borganization\s+number\b)\s*[:#.]?\s*(?:NO\s+)?([0-9]{3}(?:[ \t-]*[0-9]{3}){2})(?:\s+MVA)?\b')
_PARKED = ('domain for sale', 'buy this domain', 'this domain is parked', 'domenet er til salgs', 'hugedomains')


@dataclass(frozen=True)
class Candidate:
    organisation_number: str
    legal_name: str
    url: str
    registry_source_url: str
    registry_source_sha256: str
    registry_observed_at: str
    source_mode: str


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str
    organisation_number: str
    website_url: str
    retrieved_at: str | None
    content_sha256: str | None
    found_org_numbers: tuple[str, ...]
    evidence_excerpt: str | None
    publishable: bool = False  # First-party company claims still need per-fact validation.

    def to_dict(self):
        return asdict(self)


def normalize_legal_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('Legal name must be text')
    return ' '.join(unicodedata.normalize('NFC', value).casefold().split())


def normalize_url(raw: str) -> str:
    """Strict candidate syntax; no DNS resolution, no network, HTTPS only."""
    if not isinstance(raw, str) or not raw.strip() or any(ord(ch) < 33 or ch == '\\' for ch in raw.strip()):
        raise ValueError('Missing or unsafe URL')
    url = raw.strip()
    if '://' not in url:
        url = 'https://' + url
    p = urlsplit(url)
    host = p.hostname or ''
    if (p.scheme != 'https' or not host or p.username or p.password or p.fragment or p.query or
        p.port not in (None, 443) or '%' in url or len(url) > 2048 or
        not host.isascii() or host.endswith('.') or '_' in host or
        host in {'localhost', 'metadata.google.internal'} or
        host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')) or
        '.' not in host):
        raise ValueError('Unsupported or unsafe website URL')
    try:
        ipaddress.ip_address(host)
        raise ValueError('IP-literal candidate not allowed')
    except ValueError as ex:
        if str(ex) == 'IP-literal candidate not allowed':
            raise
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host.lower()):
        raise ValueError('Invalid hostname')
    if any(part.startswith('-') or part.endswith('-') for part in host.split('.')):
        raise ValueError('Invalid hostname label')
    # Literal original host only: no credentials, query or port survive canonicalization.
    return urlunsplit(('https', host.lower(), p.path or '/', '', ''))


def candidate_from_registry(org: str, legal_name: str, response: SourceResponse) -> Candidate | None:
    """Obtain a diagnostic candidate. A serialized SourceResponse is not a trusted
    TLS receipt, and this function NEVER confers permission to publish claims.
    """
    if (not is_valid_orgnr(org) or not isinstance(legal_name, str) or not legal_name.strip() or
        not isinstance(response, SourceResponse) or response.module != 'registry_live' or
        response.url != endpoint('registry_live', org) or not verified_payload(response) or
        not isinstance(response.body, dict) or response.body.get('organisasjonsnummer') != org):
        return None
    name = response.body.get('navn')
    if not isinstance(name, str) or not name.strip() or normalize_legal_name(name) != normalize_legal_name(legal_name):
        return None
    homepage = response.body.get('hjemmeside')
    if not isinstance(homepage, str) or not homepage.strip():
        return None
    try:
        website_url = normalize_url(homepage)
    except (ValueError, TypeError):
        return None
    # Never upgrade caller-provided digest_kind into independently attested LIVE.
    return Candidate(org, legal_name, website_url, response.url,
                     response.content_sha256 or sha(response.body), response.retrieved_at,
                     'SYNTHETIC' if response.digest_kind == 'canonical-json' else 'UNATTESTED_SOURCE')


class _VisibleHTML(HTMLParser):
    """Keep coherent visible HTML records; NEVER flatten unrelated DOM blocks.

    CSS external stylesheets / browser visibility cannot be proven offline;
    therefore this is an evidence preflight only (all verdicts nonpublishable).
    """
    BLOCK = {'p', 'footer', 'address', 'div', 'section', 'article', 'li', 'h1', 'h2',
             'h3', 'header', 'main', 'td', 'tr', 'dl', 'dd', 'dt', 'aside'}
    SKIP = {'script', 'style', 'noscript', 'svg', 'template', 'head', 'iframe'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, list[str]]] = []
        self.records: list[tuple[str, str]] = []
        self.root: list[str] = []
        self.meta_refresh = False
        self.canonicals: list[str] = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == 'meta' and (data.get('http-equiv') or '').casefold() == 'refresh':
            self.meta_refresh = True
        if tag == 'link' and 'canonical' in (data.get('rel') or '').casefold():
            if isinstance(data.get('href'), str):
                self.canonicals.append(data['href'])
        style = re.sub(r'\s+', '', (data.get('style') or '').casefold())
        hidden = (tag in self.SKIP or 'hidden' in data or
                  (data.get('aria-hidden') or '').casefold() == 'true' or
                  'display:none' in style or 'visibility:hidden' in style or
                  (self.stack[-1][1] if self.stack else False))
        self.stack.append((tag, hidden, tag in self.BLOCK, []))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            return
        # HTMLParser is tolerant: unwind malformed nesting instead of leaking hidden context.
        pos = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if pos is None:
            return
        for _ in range(len(self.stack)-pos):
            name, hidden, block, data = self.stack.pop()
            if hidden or not data:
                continue
            txt = ' '.join(data)
            if block:
                self.records.append((name, txt))
            else:
                target = self.stack[-1][3] if self.stack else self.root
                target.append(txt)

    def handle_data(self, data):
        if not data.strip() or any(entry[1] for entry in self.stack):
            return
        if self.stack:
            self.stack[-1][3].append(data.strip())
        else:
            self.root.append(data.strip())

    def finish(self):
        while self.stack:
            self.handle_endtag(self.stack[-1][0])
        if self.root:
            self.records.append(('text', ' '.join(self.root)))


def _decode_html(raw: bytes) -> str | None:
    head = raw[:8192]
    try:
        head_text = head.decode('ascii', errors='ignore')
        m = re.search(r'<meta\b[^>]*charset\s*=\s*[\"\']?([a-zA-Z0-9_-]+)', head_text, re.I)
        declared = m.group(1).lower() if m else 'utf-8'
        encoding = {'utf8':'utf-8','utf-8':'utf-8','iso-8859-1':'iso-8859-1',
                    'latin1':'iso-8859-1','windows-1252':'cp1252','cp1252':'cp1252'}.get(declared)
        if encoding is None:
            return None
        return raw.decode(encoding, errors='strict')
    except UnicodeError:
        return None


def _valid_time(value: str, cutoff: str | None = None) -> bool:
    try:
        if not isinstance(value, str) or not value.strip():
            return False
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return False
        limit = datetime.now(timezone.utc)
        if cutoff is not None:
            cut = datetime.fromisoformat(cutoff.replace('Z', '+00:00'))
            if cut.tzinfo is None or cut.utcoffset() is None:
                return False
            limit = min(limit, cut)
        return instant <= limit
    except (ValueError, TypeError, OverflowError):
        return False


def verify_saved_page(candidate: Candidate, html: bytes | str, retrieved_at: str,
                      final_url: str | None = None, *, evidence_cutoff: str | None = None) -> Verdict:
    """Diagnostic classifier of UNTRUSTED saved bytes, never publication authority."""
    if not isinstance(candidate, Candidate):
        raise ValueError('Missing registry candidate')
    org = candidate.organisation_number
    url = candidate.url
    def v(status, reason, digest=None, nums=(), snippet=None):
        return Verdict(status, reason, org, url, retrieved_at, digest, tuple(nums), snippet, False)
    try:
        if (not is_valid_orgnr(org) or not isinstance(candidate.legal_name, str) or
            not candidate.legal_name.strip() or
            candidate.registry_source_url != endpoint('registry_live', org) or
            not isinstance(candidate.registry_source_sha256, str) or
            not re.fullmatch(r'[a-f0-9]{64}', candidate.registry_source_sha256) or
            candidate.source_mode not in ('SYNTHETIC', 'UNATTESTED_SOURCE') or
            normalize_url(url) != url or not _valid_time(candidate.registry_observed_at)):
            return v('blocked', 'unverifiable registry candidate')
        if final_url is not None and normalize_url(final_url) != url:
            return v('blocked', 'cross-origin or redirect URL not verified')
    except (ValueError, TypeError):
        return v('blocked', 'invalid registry or final URL')
    if not _valid_time(retrieved_at, evidence_cutoff):
        return v('blocked', 'invalid/future/post-cutoff retrieval time')
    raw = html.encode('utf-8') if isinstance(html, str) else html
    if not isinstance(raw, bytes) or len(raw) > _MAX_HTML:
        return v('blocked', 'HTML missing or exceeds bound')
    digest = hashlib.sha256(raw).hexdigest()
    decoded = _decode_html(raw)
    if decoded is None:
        return v('blocked', 'unsupported or invalid HTML encoding', digest)
    parser = _VisibleHTML()
    try:
        parser.feed(decoded)
        parser.finish()
    except (ValueError, RecursionError, TypeError) as exc:
        return v('blocked', f'invalid HTML parser: {type(exc).__name__}', digest)
    if parser.meta_refresh:
        return v('review', 'HTML meta redirect/interstitial requires re-acquisition', digest)
    for canonical in parser.canonicals:
        try:
            from urllib.parse import urljoin
            if normalize_url(urljoin(url, canonical)) != url:
                return v('review', 'canonical URL differs from verified candidate', digest)
        except (ValueError, TypeError):
            return v('review', 'unverifiable canonical URL', digest)
    records = parser.records
    flattened = ' '.join(t for _, t in records)
    allnums = set()
    for _, text in records:
        for match in _ORG_RE.finditer(text):
            n = re.sub(r'\D', '', match.group(1))
            if is_valid_orgnr(n):
                allnums.add(n)
    nums = tuple(sorted(allnums))
    lower = normalize_legal_name(flattened)
    if any(term in lower for term in ('access denied', 'captcha', 'verify you are human', 'attention required')):
        return v('review', 'access-denied or challenge/interstitial page', digest, nums)
    # A domain-sale instruction in the homepage's primary visible area is relevant,
    # but a newspaper/article quotation is NOT an identity counterexample.
    for tag, txt in records:
        if tag in {'h1', 'header', 'main', 'text'} and any(k in normalize_legal_name(txt) for k in _PARKED):
            return v('rejected', 'site advertises a parked or for-sale domain', digest, nums)
    if not nums:
        return v('candidate_only', 'no explicit valid legal organisation number', digest)
    if org not in nums:
        return v('rejected', 'no matching target organisation number', digest, nums)
    name = normalize_legal_name(candidate.legal_name)
    exact = None
    for tag, txt in records:
        for match in _ORG_RE.finditer(txt):
            n = re.sub(r'\D', '', match.group(1))
            if n != org or not is_valid_orgnr(n):
                continue
            context = txt[max(0, match.start()-180):match.end()+80]
            norm = normalize_legal_name(context)
            if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', norm):
                # A nearby description of another organisation's customer/partner
                # cannot prove that the pair belongs to the website operator.
                if re.search(r'\b(?:customer|client|kunde|partner)\b', norm) and tag not in {'footer','address'}:
                    continue
                exact = context[:340]
                break
        if exact:
            break
    if exact is None:
        if len(nums) > 1 and any(x in lower for x in ('group entities', 'subsidiar', 'konsern', 'datterselskap')):
            return v('group_review', 'multi-entity group identity unresolved', digest, nums)
        return v('ambiguous', 'name and organisation number not paired in one visible legal record', digest, nums)
    if len(nums) > 1 and (any(x in lower for x in ('subsidiar', 'datterselskap', 'konsern', 'group of companies')) or
                          any(len({re.sub(r'\D','',m.group(1)) for m in _ORG_RE.finditer(txt)}) > 1 for _, txt in records)):
        return v('group_review', 'multi-entity group or overlapping legal disclosure', digest, nums, exact)
    if any(x in lower for x in ('subsidiaries', 'subsidiary', 'konsernet', 'group with', 'group of', 'group companies')):
        return v('group_review', 'group scope not exact legal entity', digest, nums, exact)
    return v('exact_page_identity', 'legal name and ID corroborated in one visible record; not claim authorization', digest, nums, exact)
