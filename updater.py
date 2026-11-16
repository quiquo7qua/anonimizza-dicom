"""Aggiornamenti automatici da GitHub Releases: solo libreria standard, HTTPS e impronta SHA-256."""
from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from version import VERSION

REPO = "quiquo7qua/anonimizza-dicom"
ASSETS = {"win32": "Anonimizza-DICOM-Windows.exe", "darwin": "Anonimizza-DICOM-Mac.zip"}
RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"
CAFILE = "/etc/ssl/cert.pem"  # Mac: OpenSSL non legge il Portachiavi e l'app compilata non ha un suo pacchetto di CA
RETRIES, RETRY_DELAY = 10, 0.5  # Windows: antivirus e indicizzazione tengono aperto per un attimo il file appena scritto
NO_UPDATE_FILE = "no-aggiornamenti"
FAILED = "Aggiornamento non riuscito: l'app è rimasta com'era. Riprova più tardi o scarica la nuova versione a mano."


class UpdateError(Exception):
    """Errore con messaggio già pronto per l'utente (una riga)."""


def explain(exc: Exception) -> str:
    """Messaggio per l'utente: mai testo grezzo di urllib, subprocess o percorsi."""
    if isinstance(exc, UpdateError):
        return str(exc)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Disco pieno: libera spazio e riprova."
    return FAILED


def context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=CAFILE) if sys.platform == "darwin" else ssl.create_default_context()


def open_url(url: str, timeout: float = 30):
    """urlopen con le CA giuste (creato qui: un file mancante diventa un normale errore di rete) e un User-Agent proprio."""
    request = urllib.request.Request(url, headers={"User-Agent": f"Anonimizza-DICOM/{VERSION}"})
    return urllib.request.urlopen(request, timeout=timeout, context=context())


def parse_version(s) -> tuple[int, ...] | None:
    return tuple(int(n) for n in s.split(".")) if isinstance(s, str) and re.fullmatch(r"[0-9]+(\.[0-9]+)*", s) else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def latest_version(opener=open_url, timeout: float = 5) -> str | None:
    """Versione dell'ultima release, letta dall'URL finale di /releases/latest (niente API: limite per IP). Errore = None."""
    try:
        with opener(RELEASE_PAGE, timeout=timeout) as response:
            final = response.geturl()
    except Exception:
        return None
    if not final.startswith("https://github.com/"):  # un proxy o un reindirizzamento altrove non vale
        return None
    version = final.rpartition("/tag/v")[2]
    return version if parse_version(version) else None


def target() -> Path:
    """Ciò che si sostituisce: l'.exe in uso su Windows, il bundle .app su Mac."""
    exe = Path(sys.executable)
    if sys.platform != "darwin":
        return exe
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    raise UpdateError("Non trovo l'app da aggiornare.")


def enabled() -> bool:
    """Solo l'app compilata con una versione vera e non spenta dall'IT (variabile o file accanto all'app)."""
    if not getattr(sys, "frozen", False) or VERSION == "dev" or os.environ.get("ANONIMIZZA_NO_AGGIORNAMENTI"):
        return False
    try:
        return not (target().parent / NO_UPDATE_FILE).exists()
    except UpdateError:
        return False


def can_replace(path: Path) -> str | None:
    """None se si può sostituire, altrimenti il motivo (in italiano, senza percorsi, da mettere tra parentesi)."""
    path = Path(path)
    if sys.platform == "darwin" and "AppTranslocation" in path.parts:
        return "l'app gira da una cartella temporanea di macOS: spostala in Applicazioni e riaprila"
    try:
        tempfile.TemporaryFile(dir=path.parent).close()  # serve poter creare e rinominare accanto all'app
    except OSError:
        return "la cartella dell'app non è scrivibile"
    if sys.platform == "darwin" and not os.access(path, os.W_OK):  # spostare un bundle chiede permessi sul bundle stesso
        return "l'app non è scrivibile da questo utente"
    return None


def _sibling(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def _replace(src: Path, dst: Path) -> None:
    """os.replace con qualche nuovo tentativo se Windows nega l'accesso (file tenuto aperto un istante)."""
    for attempt in range(RETRIES):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)


