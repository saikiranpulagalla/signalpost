"""Bounded RFC 9309 core Allow/Disallow matcher (stdlib, no network).

Merge groups of equal longest product-token match; choose longest matching
path rule, Allow wins a same-length tie. Unsupported ambiguous directives and
policies fail closed at the network boundary.
"""
from __future__ import annotations
import re
from urllib.parse import unquote, urlsplit

MAX_ROBOTS_BYTES = 64 * 1024


def _normalize_path(value: str) -> str:
    # Normalize valid percent triplets of unreserved ASCII, retain reserved
    # escapes to avoid false permission decisions on a server-dependent path.
    if '%' in value and re.search(r'%(?![A-Fa-f0-9]{2})', value):
        raise ValueError('malformed percent encoding')
    def repl(m):
        v = chr(int(m.group(1), 16))
        return v if re.fullmatch('[a-zA-Z0-9._~-]', v) else '%' + m.group(1).upper()
    return re.sub(r'%([a-fA-F0-9]{2})', repl, value)


def _match(rule: str, path: str) -> bool:
    end = rule.endswith('$')
    if end:
        rule = rule[:-1]
    normalized = _normalize_path(rule)
    expr = '^' + ''.join('.*' if c == '*' else re.escape(c) for c in normalized)
    if end:
        expr += '$'
    return bool(re.match(expr, path))


def robots_permission(raw: bytes, *, user_agent: str, url: str) -> tuple[bool, str]:
    if not isinstance(raw, bytes) or len(raw) > MAX_ROBOTS_BYTES:
        return False, 'invalid_or_oversized_policy'
    try:
        lines = raw.decode('utf-8', errors='strict').splitlines()
        parsed = urlsplit(url)
        path = _normalize_path(parsed.path or '/')
        if parsed.query:
            path += '?' + _normalize_path(parsed.query)
    except (UnicodeError, ValueError, TypeError):
        return False, 'malformed_policy_or_path'
    groups: list[dict] = []
    group = None
    seen_rules = False
    for line in lines:
        line = line.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        key, val = line.split(':', 1)
        key, val = key.strip().casefold(), val.strip()
        if key == 'user-agent':
            if not val or len(val) > 128:
                return False, 'malformed_user_agent'
            if group is None or seen_rules:
                group = {'agents':[], 'rules':[], 'pacing':False}
                groups.append(group)
                seen_rules = False
            group['agents'].append(val.casefold())
        elif group is not None and key in ('allow', 'disallow'):
            seen_rules = True
            if val:
                if not val.startswith('/'):
                    return False, 'malformed_rule'
                group['rules'].append((key == 'allow', val))
        elif group is not None and key in ('crawl-delay', 'request-rate'):
            seen_rules = True
            group['pacing'] = True
    token = user_agent.casefold().split('/', 1)[0].split(' ', 1)[0]
    choices = []
    for item in groups:
        strength = max((len(a) if a != '*' and a in token else 0 for a in item['agents']), default=0)
        if '*' in item['agents'] and strength == 0:
            strength = -1
        if strength:
            choices.append((strength, item))
    if not choices:
        return True, 'no_applicable_group'
    highest = max(strength for strength, _ in choices)
    selected = [g for strength, g in choices if strength == highest]
    if any(g['pacing'] for g in selected):
        return False, 'pacing_policy_requires_scheduler'
    matches = []
    for g in selected:
        for allow, rule in g['rules']:
            try:
                if _match(rule, path):
                    # Specificity: non-wildcard length; byte length for UTF-8.
                    weight = len(rule.rstrip('$').replace('*','').encode('utf-8'))
                    matches.append((weight, allow))
            except (ValueError, re.error):
                return False, 'malformed_rule'
    if not matches:
        return True, 'no_matching_rule'
    specificity = max(n for n, _ in matches)
    if any(allow for n, allow in matches if n == specificity):
        return True, 'most_specific_allow'
    return False, 'most_specific_disallow'
