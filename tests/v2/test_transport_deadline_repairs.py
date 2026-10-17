"""Absolute I/O bounds with real stdlib readers, without socket connections."""
from __future__ import annotations
import http.client
import io
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from norway_company_agent.http import fetch_json, transport_stop_reason
from norway_company_agent.network_policy import ServiceCooldown
from norway_company_agent.telemetry import classify_outcome

URL = "https://data.brreg.no/enhetsregisteret/api/enheter/923609016"


def response(payload=b'{"ok":true}', *, delay=0, status=200, headers=b''):
    class Raw(io.RawIOBase):
        def __init__(self):
            self.head = bytearray(f"HTTP/1.1 {status} OK\r\nContent-Length: {len(payload)}\r\n".encode() + headers + b'\r\n')
            self.body = bytearray(payload)
        def readable(self):
            return True
        def readinto(self, buffer):
            if self.head:
                size = min(len(buffer), len(self.head))
                buffer[:size] = self.head[:size]; del self.head[:size]
                return size
            if not self.body:
                return 0
            if delay:
                time.sleep(delay)
            size = 1 if delay else min(len(buffer), len(self.body))
            buffer[:size] = self.body[:size]; del self.body[:size]
            return size
    class Sock:
        def makefile(self, *args, **kwargs):
            return io.BufferedReader(Raw())
    result = http.client.HTTPResponse(Sock())
    result.begin()
    return result


