"""Exercise extraction runtime checks through the actual frozen agent binary."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="lawhand-packaged-search-") as directory:
        root = Path(directory)
        env = dict(os.environ)
        env.update(
            SEARCH_NODE_ENABLED="true",
            SEARCH_NODE_SANDBOX_VERIFIED="true",
            SEARCH_NODE_TEMP_ROOT=str(root / "temp"),
            SEARCH_NODE_STAGING_ROOT=str(root / "staging"),
        )
        env.pop("SEARCH_NODE_PYTHON_EXECUTABLE", None)
        missing = subprocess.run(
            [str(binary), "search-preflight"], cwd=root, env=env,
            capture_output=True, text=True, timeout=45, check=False,
        )
        output = missing.stdout + missing.stderr
        if missing.returncode == 0 or "SEARCH_NODE_PYTHON_EXECUTABLE" not in output:
            raise RuntimeError(f"Packaged runtime requirement not enforced: {output}")
        env["SEARCH_NODE_PYTHON_EXECUTABLE"] = sys.executable
        ready = subprocess.run(
            [str(binary), "search-preflight"], cwd=root, env=env,
            capture_output=True, text=True, timeout=45, check=False,
        )
        if ready.returncode:
            raise RuntimeError(f"Provisioned packaged preflight failed: {ready.stdout}{ready.stderr}")
        print("Packaged search preflight rejects missing runtime and accepts installed worker")


if __name__ == "__main__":
    main()
