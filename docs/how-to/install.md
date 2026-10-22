# Install and check the tools

The installer sets up everything: Python, KiCad, TraceMaker, FreeCAD, KiCad's libraries and WholeBoard's
Python packages. It asks before installing system packages.

=== "Linux and macOS"

    Ubuntu, Debian, Fedora, Arch, openSUSE and macOS:

    ```
    ./install.sh
    ```

=== "Windows"

    Windows 10 or 11: double-click `install.bat`. It uses winget for Python, Git, KiCad and FreeCAD, and installs
    TraceMaker inside WSL, because TraceMaker runs on Linux. If WSL is not set up, the installer offers to set it
    up. Restart Windows afterwards and run `install.bat` again.

| What | Linux | macOS | Windows |
|---|---|---|---|
| Python 3.10+, git | package manager | Homebrew | winget |
| KiCad 10 | KiCad's PPA on Ubuntu, the distribution's package elsewhere, Flathub if that is too old | Homebrew | winget |
| TraceMaker | ready-made release (x86_64, glibc 2.38+), otherwise built from source | built from source | release inside WSL |
| FreeCAD | official AppImage in `~/.wholeboard/tools` | Homebrew | winget |
| KiCad libraries | with KiCad, or downloaded | with KiCad | with KiCad |

## Check what is installed

```
.venv/bin/python -m wholeboard.doctor
```

On Windows, use `.venv\Scripts\python` instead. The doctor lists each tool, where it was found and its version.

## Install one missing tool

```
.venv/bin/python -m wholeboard.doctor --install freecad
.venv/bin/python -m wholeboard.doctor --install platformio
.venv/bin/python -m wholeboard.doctor --install kikit
```

`--install` on its own installs every missing tool except PlatformIO and KiKit (`tracemaker`, `freecad`, `ngspice`, `libs`). Add `--force` to reinstall a tool that is already found.

## Requirements in detail

- **Python 3.10+**
- **KiCad 9 or 10.** `kicad-cli` is used for ERC, DRC, zone fill, renders and fab outputs. It is found in the usual
  install folders, or set its path in *Settings*.
- **TraceMaker** (<https://github.com/DingoOz/TraceMaker>) for placement and routing. It runs on Linux and macOS;
  on Windows it runs inside WSL (*Run TraceMaker inside WSL* in *Settings*).
- **FreeCAD 1.0 or newer** for enclosures.
- **ngspice** for simulation: the `ngspice` program, KiCad's own `libngspice`, or the KiCad Docker image. The
  installers add it, and *Settings → Install ngspice* fetches it on Windows and macOS.
- **KiKit** (optional) for panels. It runs on KiCad's own Python.
- **PlatformIO** (optional) to compile the starter firmware.
- **3D part models** come from your KiCad install. Any missing model is downloaded once from KiCad's library into
  `~/.wholeboard/models3d` (*Settings → KiCad & TraceMaker → 3D part models*).
- **KiCad footprint and symbol libraries**, normally installed with KiCad. If they are missing (for example when
  `kicad-cli` runs from Docker), use *Settings → Download KiCad libraries*.
- **An AI**, one of:
    - **Claude** through the Anthropic API (a key in *Settings* or `ANTHROPIC_API_KEY`), default model
      `claude-opus-5-5`;
    - **OpenAI or Grok (xAI)** with an API key;
    - **any OpenAI-compatible server**: local models through Ollama (`http://localhost:11434/v1`), LM Studio,
      llama.cpp server or vLLM. The model must support tool calling. Bigger models design better boards.

    *Settings → How to get an API key* walks through getting a key from Anthropic, OpenAI or xAI. Quick-setup
    buttons fill in the server address, and *List models* shows the models your key can use.