class TransportDeadlineRepairs(unittest.TestCase):
    def setUp(self):
        self.guard = patch('socket.create_connection', side_effect=AssertionError('NETWORK FORBIDDEN'))
        self.guard.start(); self.addCleanup(self.guard.stop)
        self.guard2 = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK FORBIDDEN'))
        self.guard2.start(); self.addCleanup(self.guard2.stop)
        end = time.monotonic() + 1
        while transport_stop_reason(URL) and time.monotonic() < end:
            time.sleep(.005)
        self.assertIsNone(transport_stop_reason(URL))

    def fetch(self, reply, **options):
        class Opener:
            def open(self, *args, **kwargs):
                return reply() if callable(reply) else reply
        with patch('urllib.request.build_opener', return_value=Opener()):
            return fetch_json(URL, policy='authorized_public_brreg', allow_redirects=False, attempts=1, **options)

    def test_bounded_success_and_oversized_body(self):
        good = self.fetch(response(), timeout=.5, max_bytes=100)
        self.assertEqual(good.body, {'ok': True}); self.assertTrue(good.body_complete)
        large = self.fetch(response(b'x' * 1000), timeout=.5, max_bytes=50)
        self.assertEqual(large.error, 'ResponseTooLarge')
        self.assertIsNone(large.body); self.assertLessEqual(large.bytes_received, 51)

    def test_physical_start_callback_runs_only_at_admitted_opener_boundary(self):
        calls = []
        def start():
            calls.append('start')
        good = self.fetch(response(), timeout=.5, on_request_start=start)
        self.assertTrue(good.request_started)
        self.assertEqual(calls, ['start'])
        calls.clear()
        denied = self.fetch(response(), timeout=.5, on_request_start=lambda: 'source request budget exhausted')
        self.assertFalse(denied.request_started)
        self.assertEqual(denied.error, 'source request budget exhausted')
        expired = self.fetch(response(), timeout=.5, deadline=time.monotonic() - 1, on_request_start=start)
        self.assertFalse(expired.request_started)
        offline = fetch_json(URL, on_request_start=start)
        self.assertFalse(offline.request_started)
        self.assertEqual(calls, [])

    def test_real_httpresponse_slow_drip_absolute_deadline(self):
        start = time.monotonic()
        result = self.fetch(response(b'{"ok":true}', delay=.015), timeout=.045)
        self.assertLess(time.monotonic() - start, .2)
        self.assertEqual(result.status, 200); self.assertEqual(result.error, 'TimeoutError')
        self.assertGreaterEqual(result.elapsed_ms, 30); self.assertGreater(result.bytes_received, 0)
        self.assertFalse(result.body_complete); self.assertIsNone(result.body)

    def test_delayed_headers_isolated_and_host_quarantined(self):
        def delayed():
            time.sleep(.15)
            return response()
        start = time.monotonic()
        result = self.fetch(delayed, timeout=.025)
        self.assertLess(time.monotonic() - start, .12)
        self.assertEqual(result.error, 'TimeoutError')
        self.assertEqual(transport_stop_reason(URL), 'transport service stopped')
        denied = self.fetch(response(), timeout=.1)
        self.assertEqual(denied.error, 'transport service stopped')
        time.sleep(.17)
        self.assertIsNone(transport_stop_reason(URL))

    def test_remaining_batch_deadline_wins_over_request_timeout(self):
        start = time.monotonic()
        result = self.fetch(response(delay=.02), timeout=2, deadline=start + .04)
        self.assertEqual(result.error, 'TimeoutError')
        self.assertLess(time.monotonic() - start, .2)

    def test_truncated_json_retains_observed_status_bytes_and_time(self):
        result = self.fetch(response(b'{"bad":', delay=.004), timeout=.5)
        self.assertEqual(result.status, 200); self.assertEqual(result.bytes_received, 7)
        self.assertGreater(result.elapsed_ms, 10); self.assertEqual(result.error, 'JSONDecodeError')
        self.assertEqual(classify_outcome(result.status, result.error), 'transport_error')
        self.assertIsNotNone(result.content_sha256); self.assertIsNone(result.body)

    def test_error_body_is_bounded_and_retry_after_seen_before_body(self):
        marked = []
        slow = response(b'x' * 100, delay=.01)
        error = urllib.error.HTTPError(URL, 429, 'limited', {'Retry-After': '600'}, slow)
        def fail():
            raise error
        result = self.fetch(fail, timeout=.04, on_rate_limit=marked.append)
        self.assertEqual(marked, ['600']); self.assertEqual(result.status, 429)
        self.assertEqual(result.retry_after, '600'); self.assertEqual(result.error, 'TimeoutError')

    def test_connection_interruption_and_elapsed_timeout(self):
        def timeout():
            time.sleep(.025); raise TimeoutError()
        result = self.fetch(timeout, timeout=.5)
        self.assertEqual(result.error, 'TimeoutError'); self.assertGreater(result.elapsed_ms, 0)
        def interrupted():
            raise ConnectionResetError()
        result = self.fetch(interrupted, timeout=.5)
        self.assertEqual(result.error, 'ConnectionResetError')

    def test_service_cooldown_is_host_scoped_and_missing_policy_stops_run(self):
        gate = ServiceCooldown(); gate.limited(URL, '600')
        self.assertTrue(gate.blocked(URL.replace('/enheter/923609016', '/enheter/923609016/roller')))
        self.assertFalse(gate.blocked('https://other.invalid/test'))
        gate = ServiceCooldown(); gate.limited(URL, None); self.assertTrue(gate.blocked(URL))

    def test_actual_two_worker_429_stops_subsequent_waves(self):
        harness = r'''
import importlib.util,sys,socket,urllib.request,time,threading
def forbidden(*a,**k):raise AssertionError('NETWORK FORBIDDEN')
socket.create_connection=forbidden;socket.socket.connect=forbidden;urllib.request.OpenerDirector.open=forbidden
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
from norway_company_agent.http import FetchResult
lock=threading.Lock(); starts=[]; barrier=threading.Barrier(2)
def fetch(url,**kwargs):
 with lock:starts.append(url); number=len(starts)
 if number<=2:barrier.wait(timeout=3)
 if number==1:return FetchResult(url,429,10,0,error='HTTP 429',retry_after='600',retrieved_at='2026-10-01T00:00:00Z')
 time.sleep(.03)
 return FetchResult(url,404,30,0,error='HTTP 404',retrieved_at='2026-10-01T00:00:00Z')
r.fetch_json=fetch;sys.argv=sys.argv[1:]
try:r.main()
finally:print('PHYSICAL_FIXTURE_STARTS='+str(len(starts)))
'''
        (ROOT / '.release-gate-tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-gate-tmp') as temporary:
            d = Path(temporary)
            batch = d/'input.jsonl'; bulk=d/'registry.csv'
            orgs = ['923609016', '993578843', '910608193']
            batch.write_text(''.join(json.dumps({'organisation_number': org})+'\n' for org in orgs))
            bulk.write_text('organisasjonsnummer,navn,organisasjonsform.kode\n'+''.join(org+',Synthetic,AS\n' for org in orgs))
            p = subprocess.run([sys.executable,'-S','-c',harness,str(ROOT/'scripts/run_competition_batch.py'),
                '--organisations',str(batch),'--bulk',str(bulk),'--modules','registry,accounting_obligation,financials,roles',
                '--network-policy','authorized_public_brreg','--confirm-authorized-live-sources','--workers','2',
                '--max-source-requests','6','--source-attempts','1','--output-target','starter_compatible',
                '--profiles-output',str(d/'profiles.jsonl'),'--output',str(d/'envelopes.jsonl'),
                '--report',str(d/'report.json'),'--run-id','cooldown'],capture_output=True,text=True,cwd=ROOT,timeout=15)
            self.assertNotEqual(p.returncode,0)  # Promised but unattempted work remains fail-closed.
            self.assertIn('PHYSICAL_FIXTURE_STARTS=2',p.stdout, p.stderr)
            report=json.loads((d/'report.json').read_text())
            self.assertEqual(report['source_request_policy']['source_requests_started'],2)
            self.assertFalse((d/'envelopes.jsonl').exists())
            profiles=[json.loads(l) for l in (d/'profiles.jsonl').read_text().splitlines()]
            self.assertTrue(any(e.get('status')=='not_fetched' for x in profiles for e in x['evidence'].values()))

    def test_actual_transport_quarantine_reconciles_physical_starts(self):
        harness = r'''
import importlib.util,sys,socket,urllib.request,time,json
def forbidden(*a,**k):raise AssertionError('NETWORK FORBIDDEN')
socket.create_connection=forbidden;socket.socket.connect=forbidden
spec=importlib.util.spec_from_file_location('runner',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
starts=[]
class Opener:
 def open(self,*a,**k):
  starts.append(1);time.sleep(.4);raise TimeoutError()
urllib.request.build_opener=lambda *a,**k:Opener()
sys.argv=sys.argv[1:]
try:r.main()
finally:print('ACTUAL_OPENER_STARTS='+str(len(starts)))
'''
        (ROOT / '.release-gate-tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-gate-tmp') as temporary:
            d=Path(temporary)
            p=subprocess.run([sys.executable,'-S','-c',harness,str(ROOT/'scripts/run_competition_batch.py'),
                '--organisations','tests/v0/demo/organisations.jsonl','--bulk','tests/v0/demo/frozen-registry.csv',
                '--modules','registry,accounting_obligation,financials,roles','--network-policy','authorized_public_brreg',
                '--confirm-authorized-live-sources','--workers','1','--source-timeout-seconds','.03','--source-attempts','1',
                '--max-source-requests','4','--output-target','starter_compatible','--profiles-output',str(d/'profiles.jsonl'),
                '--output',str(d/'envelopes.jsonl'),'--report',str(d/'report.json'),
                '--request-telemetry',str(d/'telemetry.jsonl'),'--run-id','physical-boundary'],
                capture_output=True,text=True,cwd=ROOT,timeout=10)
            self.assertNotEqual(p.returncode,0,p.stdout)
            self.assertIn('ACTUAL_OPENER_STARTS=1',p.stdout,p.stderr)
            report=json.loads((d/'report.json').read_text())
            self.assertEqual(report['source_request_policy']['source_requests_started'],1)
            telemetry=[json.loads(l) for l in (d/'telemetry.jsonl').read_text().splitlines()]
            self.assertEqual(sum(t.get('physical_source_request_number') is not None for t in telemetry),1)


if __name__ == '__main__':
    unittest.main()
