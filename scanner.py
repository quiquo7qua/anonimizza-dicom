"""Trova i file DICOM in un CD o in una cartella e li raggruppa in serie."""
from __future__ import annotations

import os
import re
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pydicom
from pydicom.multival import MultiValue

# Prefisso della SOP Class -> motivo per cui la serie può avere testo nei pixel
RISKY_SOP = {
    "1.2.840.10008.5.1.4.1.1.7": "screenshot (secondary capture)",
    "1.2.840.10008.5.1.4.1.1.104": "documento PDF/CDA",
    "1.2.840.10008.5.1.4.1.1.88": "report strutturato",
    "1.2.840.10008.5.1.4.1.1.77.1": "foto / endoscopia",
    "1.2.840.10008.5.1.4.1.1.6": "ecografia",
    "1.2.840.10008.5.1.4.1.1.3": "ecografia",
}
RISKY_MODALITY = {
    "SC": "screenshot",
    "OT": "immagine 'altro'",
    "DOC": "documento",
    "SR": "report strutturato",
    "KO": "selezione immagini chiave",
    "PR": "annotazioni",
    "US": "ecografia",
}
RISKY_WORDS = re.compile(r"DOSE|REPORT|SCREEN(?!ING)|PROTOCOL")


@dataclass
class Series:
    patient_name: str
    patient_id: str
    birth_date: str
    accession: str
    study_uid: str
    study_date: str
    study_desc: str
    series_uid: str
    series_number: str
    series_desc: str
    modality: str
    files: list[Path] = field(default_factory=list)
    size_bytes: int = 0
    risk: str | None = None
    identifiers: set[str] = field(default_factory=set)


def _text(ds, keyword: str) -> str:
    value = ds.get(keyword)
    return "" if value is None else str(value)


def patient_key(name: str, patient_id: str) -> tuple[str, str]:
    """Chiave del paziente: ROSSI^MARIO e «rossi^mario^^^ » sono la stessa persona."""
    return " ".join(name.rstrip("^ ").split()).upper(), patient_id.strip()


def risk_reason(ds) -> str | None:
    """Motivo per cui l'immagine può avere testo nei pixel; None se sicura."""
    sop = _text(ds, "SOPClassUID")
    for prefix, why in RISKY_SOP.items():
        if sop == prefix or sop.startswith(prefix + "."):
            return why
    modality = _text(ds, "Modality").upper()
    if modality in RISKY_MODALITY:
        return RISKY_MODALITY[modality]
    image_type = _text(ds, "ImageType").upper()
    if "SCREEN SAVE" in image_type or "DOSE" in image_type:
        return "screenshot / dose"
    description = _text(ds, "SeriesDescription").upper()
    description = re.sub(r"(LOW|BASSA)\s*[_-]?\s*DOSE", "", description)
    match = RISKY_WORDS.search(description)
    if match:
        return f"descrizione contiene '{match.group(0).lower()}'"
    if _text(ds, "BurnedInAnnotation").upper() == "YES":
        return "testo impresso nei pixel"
    return None


def _values(ds, keyword: str) -> list[str]:
    value = ds.get(keyword)
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, MultiValue) else [str(value)]


def identifiers(ds) -> set[str]:
    """Dati originali che non devono comparire nell'esame anonimizzato (maiuscoli)."""
    found = set()
    for keyword in ("PatientName", "OtherPatientNames", "PatientBirthName", "PatientMotherBirthName"):
        for text in _values(ds, keyword):
            found |= {t for t in re.split(r"\W+", text.upper()) if len(t) >= 3}
    for keyword in ("PatientID", "OtherPatientIDs", "AccessionNumber"):
        for text in _values(ds, keyword):
            if len(text.strip()) >= 3:
                found.add(text.strip().upper())
    for item in ds.get("OtherPatientIDsSequence", []):
        text = _text(item, "PatientID").strip().upper()
        if len(text) >= 3:
            found.add(text)
    birth = _text(ds, "PatientBirthDate")
    if len(birth) == 8:
        found.add(birth)
    return found


def _is_dicom(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(132)[128:] == b"DICM"


def _number(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def scan(root: Path, progress=lambda done, total, text: None) -> tuple[list[Series], int, int]:
    """Legge solo le intestazioni. Ritorna (serie ordinate, file non DICOM, file o cartelle illeggibili)."""
    ignored = unreadable = 0

    def on_error(error):
        nonlocal unreadable
        unreadable += 1

    progress(0, 0, "Ricerca dei file")  # totale 0 = indeterminato
    paths = []
    for folder, dirs, names in os.walk(root, onerror=on_error):
        # cartelle di sistema di chiavette e dischi esterni: illeggibili per costruzione
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d != "System Volume Information"]
        paths += [
            Path(folder) / n for n in names
            if n.upper() != "DICOMDIR" and os.path.isfile(os.path.join(folder, n))  # niente FIFO
        ]
    paths.sort()  # stesso ordine su Mac e Windows
    found: dict[str, Series] = {}
    seen: set[str] = set()
    for done, path in enumerate(paths, 1):
        progress(done, len(paths), "Lettura del disco")
        try:
            if not _is_dicom(path):
                ignored += 1
                continue
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            uid = str(ds.SeriesInstanceUID)
            sop = _text(ds, "SOPInstanceUID")
            if sop and sop in seen:
                continue  # immagine duplicata
            size = path.stat().st_size
            reason = risk_reason(ds)
            ids = identifiers(ds)
            # tutto calcolato: da qui in poi si aggiorna lo stato
            seen.add(sop)
            series = found.get(uid)
            if series is None:
                series = found[uid] = Series(
                    patient_name=_text(ds, "PatientName"),
                    patient_id=_text(ds, "PatientID"),
                    birth_date=_text(ds, "PatientBirthDate"),
                    accession=_text(ds, "AccessionNumber"),
                    study_uid=_text(ds, "StudyInstanceUID"),
                    study_date=_text(ds, "StudyDate"),
                    study_desc=_text(ds, "StudyDescription"),
                    series_uid=uid,
                    series_number=_text(ds, "SeriesNumber"),
                    series_desc=_text(ds, "SeriesDescription"),
                    modality=_text(ds, "Modality"),
                )
            series.files.append(path)
            series.size_bytes += size
            series.risk = series.risk or reason
            series.identifiers |= ids
        except Exception:
            unreadable += 1
    first_date: dict[str, str] = {}
    for s in found.values():
        first_date[s.study_uid] = min(s.study_date, first_date.get(s.study_uid, s.study_date))
    ordered = sorted(found.values(), key=lambda s: (first_date[s.study_uid], s.study_uid, _number(s.series_number)))
    return ordered, ignored, unreadable


def _has_dicomdir(folder: Path) -> bool:
    try:
        return (folder / "DICOMDIR").is_file()
    except OSError:
        return False


def find_dicom_sources() -> list[Path]:
    """Dischi e chiavette montati che contengono un DICOMDIR (nella radice o un livello sotto)."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetErrorMode(1)  # niente popup "nessun disco nell'unità"
        roots = [
            Path(f"{letter}:\\")
            for letter in string.ascii_uppercase
            if kernel32.GetDriveTypeW(f"{letter}:\\") in (2, 5)  # removibile, CD/DVD
        ]
    else:
        volumes = Path("/Volumes")
        roots = list(volumes.iterdir()) if volumes.is_dir() else []
    found = []
    for root in roots:
        try:
            subdirs = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            subdirs = []
        found += [d for d in [root] + subdirs if _has_dicomdir(d)]
    return found
