"""Owned Windows Chrome websocket over binary PowerShell stdin/stdout.

No NAT listener, ambient browser attachment or generic URL proxy.
"""
from __future__ import annotations
import json
import errno
import os
import platform
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading


class BridgeError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _powershell_file(path):
    if not (path.is_absolute() and path.name.lower() == 'powershell.exe'
            and path.is_file() and os.access(path, os.X_OK)):
        return False
    try:
        with path.open('rb') as executable:
            return executable.read(2) == b'MZ'
    except OSError:
        return False


def discover_powershell():
    """PATH first, then only standard executable directories on drive mounts."""
    override = os.environ.get('HUELLA_POWERSHELL_EXE')
    if override:
        path = Path(override)
        if not _powershell_file(path):
            raise BridgeError('powershell_override_invalid', 'HUELLA_POWERSHELL_EXE debe indicar una ruta absoluta existente y ejecutable a powershell.exe.')
        return str(path)
    found = shutil.which('powershell.exe')
    if found:
        return found
    roots = [Path('/mnt/c')]
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            if len(fields) > 5 and ('drvfs' in fields or any('aname=drvfs' in field for field in fields)):
                mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
                roots.append(Path(mount))
    except OSError:
        pass
    for root in dict.fromkeys(roots):
        path = root
        try:
            # Case-aware lookup is bounded to five standard path components.
            for component in ('Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'):
                exact = path / component
                path = exact if exact.exists() else next(entry for entry in path.iterdir() if entry.name.lower() == component.lower())
            if _powershell_file(path):
                return str(path)
        except (OSError, StopIteration):
            continue
    raise BridgeError('powershell_missing', 'No se encontró PowerShell de Windows en PATH ni en las rutas estándar montadas. Comprueba el montaje de Windows o indica HUELLA_POWERSHELL_EXE; no se abrirá Chrome de Linux.')


def discover_wslpath():
    found = shutil.which('wslpath')
    if found:
        return found
    if Path('/usr/bin/wslpath').is_file() and os.access('/usr/bin/wslpath', os.X_OK):
        return '/usr/bin/wslpath'
    raise BridgeError('wslpath_missing', 'No se encontró wslpath en WSL. Revisa las herramientas de esta distribución; esto no es un bloqueo de inicio de sesión de Chrome.')


def powershell_os_error(exc):
    if exc.errno in (errno.EACCES, errno.EPERM):
        return BridgeError('windows_execution_denied', 'El sistema denegó la ejecución de PowerShell de Windows. Revisa los permisos o restricciones con el administrador; no se cambiaron políticas.')
    if exc.errno == errno.ENOENT:
        return BridgeError('windows_interop_unavailable', 'PowerShell fue localizado, pero WSL no pudo ejecutarlo. Comprueba que el montaje siga disponible y que la interoperabilidad de Windows esté habilitada; no es un problema de login de Chrome.')
    return BridgeError('windows_interop_unavailable', 'WSL no pudo ejecutar PowerShell de Windows. Revisa la interoperabilidad WSL/Windows; no se abrirá otro navegador ni se cambiarán políticas.')


