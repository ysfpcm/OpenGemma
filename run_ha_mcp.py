import os
import sys
import subprocess


def _resolve_environment(source=None):
    """Normalize the two supported Home Assistant environment conventions."""
    env = dict(source or os.environ)
    env["HA_URL"] = (
        env.get("HA_URL", "").strip()
        or env.get("HOME_ASSISTANT_URL", "").strip()
        or "http://localhost:8123"
    )
    env["HA_TOKEN"] = (
        env.get("HA_TOKEN", "").strip()
        or env.get("HOME_ASSISTANT_TOKEN", "").strip()
    )
    return env


def main():
    env = _resolve_environment()
    if not env.get("HA_TOKEN", "").strip():
        raise SystemExit(
            "Set HA_TOKEN or HOME_ASSISTANT_TOKEN before starting "
            "the Home Assistant MCP bridge."
        )
    # Allow state changes such as light.turn_on, while leaving configuration
    # writes (automations, dashboards, YAML) disabled.
    env["HA_ALLOW_WRITE"] = "true"
    
    script_path = r"C:\Users\Marc\AppData\Roaming\npm\node_modules\@vortitron\home-assistant-mcp\dist\index.js"
    
    # Run the node process, piping stdin/stdout directly to our own stdin/stdout
    process = subprocess.Popen(
        ["node", script_path],
        env=env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    sys.exit(process.wait())

if __name__ == "__main__":
    main()
