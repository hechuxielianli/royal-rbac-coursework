"""Disposable browser fixture on port 5089, separate from study and stress DBs."""
from datetime import datetime
from pathlib import Path
import json
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'sut'))
from app import create_app
from flask import jsonify, request, session
from rbac.db import get_db
from rbac.auth_sessions import session_key
from waitress import serve

database = Path(tempfile.mkdtemp(prefix='sqa-browser-5089-')) / 'browser.sqlite3'
app = create_app({'DATABASE': str(database), 'SECRET_KEY': secrets.token_hex(32)})


@app.post('/__browser__/expire-current-session')
def expire_current_session():
    """Fixture setup only: expire the current real server-side session row."""
    if request.remote_addr != '127.0.0.1' or request.headers.get('X-Browser-Fixture') != '1':
        return jsonify(ok=False), 403
    nonce = session.get('session_nonce')
    if not nonce:
        return jsonify(ok=False), 400
    conn = get_db()
    cursor = conn.execute('UPDATE auth_session SET expires_at=? WHERE session_id=?',
                          (time.time() - 1, session_key(nonce)))
    conn.commit()
    return jsonify(ok=cursor.rowcount == 1, setup='server-side expiry in disposable fixture')


if __name__ == '__main__':
    manifest = {'started_at': datetime.now().astimezone().isoformat(),
                'base_url': 'http://127.0.0.1:5089', 'database': str(database),
                'threads': 4, 'fixture_control': 'expire-current-session'}
    (ROOT / 'evidence' / 'browser-server.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    serve(app, host='127.0.0.1', port=5089, threads=4)
