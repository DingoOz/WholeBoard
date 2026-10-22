# Start WholeBoard

=== "Linux and macOS"

    ```
    ./run.sh
    ```

    After `install.sh` you can also type `wholeboard`.

=== "Windows"

    ```
    run.bat
    ```

    Or use the Start menu entry.

The browser opens at <http://127.0.0.1:8770/>. If you skipped the installer, the first run creates a virtual
environment and installs the Python packages, but KiCad, TraceMaker and FreeCAD must already be installed.

You can also install it as a command: `pip install -e .`, then `wholeboard`. All the options are in
[Command line](../reference/cli.md).

## Use it from another computer

1. Set a password so that nobody else on the network can use WholeBoard or your AI key: run
   `wholeboard --set-password`, or use *Settings → Network* on the computer running it.
2. Start it listening on the network: `./run.sh --host 0.0.0.0` (or `run.bat --host 0.0.0.0`).
3. Open `http://<this machine's IP>:8770/` on the other computer. WholeBoard prints the address at startup.

Other computers log in once; the computer running WholeBoard never needs to. Only a hash of the password is
stored, and changing it signs everyone out. The TraceMaker live view then listens on the network too, protected
by a random token. Without a password, use it only on a network you trust.
