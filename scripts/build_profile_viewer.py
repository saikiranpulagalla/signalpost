#!/usr/bin/env python3
"""Build an offline, evidence-first viewer from runner profile JSONL."""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def render(profiles: list[dict], *, observation_key: bytes | None = None) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from norway_company_agent.profile_integrity import verify_history
    for profile in profiles:
        verify_history(profile, observation_key)
    payload = json.dumps(profiles, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(f"Signalpost research viewer — {len(profiles)} companies")
    return f"""<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title>
<style>body{{font:16px system-ui;margin:0;background:#f6f7fb;color:#172033}}header,main{{max-width:1100px;margin:auto;padding:1rem}}input{{width:100%;padding:.8rem;font:inherit;box-sizing:border-box}}article{{background:white;margin:1rem 0;padding:1rem;border-radius:.6rem;box-shadow:0 1px 4px #0002;overflow-wrap:anywhere}}h1,h2,h3{{margin:.2rem 0 .6rem}}.meta,.unknown{{color:#5b6475;font-size:.9em}}.chip{{display:inline-block;padding:.18rem .45rem;border-radius:1rem;background:#e8eefc;margin:.15rem}}details{{margin:.6rem 0}}a{{color:#0759b5;overflow-wrap:anywhere}}ul{{padding-left:1.2rem}}table{{display:block;overflow-x:auto;width:100%}}th,td{{text-align:left;padding:.4rem}}</style>
<header><h1>{title}</h1><p class=\"meta\">Local verified-profile viewer. Synthesis is deterministic and every fact retains claim/source references. This file makes no network requests.</p><input id=\"q\" placeholder=\"Search organisation number or company name\"><button id=\"compare\" type=\"button\">Compare visible companies</button></header><main id=\"comparison\"></main><main id=\"app\"></main>
<script type=\"application/json\" id=\"profiles\">{payload}</script><script>
const data=JSON.parse(document.getElementById('profiles').textContent), app=document.getElementById('app'), q=document.getElementById('q'), comparison=document.getElementById('comparison');
const esc=x=>String(x??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\\"':'&quot;'}}[c]));
const val=x=>typeof x==='object'?JSON.stringify(x):String(x??'');
function link(u,label='source'){{try{{let url=new URL(u);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw Error();return `<a href=\"${{esc(url.href)}}\" rel=\"noreferrer noopener\">${{esc(label)}}</a>`}}catch{{return '<span class=\"unknown\">No safe source link</span>'}}}}
function source(v){{return `${{link(v.source_url)}} <span class=\"meta\">retrieved ${{esc(v.retrieved_at)}} · effective ${{esc(v.effective_at||'not supplied')}} · SHA-256 ${{esc(v.content_sha256||'not retained')}}</span>`}}
function claim(c,e){{return `<li id=\"${{esc(c.id)}}\"><b>${{esc(c.field)}}</b>: ${{esc(val(c.value))}} <span class=\"meta\">${{esc(val(c.reporting_period||''))}} · ${{esc(c.scope||'')}} · ${{esc(c.id)}}</span> ${{(c.evidence_fields||[]).map(k=>e[k]?source(e[k]):'<span>Evidence unavailable</span>').join(' ')}}</li>`}}
function history(p){{let rows=p.changes||[];return `<h3>Verified change history</h3><ul>${{rows.map(c=>`<li><b>${{esc(c.field)}}</b> (${{esc(c.classification||'unclassified')}}): ${{esc(val(c.old_value))}} → ${{esc(val(c.new_value))}} <div>Previous evidence: ${{c.previous_evidence?source(c.previous_evidence):'unavailable; not comparable'}}</div><div>Current evidence: ${{c.current_evidence?source(c.current_evidence):'unavailable; not comparable'}}</div><span class=\"meta\">${{esc(c.id)}}</span></li>`).join('')||'<li>No verified material changes supplied.</li>'}}</ul>`}}
function retained(p){{return `<details><summary>Historical / last-known-good observations (not current research)</summary><ul>${{(p.historical_observations||[]).map(x=>`<li><b>${{esc(x.module)}}</b>: ${{esc(val(x.evidence.value))}} ${{source(x.evidence)}}</li>`).join('')||'<li>No authenticated prior observations supplied.</li>'}}</ul></details>`}}
function card(p){{
 let r=p.research_result||{{claims:[],availability:{{}}}}, s=p.synthesis||{{sentences:[]}}, e=p.evidence||{{}};
 let synth=s.sentences.map(x=>`<li>${{esc(x.text)}} <span class=\"meta\">${{(x.claim_ids||[]).map(id=>`<a href=\"#${{esc(id)}}\">${{esc(id)}}</a>`).join(' ')}} ${{esc((x.change_ids||[]).join(', '))}}</span></li>`).join('')||'<li>No supported synthesis facts.</li>';
 let sources=Object.entries(e).map(([k,v])=>`<li><b>${{esc(k)}}</b> <span class=\"chip\">${{esc(v.status)}}</span> ${{source(v)}} <span class=\"meta\">${{esc(v.completeness||'')}} ${{esc(v.note||'')}}</span></li>`).join('');
 return `<article><h2>${{esc(p.name||'Unknown legal entity')}}</h2><div class=\"meta\">Organisation number: ${{esc(p.organisation_number)}}</div><h3>Evidence-grounded overview</h3><ul>${{synth}}</ul><h3>Verified claims</h3><ul>${{r.claims.map(c=>claim(c,e)).join('')||'<li>None</li>'}}</ul>${{history(p)}}${{retained(p)}}<details><summary>Sources and availability</summary><ul>${{sources}}</ul></details></article>`
}}
function visible(){{let needle=q.value.toLowerCase();return data.filter(p=>`${{p.organisation_number}} ${{p.name||''}}`.toLowerCase().includes(needle))}}
function draw(){{let rows=visible();app.innerHTML=rows.map(card).join('')||'<article>No matching profile.</article>';comparison.innerHTML=''}}q.oninput=draw;document.getElementById('compare').onclick=()=>{{let rows=visible();if(rows.length<2){{comparison.innerHTML='<article>Select at least two companies to compare.</article>';return}}let cells=rows.map(p=>{{let c=(p.research_result||{{claims:[]}}).claims||[];return `<tr><td>${{esc(p.name||'Unknown')}}</td><td>${{esc(p.organisation_number)}}</td><td>${{c.filter(x=>x.field==='registered_role').length}}</td><td>${{c.filter(x=>String(x.field).startsWith('financial_')).length}}</td><td>${{c.filter(x=>x.field==='registered_subunit').length}}</td></tr>`}}).join('');comparison.innerHTML=`<article><h2>Visible-company comparison</h2><table><tr><th>Company</th><th>Organisation</th><th>Roles</th><th>Financial facts</th><th>Subunits</th></tr>${{cells}}</table></article>`}};draw();
</script></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local Signalpost research viewer")
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observation-key", type=Path, help="Private local history authentication key; never publish")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(_load(args.profiles), observation_key=args.observation_key.read_bytes() if args.observation_key else None), encoding="utf-8")


if __name__ == "__main__":
    main()
