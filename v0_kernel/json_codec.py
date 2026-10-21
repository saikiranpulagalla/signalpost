"""Strict finite-number JSON boundaries; finite representations stay unchanged."""
import json
import math

class StrictJSONError(ValueError):
    """A source is not representable as portable finite-number JSON."""

def _constant(token):
    raise StrictJSONError('Non-finite JSON token: '+token)

def _float(token):
    value=float(token)
    if not math.isfinite(value):
        raise StrictJSONError('JSON number exceeds finite float range')
    return value

def loads(value):
    return json.loads(value,parse_constant=_constant,parse_float=_float)

def dumps(value,**options):
    options['allow_nan']=False
    return json.dumps(value,**options)
