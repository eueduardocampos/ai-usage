"""
hwmon.py — hardware da maquina para o painel e o Stream Deck.

No macOS roda o leitor nativo (hwmon/mac/main.swift) como processo filho, que
devolve uma linha JSON por ciclo. O leitor so' fica ligado enquanto alguem
pergunta (/api/hardware): 60 s sem consulta e ele e' desligado.

Onde o binario e' procurado, nesta ordem:
  1. hwmon/bin/ai-usage-hwmon (vem pronto no instalador)
  2. <pasta de dados>/bin/ai-usage-hwmon (compilado aqui com swiftc, se houver
     Xcode ou Command Line Tools; recompila quando o .swift muda)
Sem nenhum dos dois (ou fora do Mac), cai num modo basico em Python: disco,
memoria e carga da CPU.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

IDLE_STOP_S = 60
INTERVAL_S = 2


def _data_bin_dir():
    base = (os.environ.get("AI_USAGE_DATA_DIR")
            or os.path.join(os.environ.get("LOCALAPPDATA")
                            or os.path.join(os.path.expanduser("~"), ".local", "share"),
                            "claude-usage"))
    return os.path.join(base, "bin")


class HardwareMonitor:
    def __init__(self, here, log=print):
        self.here = here
        self.log = log
        self.latest = None
        self.error = None
        self.source = None
        self._proc = None
        self._last_ask = 0.0
        self._lock = threading.Lock()
        self._compiling = False

    # --- binario -------------------------------------------------------------

    def _binary(self):
        if sys.platform != "darwin":
            return None
        pronto = os.path.join(self.here, "hwmon", "bin", "ai-usage-hwmon")
        if os.path.isfile(pronto) and not os.access(pronto, os.X_OK):
            try:
                os.chmod(pronto, 0o755)  # empacotadores as vezes perdem o +x
            except OSError:
                pass
        if os.access(pronto, os.X_OK):
            return pronto
        src = os.path.join(self.here, "hwmon", "mac", "main.swift")
        dest = os.path.join(_data_bin_dir(), "ai-usage-hwmon")
        if os.access(dest, os.X_OK) and (not os.path.exists(src)
                                         or os.path.getmtime(dest) >= os.path.getmtime(src)):
            return dest
        if os.path.exists(src) and not self._compiling and shutil.which("swiftc"):
            self._compiling = True
            threading.Thread(target=self._compile, args=(src, dest), daemon=True).start()
        return dest if os.access(dest, os.X_OK) else None

    def _compile(self, src, dest):
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".tmp"
            r = subprocess.run(["swiftc", "-O", src, "-o", tmp],
                               capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                os.replace(tmp, dest)
                self.log("[hw] leitor nativo compilado")
            else:
                self.error = "falha ao compilar o leitor de hardware"
                self.log(f"[hw] swiftc falhou: {r.stderr[-400:]}")
        except Exception as e:  # noqa: BLE001
            self.log(f"[hw] nao consegui compilar: {e}")
        finally:
            self._compiling = False

    # --- processo ------------------------------------------------------------

    def _ensure_running(self):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return True
            exe = self._binary()
            if not exe:
                return False
            try:
                self._proc = subprocess.Popen(
                    [exe, str(INTERVAL_S)], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError as e:
                self.error = f"leitor de hardware nao abriu: {e}"
                return False
            threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
            threading.Thread(target=self._watchdog, daemon=True).start()
            self.source = "native"
            return True

    def _reader(self, proc):
        for line in proc.stdout:
            try:
                self.latest = json.loads(line)
                self.error = None
            except ValueError:
                continue

    def _watchdog(self):
        while True:
            time.sleep(10)
            with self._lock:
                if not self._proc or self._proc.poll() is not None:
                    return
                if time.time() - self._last_ask > IDLE_STOP_S:
                    try:
                        self._proc.stdin.close()
                        self._proc.terminate()
                    except OSError:
                        pass
                    self._proc = None
                    return

    # --- modo basico ---------------------------------------------------------

    def _basic(self):
        out = {"ts": time.time(), "platform": sys.platform, "basic": True}
        try:
            du = shutil.disk_usage(os.path.expanduser("~"))
            out["disk"] = {"total": du.total, "used": du.used, "free": du.free,
                           "usage": round(du.used / du.total * 100, 1)}
        except OSError:
            pass
        try:
            import psutil  # opcional
            vm = psutil.virtual_memory()
            out["memory"] = {"total": vm.total, "used": vm.used, "free": vm.available,
                             "usage": vm.percent}
            out["cpu"] = {"usage": psutil.cpu_percent(interval=None),
                          "cores": psutil.cpu_count()}
            net = psutil.net_io_counters()
            out["net"] = {"rx_total": net.bytes_recv, "tx_total": net.bytes_sent}
        except Exception:  # noqa: BLE001
            if hasattr(os, "getloadavg"):
                la = os.getloadavg()
                n = os.cpu_count() or 1
                out["cpu"] = {"usage": round(min(100, la[0] / n * 100), 1),
                              "cores": n, "load": [round(x, 1) for x in la]}
        return out

    # --- icone de app ----------------------------------------------------------

    def app_icon(self, app_path, size=144):
        """PNG do icone do app (cache na pasta de dados). None fora do Mac ou
        se o leitor nativo ainda nao estiver pronto."""
        exe = self._binary()
        if not exe or not os.path.isdir(app_path):
            return None
        import hashlib
        d = os.path.join(_data_bin_dir(), "..", "icons")
        os.makedirs(d, exist_ok=True)
        nome = hashlib.sha1(f"{app_path}|{size}".encode()).hexdigest()[:16] + ".png"
        out = os.path.normpath(os.path.join(d, nome))
        try:
            fresco = os.path.getmtime(out) >= os.path.getmtime(app_path)
        except OSError:
            fresco = False
        if not fresco:
            r = subprocess.run([exe, "--icon", app_path, out, str(size)],
                               capture_output=True, timeout=20)
            if r.returncode != 0 or not os.path.exists(out):
                return None
        with open(out, "rb") as f:
            return f.read()

    # --- API -----------------------------------------------------------------

    def state(self):
        self._last_ask = time.time()
        nativo = self._ensure_running()
        if nativo and self.latest and time.time() - self.latest.get("ts", 0) < 15:
            return dict(self.latest, available=True, source="native")
        if nativo and not self.latest:
            # primeira leitura ainda saindo (leva ~1 s)
            for _ in range(20):
                time.sleep(0.1)
                if self.latest:
                    return dict(self.latest, available=True, source="native")
        return dict(self._basic(), available=True, source="basic",
                    note=("compilando o leitor de hardware" if self._compiling else self.error))
