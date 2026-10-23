"""本地启动入口与可测试的 Flask 应用工厂。"""
from datetime import timedelta
import os
from pathlib import Path
import secrets

from flask import Flask

from rbac.db import close_db, initialize_database
from rbac.events import build_event_bus
from rbac.routes import bp


def create_app(test_config=None):
    root = Path(__file__).resolve().parent
    instance = root / 'instance'
    instance.mkdir(exist_ok=True)
    app = Flask(__name__, instance_path=str(instance))
    app.config.from_mapping(
        DATABASE=str(instance / 'kingdom.sqlite3'),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=64 * 1024,
        TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]'],
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get('SECRET_KEY'):
        secret_path = instance / 'secret.key'
        if not secret_path.exists():
            try:
                with secret_path.open('x',encoding='ascii') as file:
                    file.write(secrets.token_hex(32))
            except FileExistsError:
                pass
        app.config['SECRET_KEY'] = os.environ.get('KINGDOM_SECRET_KEY') or secret_path.read_text(encoding='ascii').strip()
    initialize_database(app.config['DATABASE'])
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    app.extensions['event_bus'] = build_event_bus()
    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)
    return app


if __name__ == '__main__':
    from waitress import serve
    application = create_app()
    print('王室政令大厅：http://127.0.0.1:5000', flush=True)
    print('演示密码：Kingdom@2026；Ctrl+C 停止服务。', flush=True)
    serve(application, host='127.0.0.1',port=int(os.environ.get('PORT','5000')),threads=4)
