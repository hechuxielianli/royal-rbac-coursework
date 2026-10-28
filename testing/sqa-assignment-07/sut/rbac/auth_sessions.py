"""Track each signed browser session so logout revokes that session on the server."""
import hashlib
import secrets
import time

from flask import current_app, session


def session_key(nonce):
    return hashlib.sha256(nonce.encode('ascii')).hexdigest()


def authenticated_user(conn):
    user_id, nonce = session.get('user_id'), session.get('session_nonce')
    if type(user_id) is not int or not isinstance(nonce, str) or not nonce.isascii():
        return None
    row = conn.execute('SELECT user_id FROM auth_session WHERE session_id=? AND expires_at>?',
                       (session_key(nonce), time.time())).fetchone()
    return user_id if row and row['user_id'] == user_id else None


def issue_session(conn, user_id):
    now = time.time()
    conn.execute('DELETE FROM auth_session WHERE expires_at<=?', (now,))
    nonce = secrets.token_urlsafe(32)
    conn.execute('INSERT INTO auth_session VALUES (?,?,?)',
                 (session_key(nonce), user_id,
                  now + current_app.permanent_session_lifetime.total_seconds()))
    return nonce


def revoke_session(conn, nonce):
    if isinstance(nonce, str) and nonce.isascii():
        conn.execute('DELETE FROM auth_session WHERE session_id=?', (session_key(nonce),))
