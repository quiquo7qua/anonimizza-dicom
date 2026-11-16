"""Masterizza una cartella su CD/DVD con gli strumenti del sistema operativo."""
from __future__ import annotations

import filecmp
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SECTOR = 2048
_VERIFY_TIMEOUT = 120  # secondi di attesa del disco scritto (Windows)
_VERIFY_POLL = 2
_BURN_TIMEOUT = 150 * 60  # secondi, Mac: scrittura e verifica completa di un DVD a bassa velocità


@dataclass
class Drive:
    id: str  # Mac: numero di drutil; Windows: ID IMAPI2 del masterizzatore
    name: str


class BurnError(Exception):
    pass


def needed_bytes(folder: Path) -> int:
    """Spazio occupato sul disco: per ogni file i settori da 2048 byte più 3 di file system
    (misurato con drutil size), più 2 MB fissi per ISO 9660 + Joliet + UDF."""
    files = (p.stat().st_size for p in Path(folder).rglob("*") if p.is_file())
    return sum((-(-size // SECTOR) + 3) * SECTOR for size in files) + 2 * 1024 * 1024


# ---------- Mac: drutil ----------

def _parse_drutil_list(text: str) -> list[Drive]:
    drives = []
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            drives.append(Drive(id=parts[0], name=" ".join(parts[1].split())))
    return drives


def _parse_drutil_free(text: str) -> int | None:
    """Byte liberi su un disco vuoto, None se manca il disco o non è vuoto."""
    # ponytail: formato di drutil non documentato; da verificare con un masterizzatore reale
    writability = re.search(r"Writability:(.*)", text)
    if writability and "blank" not in writability.group(1).lower():
        return None
    match = re.search(r"Space Free:.*?blocks:\s*(\d+)", text)
    return int(match.group(1)) * SECTOR if match and int(match.group(1)) else None


def _drutil(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["drutil", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, errors="replace")


def _mac_error(output: str, returncode: int) -> str:
    """Una sola riga dall'output di drutil: la prima con 'failed', senza percorsi."""
    for line in output.splitlines():
        if "failed" in line.lower():
            return re.sub(r"(^|\s)/\S*", " ", line).strip()
    return f"drutil ha restituito {returncode}"


# ---------- Windows: IMAPI2 via pywin32 ----------

def _com(progid: str):
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()  # necessario in ogni thread che usa COM
    return win32com.client.Dispatch(progid)


def _com_error():
    """Solo gli errori COM sono «nessun disco / nessun masterizzatore»: un pywin32 rotto deve emergere."""
    import pywintypes

    return pywintypes.com_error


def _win_recorder(drive_id: str):
    recorder = _com("IMAPI2.MsftDiscRecorder2")
    recorder.InitializeDiscRecorder(drive_id)
    return recorder


def _win_format(recorder):
    fmt = _com("IMAPI2.MsftDiscFormat2Data")
    fmt.Recorder = recorder
    fmt.ClientName = "Anonimizza DICOM"
    return fmt


_WIN_ERRORS = {
    0xC0AA0202: "nessun disco",
    0xC0AA0203: "disco non compatibile",
    0xC0AA0204: "disco inserito al contrario",
    0xC0AA0205: "masterizzatore non ancora pronto, riprova tra qualche secondo",
    0xC0AA0207: "masterizzatore occupato",
    0xC0AA0209: "disco protetto da scrittura",
    0xC0AA0210: "masterizzatore usato da un altro programma",
    0xC0AA0300: "dati non arrivati in tempo, il disco è da buttare",
    0xC0AAB120: "esame troppo grande, usa un DVD",
    0xC0AAB121: "esame troppo grande, usa un DVD",
    0xC0AAB101: "nome del disco non valido",
}


def _win_error(exc) -> str:
    """Messaggio italiano di una riga da un pywintypes.com_error (o simile)."""
    info = getattr(exc, "excepinfo", None)
    code = (info[5] if info else 0) or getattr(exc, "hresult", None)
    if code is None:
        return (str(exc).strip().splitlines() or ["errore sconosciuto"])[0]
    code &= 0xFFFFFFFF
    if code in _WIN_ERRORS:
        return _WIN_ERRORS[code]
    description = (info[2] or "").strip() if info else ""
    return description.splitlines()[0] if description else f"errore {code:#010x}"


def _compare_tree(src: Path, dst: Path) -> str | None:
    """Primo problema trovato confrontando src con dst (byte per byte), None se uguali."""
    src, dst = Path(src), Path(dst)
    filecmp.clear_cache()  # un secondo disco allo stesso percorso e con gli stessi mtime non deve usare la cache
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        copy = dst / path.relative_to(src)
        if not copy.is_file():
            return f"manca il file {path.name}"
        if not filecmp.cmp(path, copy, shallow=False):
            return f"il file {path.name} è diverso"
    return None


def _keep_awake(on: bool) -> None:
    """Windows: impedisce la sospensione del PC durante la scrittura."""
    if sys.platform != "win32":
        return
    import ctypes

    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, oppure solo ES_CONTINUOUS per tornare normale
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)


def _win_verify(recorder, folder: Path) -> None:
    """Rilegge il disco appena scritto e lo confronta con la cartella di origine."""
    # ponytail: IMAPI2 non permette di impostare la verifica da pywin32, la facciamo noi
    paths = recorder.VolumePathNames
    if not paths:
        raise BurnError("verifica fallita: il disco non ha una lettera di unità")
    root = Path(paths[0])
    start = time.monotonic()
    cycled = False
    while not (root / "DICOMDIR").exists():
        elapsed = time.monotonic() - start
        if elapsed > _VERIFY_TIMEOUT:
            raise BurnError("disco scritto ma non verificato: controllalo su un altro computer")
        if not cycled and elapsed > _VERIFY_TIMEOUT / 2:
            cycled = True  # una volta sola: espelle e richiude il vassoio per far rileggere il disco a Windows
            for step in ("EjectMedia", "CloseTray"):
                try:
                    getattr(recorder, step)()
                except Exception:
                    pass  # errori ignorati: il vassoio potrebbe non muoversi
        time.sleep(_VERIFY_POLL)
    try:
        problem = _compare_tree(folder, root)
    except OSError:
        raise BurnError("verifica fallita: errore di lettura del disco") from None
    if problem:
        raise BurnError(f"verifica fallita: {problem}")


# ---------- API ----------

def list_drives() -> list[Drive]:
    if sys.platform == "darwin":
        return _parse_drutil_list(_drutil("list").stdout)
    if sys.platform == "win32":
        try:
            ids = list(_com("IMAPI2.MsftDiscMaster2"))
            fmt = _com("IMAPI2.MsftDiscFormat2Data")
        except _com_error():  # IMAPI non disponibile
            return []
        drives = []
        for drive_id in ids:
            try:
                recorder = _win_recorder(drive_id)
                if not fmt.IsRecorderSupported(recorder):
                    continue  # lettore o unità virtuale
                letters = " ".join(recorder.VolumePathNames)
                drives.append(Drive(id=drive_id, name=f"{letters} {recorder.ProductId.strip()}".strip()))
            except _com_error():  # un'unità difettosa non deve rompere l'elenco
                continue
        return drives
    return []


def media_free_bytes(drive: Drive) -> int | None:
    """Byte disponibili su un disco vuoto nel masterizzatore; None se non c'è un disco vuoto."""
    if sys.platform == "darwin":
        return _parse_drutil_free(_drutil("-drive", drive.id, "status").stdout)
    try:
        recorder = _win_recorder(drive.id)
        fmt = _win_format(recorder)
        if not fmt.IsCurrentMediaSupported(recorder) or not fmt.MediaHeuristicallyBlank:
            return None
        return fmt.FreeSectorsOnMedia * SECTOR
    except _com_error():  # nessun disco o disco non leggibile
        return None


def eject(drive: Drive) -> bool:
    """True se il disco è stato espulso."""
    if sys.platform == "darwin":
        return _drutil("-drive", drive.id, "tray", "eject").returncode == 0
    try:
        _win_recorder(drive.id).EjectMedia()
        return True
    except _com_error():
        return False  # alcuni masterizzatori (slot, portatili) non espellono da software


def _mac_burn(folder: Path, drive: Drive) -> None:
    cmd = ["drutil", "-drive", drive.id, "burn", "-nohfsplus", "-noappendable", "-eject"]
    if os.environ.get("BURN_TEST"):
        cmd.append("-test")  # prova di scrittura senza masterizzare davvero
    proc = subprocess.Popen(cmd + [str(folder)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")
    awake = None
    try:
        # caffeinate -i: il Mac non va in stop finché drutil è in esecuzione
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(proc.pid)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    try:
        try:
            output, _ = proc.communicate(timeout=_BURN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise BurnError("tempo scaduto: il masterizzatore non ha finito") from None
    finally:
        if awake:
            awake.terminate()
    # ponytail: "Burn completed" / "Test complete" sono nel binario di drutil; da verificare con un masterizzatore reale
    done = ("Burn completed", "Test complete") if os.environ.get("BURN_TEST") else ("Burn completed",)
    if proc.returncode != 0:
        raise BurnError(_mac_error(output, proc.returncode))
    if not any(text in output for text in done):
        raise BurnError(_mac_error(output, 0) if "failed" in output.lower()
                        else "drutil non ha confermato la masterizzazione")


def burn(folder: Path, drive: Drive) -> None:
    """Masterizza il contenuto di folder (ISO 9660 + Joliet + UDF), verifica ed espelle.
    L'etichetta del disco è il nome della cartella."""
    folder = Path(folder)
    if sys.platform == "darwin":
        _mac_burn(folder, drive)
        return
    recorder = fmt = image = None
    _keep_awake(True)
    try:
        recorder = _win_recorder(drive.id)
        fmt = _win_format(recorder)
        fmt.ForceMediaToBeClosed = True  # disco chiuso: leggibile ovunque
        image = _com("IMAPI2FS.MsftFileSystemImage")
        image.ChooseImageDefaults(recorder)
        image.FileSystemsToCreate = 1 | 2 | 4  # ISO 9660 + Joliet + UDF
        image.VolumeName = folder.name[:15]  # IMAPI2: massimo 15 caratteri
        image.Root.AddTree(str(folder), False)
        fmt.Write(image.CreateResultImage().ImageStream)
        _win_verify(recorder, folder)
        try:
            recorder.EjectMedia()
        except Exception:
            pass  # la scrittura è riuscita: un'espulsione fallita non è un errore
    except BurnError:
        raise
    except Exception as exc:
        raise BurnError(_win_error(exc)) from exc
    finally:
        recorder = fmt = image = None  # rilascio COM nello stesso thread
        _keep_awake(False)
