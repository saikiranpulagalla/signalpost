"""Deterministic presentation of already validated claims; no research or inference."""
from __future__ import annotations
from datetime import date
import math


def build_synthesis(result: dict, changes=()) -> dict:
    fields = {}
    for claim in result.get("claims", []):
        fields.setdefault(claim["field"], []).append(claim)
    sentences = []
    def add(section, text, claims=(), **metadata):
        sentences.append({"section": section, "text": text,
                          "claim_ids": [c["id"] for c in claims], **metadata})
    name, form = fields.get("legal_name", []), fields.get("legal_form", [])
    if name:
        add("Company overview", f"Registered entity: {name[0]['value']}" +
            (f" ({form[0]['value']})" if form else "") + ".", name[:1]+form[:1])
    industry = fields.get("industry", [])
    if industry:
        value = industry[0]["value"]
        label = (value.get("description") or value.get("code")) if isinstance(value, dict) else value
        if label: add("Business and locations", f"Registered industry: {label}.", industry[:1])
    for field, label in (("registered_activity", "Registered activity declaration"),
                         ("registered_purpose", "Registered legal purpose")):
        for c in fields.get(field, [])[:1]:
            value = c.get("value")
            if isinstance(value, list) and value and all(isinstance(s, str) for s in value):
                value = " / ".join(value)
            if isinstance(value, str) and value.strip():
                add("Registered business declarations", f"{label}: {value}. This is a registry declaration, not verification of actual operations.", [c])
    for c in fields.get("registered_name_history", [])[:1]:
        for item in c["value"][:3]:
            add("Registered name history", f"Registered former legal name: {item['name']} (source interval {item['valid_from']} to {item['valid_to']}; timezone not supplied). This is a historical registry record, not the current legal name or a newly observed change.", [c])
    roles = [c for c in fields.get("registered_role", []) if isinstance(c["value"], dict) and not c["value"].get("inactive")]
    for c in roles[:3]:
        v=c["value"]
        if v.get("name") and (v.get("role") or v.get("role_code")):
            add("Leadership and registered roles", f"Registered {v.get('role') or v['role_code']}: {v['name']}" +
                (f" ({v['group']})" if v.get("group") else "") + ".", [c])
    def period(c):
        p=c.get("reporting_period")
        if not isinstance(p, dict): return None
        try:
            a,b=date.fromisoformat(p["fraDato"]),date.fromisoformat(p["tilDato"])
            return (a,b) if a<=b else None
        except (KeyError,ValueError,TypeError): return None
    def usable(c):
        v=c.get("value")
        return isinstance(v,dict) and c.get("scope")=="exact_legal_entity" and v.get("account_type")=="SELSKAP" and period(c) and isinstance(v.get("amount"),(int,float)) and not isinstance(v["amount"],bool) and math.isfinite(v["amount"]) and v.get("currency")
    financial=[c for key,cs in fields.items() if key.startswith("financial_") for c in cs if usable(c)]
    latest=max((period(c)[1] for c in financial), default=None)
    for field,label in (("financial_revenue","Operating income"),("financial_operating_result","Operating result"),("financial_annual_result","Annual result")):
        cs=sorted([c for c in fields.get(field,[]) if usable(c)],key=lambda c:period(c)[1],reverse=True)
        if not cs or period(cs[0])[1]!=latest: continue
        c=cs[0];v=c["value"];a,b=period(c)
        scale=f", unit {v['unit']}" if v.get("unit") else ", source amount; scale not separately specified"
        # str(int) is exact; str(float) round-trips the existing value without
        # inventing decimal precision or silently reducing it to six digits.
        add("Financial position", f"{label}: {v['amount']} {v['currency']}{scale}; {a} to {b} (SELSKAP, exact legal entity).", [c])
        # Compare only explicitly stated compatible units and consecutive calendar years.
        if len(cs)>1 and v.get("unit"):
            old=cs[1];ov=old["value"];pa,pb=period(old)
            if (a.month,a.day,b.month,b.day)==(1,1,12,31) and (pa.month,pa.day,pb.month,pb.day)==(1,1,12,31) and a.year==b.year==pb.year+1 and pa.year==pb.year and (ov.get("currency"),ov.get("unit"),old.get("scope"),ov.get("account_type"))==(v["currency"],v["unit"],c["scope"],v["account_type"]):
                approximation = "approximately " if isinstance(v['amount'], float) or isinstance(ov['amount'], float) else ""
                add("Financial comparison", f"{label} source-amount difference from {pb.year}: {approximation}{v['amount']-ov['amount']} {v['currency']} ({v['unit']}); no inference about company health.", [old,c])
    for c in fields.get("registered_subunit",[])[:2]:
        v=c["value"];address=v.get("address") or {}
        add("Business and locations", f"Registered subunit: {v.get('name') or v['organisation_number']} ({v['organisation_number']})" +
            (f", {address['poststed']}" if isinstance(address,dict) and address.get("poststed") else "") + ".", [c])
    for c in fields.get("corporate_relationship",[])[:2]:
        v=c["value"];relation=v.get("declared_relationship") or v.get("relation")
        if isinstance(relation,dict): relation=relation.get("beskrivelse") or relation.get("kode")
        if relation:
            add("Corporate relationships", f"Source-declared relationship {relation}: {v.get('name') or v['organisation_number']} ({v['organisation_number']}).", [c])
    for change in changes:
        if not isinstance(change,dict) or not change.get("previous_evidence") or not change.get("current_evidence"): continue
        add("Recent verified changes", f"Verified {change.get('field')} change: {change.get('old_value')} → {change.get('new_value')}.",
            name[:1] if change.get("field")=="registry.name" else [], change_ids=[change["id"]], evidence_fields=["previous_evidence","current_evidence"])
    meanings={"not_found":"checked source returned no relevant records", "not_fetched":"not attempted/completed; no absence finding", "not_scheduled":"not selected", "source_error":"source operation failed", "blocked":"source blocked", "ambiguous":"attribution uncertain", "not_applicable":"not applicable"}
    if result.get("availability",{}).get("financials")=="available" and not financial:
        add("Unknowns and limitations", "The checked financial source supplied no publishable exact-entity financial amounts in this profile.", source_fields=["financials"])
    for family,state in sorted(result.get("availability",{}).items()):
        if state!="available": add("Unknowns and limitations", f"{family}: {meanings.get(state,'unconfirmed source state')}.", source_fields=[family])
    families={"financials":"financial_", "roles":"registered_role", "locations":"registered_subunit", "group":"corporate_relationship"}
    absent=[family for family,prefix in families.items() if family not in result.get("availability",{}) and not any(f.startswith(prefix) for f in fields)]
    if absent: add("Unknowns and limitations", "Not researched in this profile: " + ", ".join(absent) + ".")
    return {"method":"deterministic_extractive_v2", "sentences":sentences}
