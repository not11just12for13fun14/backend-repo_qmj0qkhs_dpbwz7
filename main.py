import os
import subprocess
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Helpers ----------
MODULE_NAME = os.getenv("MODULE_NAME", "YourModule")
CONFIG_PATH = f"/data/adb/modules/{MODULE_NAME}/config.conf"


def run_cmd(cmd: str, use_su: bool = True) -> tuple[str, Optional[str], int]:
    """Run shell command. Try with su -c first (Android), then plain shell.
    Returns (stdout, stderr, returncode)
    """
    try_cmds = []
    if use_su:
        try_cmds.append(["su", "-c", cmd])
    try_cmds.append(["sh", "-c", cmd])

    for c in try_cmds:
        try:
            proc = subprocess.run(c, capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                return proc.stdout.strip(), proc.stderr.strip() or None, proc.returncode
            # If su not available, try next
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return "", "Command failed", 1


def read_config_mode() -> Optional[str]:
    # Read mode from config file; expected line like: mode=otomatis or mode=statis
    out, err, code = run_cmd(f"cat {CONFIG_PATH}")
    if code != 0 or not out:
        return None
    mode = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("mode="):
            mode = line.split("=", 1)[1].strip()
            break
    return mode


def write_config_mode(new_mode: str) -> bool:
    if new_mode not in {"otomatis", "statis"}:
        raise ValueError("Mode harus 'otomatis' atau 'statis'")

    # Read existing content if possible
    content = ""
    out, _, code = run_cmd(f"cat {CONFIG_PATH}")
    if code == 0 and out:
        lines = out.splitlines()
        updated = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("mode="):
                new_lines.append(f"mode={new_mode}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"mode={new_mode}")
        content = "\n".join(new_lines) + "\n"
    else:
        content = f"mode={new_mode}\n"

    # Try to write using sh with redirect; need root for /data path typically
    echo_cmd = f"printf '%s' {sh_escape(content)} > {CONFIG_PATH}"
    out2, err2, code2 = run_cmd(echo_cmd)
    return code2 == 0


def sh_escape(s: str) -> str:
    # Safely escape for single-quoted printf pattern
    # We'll use printf '%s' "..." but safer: here we wrap in $'...'
    # Simpler approach: use python to write via tee with here-doc
    return repr(s)


def detect_binary(name: str) -> bool:
    out, _, code = run_cmd(f"which {name}")
    return code == 0 and bool(out)


def getprop(prop: str) -> Optional[str]:
    if detect_binary("getprop"):
        out, _, code = run_cmd(f"getprop {prop}")
        if code == 0 and out:
            return out
    return None


def get_device_info() -> dict:
    model = getprop("ro.product.model") or os.getenv("DEVICE_MODEL") or "Tidak tersedia"
    board = getprop("ro.product.board") or os.getenv("DEVICE_BOARD") or "Tidak tersedia"
    brand = getprop("ro.product.manufacturer") or os.getenv("DEVICE_BRAND") or "Tidak tersedia"
    android = getprop("ro.build.version.release") or os.getenv("DEVICE_ANDROID") or "Tidak tersedia"

    # kernel
    kernel_out, _, _ = run_cmd("uname -r", use_su=False)
    kernel = kernel_out or "Tidak tersedia"

    cpu = getprop("ro.hardware") or os.getenv("DEVICE_CPU") or "Tidak tersedia"

    # RAM in kB
    ram_kb = None
    free_out, _, code = run_cmd("free | grep Mem | awk '{print $2}'", use_su=False)
    if code == 0 and free_out:
        ram_kb = free_out.strip()
    else:
        # Try proc meminfo
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_kb = line.split()[1]
                        break
        except Exception:
            pass
    ram = f"{ram_kb} kB" if ram_kb else "Tidak tersedia"

    return {
        "model": model,
        "board": board,
        "brand": brand,
        "android": android,
        "kernel": kernel,
        "cpu": cpu,
        "ram": ram,
    }


# ---------- Schemas ----------
class ModeUpdate(BaseModel):
    mode: str


# ---------- Routes ----------
@app.get("/")
def read_root():
    return {"message": "WebUI Magisk Module Controller Backend"}


@app.get("/api/device")
def device_info():
    info = get_device_info()
    # also provide the exact shell-like output lines to mimic ui_print style
    pretty = [
        f"• Model      : {info['model']}",
        f"• Board      : {info['board']}",
        f"• Brand      : {info['brand']}",
        f"• Android    : {info['android']}",
        f"• Kernel     : {info['kernel']}",
        f"• CPU        : {info['cpu']}",
        f"• RAM        : {info['ram']}",
    ]
    return {"info": info, "pretty": pretty}


@app.get("/api/mode")
def get_mode():
    mode = read_config_mode()
    status = "unknown"
    if mode in {"otomatis", "statis"}:
        status = mode
    return {"mode": status, "config_path": CONFIG_PATH}


@app.post("/api/mode")
def set_mode(payload: ModeUpdate):
    mode = payload.mode.lower().strip()
    if mode not in {"otomatis", "statis"}:
        raise HTTPException(status_code=400, detail="Mode harus 'otomatis' atau 'statis'")
    success = write_config_mode(mode)
    if not success:
        raise HTTPException(status_code=500, detail="Gagal menulis file konfigurasi. Pastikan perangkat sudah root dan path benar.")
    return {"ok": True, "mode": mode}


@app.get("/api/about")
def about():
    return {
        "developer": {
            "name": os.getenv("DEV_NAME", "Your Name"),
            "contact": os.getenv("DEV_CONTACT", "@username"),
            "website": os.getenv("DEV_WEBSITE", "https://example.com"),
        },
        "module": {
            "name": MODULE_NAME,
            "description": os.getenv(
                "MODULE_DESC",
                "Module Magisk ini menyediakan dua mode: otomatis dan statis. Mode otomatis menyesuaikan konfigurasi sesuai kondisi sistem, sedangkan mode statis menggunakan pengaturan tetap yang ditentukan pengguna.",
            ),
            "config_path": CONFIG_PATH,
        },
    }


@app.get("/test")
def test_database():
    """Compatibility endpoint kept for health check (database optional here)."""
    return {"backend": "✅ Running"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
