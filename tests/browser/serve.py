"""Start a disposable verification server; never use the normal instance DB."""
import argparse
from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import create_app
from waitress import serve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5053)
    args = parser.parse_args()
    database = Path(tempfile.mkdtemp(prefix='royal-rbac-browser-')) / 'kingdom.sqlite3'
    application = create_app({'DATABASE':str(database), 'SECRET_KEY':secrets.token_hex(32)})
    print(f'Isolated database: {database}', flush=True)
    print(f'Browser tests: http://127.0.0.1:{args.port}', flush=True)
    serve(application, host='127.0.0.1', port=args.port, threads=4)


if __name__ == '__main__':
    main()
