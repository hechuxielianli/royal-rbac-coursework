"""Run the copied application and collect a real participant's task records."""
from pathlib import Path
import json
import secrets
import sys
import time
import os
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sut'))
from app import create_app
from flask import jsonify, request, render_template_string, Response
from waitress import serve

DB = Path(os.environ.get('SQA_STUDY_DATABASE', str(ROOT / 'evidence' / 'user-study.sqlite3')))
app = create_app({'DATABASE': str(DB), 'SECRET_KEY': secrets.token_hex(32)})
record_file = Path(os.environ.get('SQA_STUDY_RECORD', str(ROOT / 'evidence' / 'user-study.json')))
records = json.loads(record_file.read_text(encoding='utf-8')) if record_file.exists() else []
starts = {}
tasks = [
    ('U1', '以亨利身份进入系统，打造一柄矿路护卫铁剑，并确认库存发生变化。', 'henry', 'forge_sword'),
    ('U2', '以汉斯身份拨付一笔运银经费，查阅收支密账，找到支出金额和银库余额。', 'hans', 'open_vault'),
    ('U3', '以阿格尼丝身份核对银库账目，找到预期余额、实际余额和差异。', 'agnes', 'audit_ledger'),
]
STYLE = '''body{font:18px/1.7 system-ui;max-width:880px;margin:36px auto;padding:0 24px;background:#f6f3ec;color:#262a31}article{background:white;padding:20px;margin:20px 0;border-radius:10px}button,a{font:inherit;padding:7px 14px}textarea{width:95%;height:70px}input{width:70px}small{display:block}#status{color:#175b43}'''
SCRIPT = '''const $=s=>document.querySelector(s);
async function start(id){let r=await fetch('/study/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,person:$('#person').value})});let d=await r.json();$('#'+id+' .started').textContent='已开始，请到软件页面操作。';}
async function done(id){let a=$('#'+id);let r=await fetch('/study/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,person:$('#person').value,prior:$('#prior').value,result:a.querySelector('.result').value,wrong_menus:Number(a.querySelector('.wrong').value),ease:Number(a.querySelector('.ease').value),notes:a.querySelector('.notes').value})});let d=await r.json();a.querySelector('.saved').textContent=d.ok?' 已保存（'+d.elapsed_seconds+'秒）':d.message;}
document.querySelectorAll('[data-start]').forEach(b=>b.addEventListener('click',()=>start(b.dataset.start)));
document.querySelectorAll('[data-finish]').forEach(b=>b.addEventListener('click',()=>done(b.dataset.finish)));'''
PAGE = '''<!doctype html><html lang="zh"><meta charset="utf-8"><title>用户测试任务单</title><link rel="stylesheet" href="/study/style.css"><script src="/study/script.js" defer></script>
<h1>库滕堡政令厅：用户测试</h1><p>请由你或实际参与的同学操作。点击“开始”后，在另一个标签页完成任务，回到本页记录感受。测试独立保存数据，三项约需3分钟。</p>
<p><a href="/" target="_blank">打开被测软件 ↗</a>　演示密码：Kingdom@2026</p>
<label>参与者代号 <input id="person" value="P1"></label>　<label>是否使用过本软件 <select id="prior"><option>使用过</option><option>未使用过</option></select></label>
{% for id,text,user,action in tasks %}<article id="{{id}}"><h2>{{id}}</h2><p>{{text}}</p><button data-start="{{id}}">开始计时</button><small class="started"></small>
<p>结果 <select class="result"><option>完成</option><option>未完成</option></select>　走错菜单次数 <input class="wrong" type="number" min="0" value="0">　容易程度 <select class="ease"><option>5</option><option>4</option><option>3</option><option>2</option><option>1</option></select> /5</p>
<textarea class="notes" placeholder="写下卡住的位置、错误提示或操作感受（可选）"></textarea><br><button data-finish="{{id}}">提交本项记录</button><strong class="saved"></strong></article>{% endfor %}
<p id="status">记录会自动保存到本次作业目录。</p></html>'''

@app.get('/study/style.css')
def style():
    return Response(STYLE, mimetype='text/css')

@app.get('/study/script.js')
def script():
    return Response(SCRIPT, mimetype='application/javascript')

@app.get('/study')
def study():
    return render_template_string(PAGE, tasks=tasks)

def latest_id():
    import sqlite3
    with sqlite3.connect(DB) as conn:
        return conn.execute('SELECT COALESCE(MAX(rowid),0) FROM business_ledger').fetchone()[0]

@app.post('/study/start')
def start():
    data = request.get_json()
    starts[(data['person'], data['id'])] = (time.monotonic(), latest_id())
    return jsonify(ok=True)

@app.post('/study/finish')
def finish():
    import sqlite3
    data = request.get_json()
    started = starts.get((data['person'], data['id']))
    if not started:
        return jsonify(ok=False, message='请先点击开始计时')
    elapsed = round(time.monotonic() - started[0], 2)
    task = next(t for t in tasks if t[0] == data['id'])
    with sqlite3.connect(DB) as conn:
        actions = conn.execute('SELECT b.id,b.action,b.amount FROM business_ledger b JOIN sys_user u ON u.id=b.actor_id WHERE b.id>? AND u.username=? AND b.action=?', (started[1], task[2], task[3])).fetchall()
    record = dict(data, elapsed_seconds=elapsed, verified_actions=actions,
                  recorded_at=datetime.now(timezone.utc).isoformat())
    records[:] = [r for r in records if (r['person'],r['id']) != (data['person'],data['id'])]
    records.append(record)
    record_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    return jsonify(ok=True, elapsed_seconds=elapsed)

if __name__ == '__main__':
    port = int(os.environ.get('SQA_STUDY_PORT','5087'))
    print(f'USER STUDY http://127.0.0.1:{port}/study', flush=True)
    serve(app, host='127.0.0.1', port=port, threads=4)
