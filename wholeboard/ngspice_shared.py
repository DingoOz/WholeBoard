"""Run a netlist through the ngspice shared library (e.g. the one KiCad bundles).

    python -m wholeboard.ngspice_shared <libngspice path> <netlist.cir>

Runs in its own process so a crash inside the library cannot stop WholeBoard. The netlist's
.control block does the analyses and writes the raw files; this only loads and sources it."""

from __future__ import annotations

import ctypes
import os
import sys


def main() -> int:
    lib_path, netlist = sys.argv[1], sys.argv[2]
    if os.name == "nt":
        os.add_dll_directory(os.path.dirname(os.path.abspath(lib_path)))
    lib = ctypes.CDLL(lib_path)
    out = sys.stdout

    send_char = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    send_stat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    exit_cb = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int,
                               ctypes.c_void_p)

    @send_char
    def on_char(text, _id, _user):
        line = text.decode(errors="replace")
        for prefix in ("stdout ", "stderr "):
            if line.startswith(prefix):
                line = line[len(prefix):]
        out.write(line + "\n")
        return 0

    @send_stat
    def on_stat(_text, _id, _user):
        return 0

    @exit_cb
    def on_exit(status, _unload, _quit, _id, _user):
        out.flush()
        os._exit(status)

    lib.ngSpice_Init.argtypes = [send_char, send_stat, exit_cb, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p]
    lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
    lib.ngSpice_Init(on_char, on_stat, on_exit, None, None, None, None)
    path = os.path.abspath(netlist).replace("\\", "/")
    rc = lib.ngSpice_Command(f"source {path}".encode())
    out.flush()
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
