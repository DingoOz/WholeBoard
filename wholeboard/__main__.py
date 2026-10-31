"""Start WholeBoard: python -m wholeboard [--port 8770] [--no-browser]."""

from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser

from . import config


def main() -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser(prog="wholeboard", description="AI PCB design workbench")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(cfg.get("port", 8770)))
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    ap.add_argument("--set-password", action="store_true",
                    help="ask for a password that other computers on the network must enter")
    ap.add_argument("--clear-password", action="store_true", help="remove the network password")
    args = ap.parse_args()

    from . import auth
    if args.clear_password:
        auth.set_password(None)
        print("The network password is removed.")
    if args.set_password:
        import getpass
        while True:
            pw = getpass.getpass("New password for other computers (6+ characters): ")
            if len(pw) >= 6 and pw == getpass.getpass("Again: "):
                break
            print("The passwords were too short or did not match; try again.")
        auth.set_password(pw)
        print("Password set. This computer never needs it; other computers log in once.")

    import uvicorn

    os.environ["WHOLEBOARD_HOST"] = args.host

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}/"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    print(f"WholeBoard is running at {url}  (Ctrl+C to stop)")
    if args.host in ("0.0.0.0", "::"):
        for ip in auth.lan_addresses():
            print(f"  from other machines: http://{ip}:{args.port}/")
        if auth.enabled():
            print("  Other computers must log in with the password (wholeboard --clear-password removes it).")
        else:
            print("  Anyone who can reach this port can use WholeBoard and your AI key. Use it on a trusted network,")
            print("  or set a password with: wholeboard --set-password")
    uvicorn.run("wholeboard.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
