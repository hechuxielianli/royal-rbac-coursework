"""Local, single-process application launcher (no debug reloader)."""
import argparse

from waitress import serve

from app import create_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Royal & Guild RBAC teaching application")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    application = create_app()
    print(f"Royal & Guild RBAC: http://127.0.0.1:{args.port}", flush=True)
    print("Local teaching accounts / password: Kingdom@2026", flush=True)
    print("Press Ctrl+C to stop. Data is retained in instance/.", flush=True)
    serve(application, host="127.0.0.1", port=args.port, threads=4)