def download_verified(version: str, dest: Path, opener=open_url) -> Path:
    """Scarica l'asset della piattaforma in dest e controlla l'impronta in SHA256SUMS.txt; se non torna cancella dest e lancia."""
    name = ASSETS.get(sys.platform)
    if not name:
        raise UpdateError("Aggiornamento automatico non supportato su questo sistema.")
    base = f"https://github.com/{REPO}/releases/download/v{version}/"
    try:
        with opener(base + "SHA256SUMS.txt", timeout=30) as response:
            sums = response.read().decode("utf-8", "replace")
        expected = next(
            (parts[0].lower() for parts in (line.split(None, 1) for line in sums.splitlines())
             if len(parts) == 2 and parts[1].strip().lstrip("*") == name),
            None,
        )
        if not expected:
            raise UpdateError("Aggiornamento rifiutato: impronta non trovata nella release.")
        digest = hashlib.sha256()
        with opener(base + name, timeout=30) as response, open(dest, "wb") as out:
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected:
            raise UpdateError("Aggiornamento rifiutato: l'impronta del file scaricato non corrisponde.")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


def install(version: str, opener=open_url, path: Path | None = None) -> Path:
    """Scarica, verifica e sostituisce l'app in uso; ripristina l'originale se fallisce. Ritorna ciò che va riavviato."""
    app = path or target()
    if sys.platform == "win32":
        old = _sibling(app, ".old")
        try:
            old.unlink(missing_ok=True)  # resto di un aggiornamento precedente
        except OSError:  # è ancora in esecuzione: c'è un'altra copia aperta
            raise UpdateError("Chiudi le altre finestre di Anonimizza DICOM e riprova.")
        fd, name = tempfile.mkstemp(dir=app.parent, prefix=app.name, suffix=".new")  # accanto all'.exe: stesso volume
        os.close(fd)
        new = Path(name)
        try:
            download_verified(version, new, opener)
            _replace(app, old)  # un .exe in esecuzione si può rinominare, non cancellare
            try:
                _replace(new, app)
            except BaseException:
                _replace(old, app)
                raise
        except BaseException:
            new.unlink(missing_ok=True)
            raise
        return app
    work = Path(tempfile.mkdtemp(prefix=".aggiornamento_", dir=app.parent))  # stessa cartella: la mossa è una rinomina
    backup = app.parent / f".{app.name}.vecchia"  # nella stessa cartella: spostarlo altrove chiederebbe permessi sul bundle
    try:
        zipped = download_verified(version, work / "nuova.zip", opener)
        subprocess.run(["ditto", "-x", "-k", str(zipped), str(work / "nuova")], check=True)
        found = next(iter(sorted((work / "nuova").glob("*.app"))), None)
        if not found:
            raise UpdateError("Aggiornamento non riuscito: l'archivio non contiene l'app.")
        shutil.rmtree(backup, ignore_errors=True)  # resto di un tentativo interrotto
        _replace(app, backup)
        try:
            _replace(found, app)
        except BaseException:
            _replace(backup, app)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if app.exists():  # se è fallito anche il ripristino, il backup è l'unica copia: resta per il recupero a mano
            shutil.rmtree(backup, ignore_errors=True)
    return app


def relaunch(path: Path) -> None:
    if sys.platform == "win32":
        # un onefile lanciato da un onefile deve estrarre i suoi file, non riusare quelli del genitore (che sta per uscire)
        subprocess.Popen([str(path)], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"))
    else:
        subprocess.Popen(["open", "-n", str(path)])


def cleanup_old() -> None:
    """Toglie i resti di un aggiornamento: l'.exe precedente (Windows) o le cartelle temporanee interrotte (Mac)."""
    if not enabled():
        return
    try:
        app = target()
    except UpdateError:
        return
    if sys.platform == "win32":
        leftovers = [_sibling(app, ".old"), *app.parent.glob(app.name + "*.new")]
    else:
        leftovers = [*app.parent.glob(".aggiornamento_*"), app.parent / f".{app.name}.vecchia"]
    for leftover in leftovers:
        try:
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)
        except OSError:
            pass