class WindowsChromeBridge:
    def __init__(self):
        self.process = None
        self.listener = None
        self.connection = None
        self.stderr = bytearray()

    @staticmethod
    def bootstrap_command(source, *, inspect_only=False):
        # Windows may reject executing a .ps1 directly from WSL's UNC share
        # (AuthorizationManager). Copy only this authorized helper into the
        # dedicated local runtime directory; keep execution policy unchanged.
        quoted = source.replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop'; "
            "$profile=Join-Path $env:LOCALAPPDATA 'HuellaBrowser'; "
            "[void][IO.Directory]::CreateDirectory($profile); "
            "$helper=Join-Path $profile 'huella-cdp-bridge.ps1'; "
            f"[IO.File]::WriteAllBytes($helper,[IO.File]::ReadAllBytes('{quoted}')); "
            "& $helper"
        )
        return command + (' -InspectOnly' if inspect_only else '')

    def start(self):
        if 'microsoft' not in platform.release().lower():
            raise BridgeError('wsl_required', 'La aplicación necesita WSL2 y Chrome instalado en Windows; no utiliza Chrome de Linux.')
        wslpath = discover_wslpath()
        powershell = discover_powershell()
        script = Path(__file__).with_name('windows_cdp_bridge.ps1')
        try:
            converted = subprocess.run([wslpath, '-w', str(script)], capture_output=True, text=True, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            raise BridgeError('wslpath_failed', 'wslpath no pudo convertir la ruta del puente. Revisa el montaje de WSL; no se inició Chrome.') from None
        helper_environment = dict(os.environ)
        helper_environment.pop('GEMINI_API_KEY', None)
        try:
            self.process = subprocess.Popen(
                [powershell, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', self.bootstrap_command(converted.stdout.strip())],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                env=helper_environment)
        except OSError as exc:
            raise powershell_os_error(exc) from None
        def drain_errors():
            while chunk := self.process.stderr.read(4096):
                if len(self.stderr) < 8192:
                    self.stderr.extend(chunk[:8192-len(self.stderr)])
        error_reader = threading.Thread(target=drain_errors, daemon=True)
        error_reader.start()
        ready, result = threading.Event(), []
        def handshake():
            result.append(self.process.stdout.readline(4096))
            ready.set()
        threading.Thread(target=handshake, daemon=True).start()
        if not ready.wait(25):
            self.close()
            raise BridgeError('windows_bridge_timeout', 'Windows no confirmó el navegador dedicado en 25 segundos. Revisa WSL/Windows y la ventana de Chrome.')
        try:
            message = json.loads(result[0].decode('utf-8-sig'))
            if message.get('status') != 'ready':
                raise BridgeError(message.get('code', 'windows_bridge_failed'), message.get('message', 'No se pudo conectar Chrome de Windows.'))
            path = message['path']
            if not re.fullmatch(r'/devtools/browser/[a-fA-F0-9-]{36}', path):
                raise ValueError('Unexpected endpoint')
        except BridgeError:
            self.close()
            raise
        except (ValueError, KeyError, UnicodeError):
            if not result[0]:
                error_reader.join(timeout=0.5)
            detail = self.stderr.decode('utf-8-sig', errors='replace').lower()
            self.close()
            if 'authorizationmanager' in detail:
                raise BridgeError('windows_script_authorization', 'Windows bloqueó la ejecución del puente (AuthorizationManager). Revisa la autorización del helper local de HuellaBrowser; no se cambiaron políticas ni se abrirá otro navegador.') from None
            raise BridgeError('windows_bridge_protocol', 'Windows no devolvió una identidad válida del navegador dedicado.') from None
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(20)
        port = self.listener.getsockname()[1]
        self.endpoint_path = path
        self.loopback_host = f'127.0.0.1:{port}'
        threading.Thread(target=self._relay, daemon=True, name='huella-cdp-bridge').start()
        return f'ws://127.0.0.1:{port}{path}'

    def _relay(self):
        try:
            self.connection, _ = self.listener.accept()
            self.listener.close()  # Single-use listener, not a network service.
            # Admit only the websocket upgrade for the unguessable owned browser
            # identity, not arbitrary HTTP/CDP discovery paths or proxy requests.
            self.connection.settimeout(15)
            header = bytearray()
            while b'\r\n\r\n' not in header and len(header) < 16384:
                chunk = self.connection.recv(min(4096, 16384-len(header)))
                if not chunk:
                    return
                header.extend(chunk)
            if b'\r\n\r\n' not in header:
                return
            lines = bytes(header).split(b'\r\n\r\n', 1)[0].split(b'\r\n')
            expected = f'GET {self.endpoint_path} HTTP/1.1'.encode('ascii')
            fields = {}
            for line in lines[1:]:
                name, separator, value = line.partition(b':')
                if separator:
                    fields[name.strip().lower()] = value.strip().lower()
            if (lines[0] != expected or fields.get(b'upgrade') != b'websocket' or
                    fields.get(b'host') != self.loopback_host.encode('ascii')):
                return
            self.process.stdin.write(header)
            self.process.stdin.flush()
            self.connection.settimeout(None)
            def upload():
                try:
                    while chunk := self.connection.recv(65536):
                        self.process.stdin.write(chunk)
                        self.process.stdin.flush()
                except (OSError, ValueError, AttributeError):
                    pass
                finally:
                    try:
                        self.process.stdin.close()
                    except (OSError, ValueError, AttributeError):
                        pass
            threading.Thread(target=upload, daemon=True).start()
            while chunk := self.process.stdout.read(65536):
                self.connection.sendall(chunk)
        except (OSError, ValueError, AttributeError):
            pass
        finally:
            if self.connection:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()

    def close(self):
        # Terminate our transport helper only, never the Windows browser.
        if self.listener:
            self.listener.close()
        if self.connection:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
