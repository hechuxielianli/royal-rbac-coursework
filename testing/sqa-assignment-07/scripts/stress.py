"""Closed-loop HTTP stress and recovery experiment; dedicated local database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import threading
import time

import requests
import psutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence'

def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered)*p)-1)] if ordered else None

def server_resources(pid):
    parent = psutil.Process(pid)
    processes = [parent, *parent.children(recursive=True)]
    cpu = sum(p.cpu_times().user + p.cpu_times().system for p in processes)
    rss = sum(p.memory_info().rss for p in processes)
    return cpu, rss

def server(db, port):
    sys.path.insert(0, str(ROOT / 'sut'))
    from app import create_app
    from waitress import serve
    app = create_app({'DATABASE': db, 'SECRET_KEY': 'stress-isolated-secret'})
    serve(app, host='127.0.0.1', port=port, threads=4, connection_limit=1200,
          backlog=1200, channel_timeout=30)

def run():
    port = 5088
    db = OUT / ('stress-' + datetime.now().strftime('%H%M%S') + '.sqlite3')
    log = (OUT/'stress-server.log').open('w', encoding='utf-8')
    process = subprocess.Popen([sys.executable, __file__, '--server', str(db), '--port', str(port)], stdout=log, stderr=log,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                if requests.get(base+'/healthz', timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        auth = requests.Session()
        token = auth.get(base+'/api/bootstrap').json()['csrf_token']
        login = auth.post(base+'/api/login', json={'username':'henry','password':'Kingdom@2026'}, headers={'X-CSRF-Token':token})
        login.raise_for_status()
        token = login.json()['csrf_token']
        cookies = auth.cookies.get_dict()
        initial_swords = auth.get(base+'/api/state').json()['business']['swords']
        rows, stages, serial = [], [], 0
        sequence_lock = threading.Lock()
        experiment_start = time.perf_counter()
        for name, vus, duration in [('baseline',1,6),('step4',4,6),('step16',16,8),('step64',64,8),('step160',160,10),('peak320',320,10),('recovery',1,15)]:
            barrier = threading.Barrier(vus+1)
            stage_rows = []
            lock = threading.Lock()
            started = time.perf_counter()
            cpu0, _ = server_resources(process.pid)
            deadline = started + duration
            def worker(vu):
                nonlocal serial
                client = requests.Session()
                client.cookies.update(cookies)
                barrier.wait()
                local = []
                while time.perf_counter() < deadline:
                    with sequence_lock:
                        serial += 1
                        seq = serial
                    write = seq % 2 == 0
                    path = '/api/actions/forge_sword' if write else '/api/pages/tax_scroll'
                    t0 = time.perf_counter()
                    status, correct, event_id, error = 0, False, '', ''
                    try:
                        response = client.post(base+path,json={},headers={'X-CSRF-Token':token},timeout=20) if write else client.get(base+path,timeout=20)
                        status = response.status_code
                        payload = response.json()
                        correct = status == 200 and payload.get('ok') is True and payload.get('event',{}).get('allowed') is True
                        event_id = payload.get('event',{}).get('id','')
                    except Exception as exc:
                        error = type(exc).__name__
                    local.append(dict(stage=name,vu=vu,sequence=seq,start_s=round(t0-experiment_start,5),latency_ms=round((time.perf_counter()-t0)*1000,3),write=write,status=status,correct=correct,event_id=event_id,error=error))
                client.close()
                with lock:
                    stage_rows.extend(local)
            with ThreadPoolExecutor(max_workers=vus) as pool:
                futures = [pool.submit(worker,i) for i in range(vus)]
                started = time.perf_counter()
                deadline = started + duration
                barrier.wait()
                for future in futures:
                    future.result()
            elapsed = time.perf_counter()-started
            cpu1, rss = server_resources(process.pid)
            latency = [r['latency_ms'] for r in stage_rows]
            errors = sum(not r['correct'] for r in stage_rows)
            record = dict(stage=name,vus=vus,hold_seconds=duration,wall_seconds=round(elapsed,3),drain_seconds=round(max(0,elapsed-duration),3),requests=len(stage_rows),rps=round(len(stage_rows)/elapsed,2),p50_ms=percentile(latency,.5),p95_ms=percentile(latency,.95),p99_ms=percentile(latency,.99),errors=errors,error_rate=errors/len(stage_rows),server_cpu_seconds=round(cpu1-cpu0,3),server_rss_mb=round(rss/1024**2,2))
            record['slo_pass'] = record['p95_ms']<500 and record['error_rate']<.01
            rows.extend(stage_rows)
            stages.append(record)
            print(json.dumps(record),flush=True)
        with sqlite3.connect(db) as conn:
            swords = conn.execute('SELECT swords FROM kingdom_state').fetchone()[0]
            ledger = conn.execute("SELECT event_id FROM business_ledger WHERE action='forge_sword'").fetchall()
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        successful_write_ids = {r['event_id'] for r in rows if r['write'] and r['correct']}
        stored_ids = {r[0] for r in ledger}
        recovery = [r for r in rows if r['stage']=='recovery']
        limit = max(50,2*stages[0]['p95_ms'])
        recovery_start = min(r['start_s'] for r in recovery)
        buckets = []
        for second in range(15):
            values = [r['latency_ms'] for r in recovery if second<=r['start_s']-recovery_start<second+1]
            buckets.append(dict(second=second,p95_ms=percentile(values,.95),requests=len(values)))
        restored = next((i+3 for i in range(len(buckets)-2) if all(b['p95_ms'] is not None and b['p95_ms']<=limit for b in buckets[i:i+3])),None)
        result = dict(completed_utc=datetime.now(timezone.utc).isoformat(),python=sys.version,platform=platform.platform(),cpu=platform.processor(),logical_cpus=os.cpu_count(),ram_gb=round(psutil.virtual_memory().total/1024**3,1),model='closed-loop, no think time, shared authenticated henry identity; 50% page reads, 50% sword writes',server='Waitress 3.0.2 / 4 threads; SQLite default journal; same host',database=db.name,stages=stages,recovery_buckets=buckets,recovery_p95_limit_ms=limit,recovery_confirmed_seconds=restored,consistency=dict(successful_write_responses=sum(r['write'] and r['correct'] for r in rows),unique_response_ids=len(successful_write_ids),ledger_rows=len(ledger),initial_swords=initial_swords,swords=swords,sword_delta=swords-initial_swords,response_ledger_equal=successful_write_ids==stored_ids,integrity_check=integrity))
        (OUT/'stress-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        with (OUT/'stress-requests.csv').open('w',newline='',encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        return result
    finally:
        process.terminate()
        process.wait(timeout=15)
        log.close()

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--server')
    parser.add_argument('--port',type=int,default=5088)
    args=parser.parse_args()
    server(args.server,args.port) if args.server else run()
