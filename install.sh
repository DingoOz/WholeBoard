#!/usr/bin/env bash
# WholeBoard installer for Linux and macOS.
#
# Installs everything WholeBoard needs:
#   - Python 3.10+, git and curl                        (system package manager / Homebrew)
#   - KiCad 10 (or the newest your system offers, 9+)   (system package manager / Homebrew)
#   - WholeBoard's Python packages                      (into .venv in this folder)
#   - TraceMaker, the autorouter                        (release download, or built from source)
#   - FreeCAD, for enclosures                           (official AppImage / Homebrew)
#   - ngspice, for circuit simulation                   (system package manager / Homebrew)
#   - KiCad's footprint and symbol libraries if KiCad did not bring them
# and adds a `wholeboard` command (and a menu entry on Linux).
#
# Usage: ./install.sh [--yes] [--no-system]
#   --yes        do not ask before installing system packages
#   --no-system  skip system packages (use what is already installed)
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
YES=0; SYSTEM=1
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --no-system) SYSTEM=0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  {
  [ "$YES" = 1 ] && return 0
  read -r -p "$1 [Y/n] " r </dev/tty || true
  case "$r" in [nN]*) return 1 ;; *) return 0 ;; esac
}
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

OS="$(uname -s)"
ARCH="$(uname -m)"
say "Installing WholeBoard on $OS ($ARCH)"

kicad_ok() {
  have kicad-cli && kicad-cli version 2>/dev/null | grep -Eq '^(9|1[0-9])\.' && return 0
  [ -x /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli ] && return 0
  return 1
}

# ------------------------------------------------------------------ system packages
if [ "$SYSTEM" = 1 ]; then
  if [ "$OS" = "Darwin" ]; then
    if ! have brew; then
      say "Homebrew is needed to install KiCad, FreeCAD and the build tools"
      if ask "Install Homebrew now (from brew.sh)?"; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
      else
        echo "Install Homebrew from https://brew.sh and run this again."; exit 1
      fi
    fi
    say "Installing Python, git and the TraceMaker build tools with Homebrew"
    brew install python@3.12 git cmake ninja eigen cli11 nlohmann-json catch2 zstd boost clipper2 node ngspice
    if ! kicad_ok; then say "Installing KiCad"; brew install --cask kicad; fi
    if [ ! -d /Applications/FreeCAD.app ] && [ ! -d "$HOME/Applications/FreeCAD.app" ]; then
      say "Installing FreeCAD"; brew install --cask freecad
    fi
  elif have apt-get; then
    PKGS="python3 python3-venv python3-pip git curl ca-certificates ngspice"
    if grep -qi ubuntu /etc/os-release; then PKGS="$PKGS software-properties-common gpg-agent"; fi
    # TraceMaker's ready-made build needs x86_64 and glibc 2.38+; otherwise it is built from source.
    GLIBC=$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || echo 0)
    if [ "$ARCH" != "x86_64" ] || [ "$(printf '%s\n2.38\n' "$GLIBC" | sort -V | head -1)" != "2.38" ]; then
      PKGS="$PKGS build-essential cmake ninja-build g++"
    fi
    say "Installing system packages: $PKGS and FUSE 2 (for FreeCAD's AppImage)"
    if ask "Install these with apt (needs sudo)?"; then
      $SUDO apt-get update
      # Ubuntu 24.04+ renamed FUSE 2 to libfuse2t64.
      if apt-cache show libfuse2t64 >/dev/null 2>&1; then PKGS="$PKGS libfuse2t64"; else PKGS="$PKGS libfuse2"; fi
      $SUDO apt-get install -y $PKGS
    fi
    if ! kicad_ok; then
      if grep -qi ubuntu /etc/os-release && have add-apt-repository; then
        say "Adding the official KiCad 10 PPA and installing KiCad"
        if ask "Install KiCad 10 from ppa:kicad/kicad-10.0-releases?"; then
          $SUDO add-apt-repository -y ppa:kicad/kicad-10.0-releases
          $SUDO apt-get update
          # kicad-cli plus the footprint and symbol libraries; the 3D models are several GB and optional.
          $SUDO apt-get install -y --no-install-recommends kicad kicad-footprints kicad-symbols
        fi
      else
        say "Installing KiCad from your distribution"
        ask "Install KiCad with apt?" && $SUDO apt-get install -y kicad || true
      fi
    fi
  elif have dnf; then
    say "Installing system packages with dnf"
    ask "Install python3, git, KiCad, ngspice and FUSE with dnf (needs sudo)?" && \
      $SUDO dnf install -y python3 python3-pip git curl fuse fuse-libs kicad ngspice cmake ninja-build gcc-c++ || true
  elif have pacman; then
    say "Installing system packages with pacman"
    ask "Install python, git, KiCad, ngspice and FUSE with pacman (needs sudo)?" && \
      $SUDO pacman -S --needed --noconfirm python python-pip git curl fuse2 kicad kicad-library ngspice cmake ninja gcc || true
  elif have zypper; then
    say "Installing system packages with zypper"
    ask "Install python3, git, KiCad, ngspice and FUSE with zypper (needs sudo)?" && \
      $SUDO zypper install -y python3 python3-pip git curl fuse kicad ngspice cmake ninja gcc-c++ || true
  else
    warn "No supported package manager found. Install Python 3.10+, git, curl, ngspice and KiCad 9+ yourself."
  fi

  # Fall back to KiCad's Flatpak when the distribution's KiCad is missing or too old.
  if [ "$OS" = "Linux" ] && ! kicad_ok && have flatpak; then
    say "Your distribution's KiCad is missing or older than 9; installing KiCad from Flathub"
    if ask "Install org.kicad.KiCad with Flatpak?"; then
      flatpak install -y --user flathub org.kicad.KiCad
      mkdir -p "$HOME/.wholeboard/bin"
      cat > "$HOME/.wholeboard/bin/kicad-cli" <<'EOF'
#!/bin/sh
exec flatpak run --filesystem=home --command=kicad-cli org.kicad.KiCad "$@"
EOF
      chmod +x "$HOME/.wholeboard/bin/kicad-cli"
      KICAD_WRAPPER="$HOME/.wholeboard/bin/kicad-cli"
    fi
  fi
fi

# ------------------------------------------------------------------ Python environment
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "Python 3.10 or newer is required."; exit 1; fi
say "Setting up WholeBoard's Python environment with $($PY --version)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -e .
touch .venv/.installed

if [ -n "${KICAD_WRAPPER:-}" ]; then
  .venv/bin/python - "$KICAD_WRAPPER" <<'EOF'
import sys
from wholeboard import config
cfg = config.load(); cfg["tools"]["kicad_cli"] = sys.argv[1]; config.save(cfg)
EOF
fi

# ------------------------------------------------------------------ TraceMaker, FreeCAD, libraries
say "Installing TraceMaker, FreeCAD, ngspice and KiCad libraries where they are missing"
.venv/bin/python -m wholeboard.doctor --install || true

# ------------------------------------------------------------------ launcher
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/wholeboard" <<EOF
#!/bin/sh
exec "$HERE/run.sh" "\$@"
EOF
chmod +x "$HOME/.local/bin/wholeboard"
if [ "$OS" = "Linux" ]; then
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/wholeboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=WholeBoard
Comment=Describe a PCB and get it designed, routed, quoted and boxed
Exec=$HERE/run.sh
Icon=$HERE/wholeboard/web/icon.svg
Terminal=true
Categories=Development;Electronics;
EOF
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "Add ~/.local/bin to your PATH to use the 'wholeboard' command." ;; esac

say "Done"
echo "Start WholeBoard with:  wholeboard     (or $HERE/run.sh)"
echo "To use it from another computer on your network:  wholeboard --host 0.0.0.0"
echo "Then open Settings in WholeBoard to choose your AI (Claude, OpenAI, Grok or a local model)."
