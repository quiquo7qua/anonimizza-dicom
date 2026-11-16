# Anonimizza e Masterizza DICOM: piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** app desktop Windows/Mac che legge un esame DICOM da CD o cartella, lo anonimizza e lo masterizza su CD/DVD o lo salva in una cartella.

**Architecture:** quattro moduli Python con un compito ciascuno (`scanner`, `anonymizer`, `burner`, `app`) più un file di test con semplici `assert`. I pixel non vengono mai decodificati: solo i tag vengono riscritti. Il DICOMDIR è scritto dal nostro codice in tempo lineare; la masterizzazione usa `drutil` su Mac e IMAPI2 su Windows.

**Tech Stack:** Python 3.12, pydicom 3.0.2, Tkinter (libreria standard), pywin32 (solo Windows), PyInstaller per creare le app.

Spec: `docs/superpowers/specs/2026-11-16-dicom-anon-burner-design.md`

---

## Cambiamenti rispetto alla spec

Sono emersi preparando il piano. Tutto il codice qui sotto è già stato provato in un prototipo (12 test verdi).

1. **DICOMDIR scritto da noi invece che da `pydicom.FileSet`.** `FileSet.add` è quadratico: con 2000 immagini impiega 11 s, con 10.000 circa 5 minuti. La nostra versione usa i record standard di pydicom (`DIRECTORY_RECORDERS`) e scrive il file in due passaggi: il primo con gli offset a zero, il secondo con i valori giusti (gli offset hanno lunghezza fissa, quindi le posizioni non cambiano). Misurato: 5000 immagini in 16 s, e il DICOMDIR prodotto si rilegge con `FileSet`.
2. **Un solo processo invece di più processi.** Da CD il limite è la velocità di lettura del disco. Da cartella locale: 5000 immagini in circa 22 s in totale.
3. **Date con interruttore spento: valore fittizio `19000101` / `000000` ovunque, invece di svuotarle.** Il DICOMDIR richiede date non vuote anche nei record SR, quindi una sola regola copre tutti i casi.
4. **Windows: pywin32 invece di comtypes.** Gli esempi IMAPI2 di Microsoft si traducono riga per riga in `win32com.client.Dispatch`, e PyInstaller ha già il supporto per pywin32.
5. **Progresso della masterizzazione indeterminato** (barra animata, senza percentuale). Gli eventi di IMAPI2 richiederebbero molto codice in più.
6. **Il PC resta sveglio durante la masterizzazione** (`caffeinate` su Mac, `SetThreadExecutionState` su Windows). Microsoft avverte che sospensione o logoff interrompono la scrittura.
7. **Il disco originale viene espulso prima di chiedere quello vuoto.** Quasi tutti i PC hanno un solo lettore: si legge il CD, si anonimizza nella cartella temporanea, si espelle, si inserisce il disco vuoto.
8. **Rischio testo nei pixel:** aggiunte le foto e le endoscopie (SOP Class VL `…1.77.1`).
9. **PDF/CDA incapsulati sempre esclusi**, anche se rispuntati: il testo di un PDF compresso non si può controllare.
10. **`burn(folder, drive)` senza parametro `progress`;** l'etichetta del disco è il nome della cartella.

Dopo la review di qualità del task 1:

11. **`scan()` restituisce `(serie, ignorati, illeggibili)`.** I file illeggibili (CD graffiato) sono separati dai file non DICOM, e la finestra avvisa che l'esame potrebbe essere incompleto.
12. **Identificativi raccolti da ogni file** in `Series.identifiers`, compresi `OtherPatientIDs` (spesso il codice fiscale), `PatientBirthName` e `PatientMotherBirthName`. `check_leaks` li usa tutti e restituisce una riga per ogni coppia campo/dato, con un esempio del valore.
13. **Il controllo fughe chiede conferma invece di bloccare sempre.** Un cognome che è anche una parola comune (Testa → «TC TESTA») bloccherebbe l'esame senza via d'uscita. L'utente vede campo e valore e decide; il default è «No».
14. **Serie a rischio:** «LOW DOSE» e «SCREENING» non sono più a rischio (falsi allarmi su esami comunissimi); le ecografie sono riconosciute anche dalla SOP Class.
15. **Più dischi con esami:** la finestra chiede quale usare.
16. **Altre correzioni:** immagini duplicate contate una volta sola; studi ordinati per data dello studio; `find_dicom_sources` restituisce la cartella che contiene il DICOMDIR; cartelle di sistema (`.Trashes`, `System Volume Information`…) saltate; solo file regolari; «BASSA DOSE» come «LOW DOSE»; codice fiscale letto anche da `OtherPatientIDsSequence`.

Dopo la review di qualità del task 2 (verificata anche con DCMTK sull'intero set di test di pydicom):

17. **Più attributi rimossi:** tutti gli AE title (spesso il nome dell'ospedale) e tutte le righe X/Z della PS3.15 Table E.1-1, compresa l'etichetta del CD originale (`StorageMediaFileSetID`). Restano apposta le sequenze strutturali con UID rimappati (`ReferencedImageSequence`, `SourceImageSequence`, `ContentSequence`), le descrizioni e `ROIName`.
18. **Controllo fughe senza falsi allarmi:** gli UID vengono saltati e gli identificativi si cercano con confini per tipo di carattere. Così «48213» non scatta dentro un UID o in «1048213.5», ma si trova in «PZ48213». Limite noto: nome e cognome attaccati («ROSSIMARIO»).
19. **Report strutturati (SR) leggibili:** `PersonName` e `VerifyingObserverName` diventano «ANONIMO» invece di essere cancellati. Gli attributi di tipo 2 (es. data di nascita) restano presenti ma vuoti.
20. **Implicit VR riscritto come Explicit VR,** come richiesto per i CD DICOM. Se la conversione non è possibile (vecchi tag ambigui), il file viene scritto com'è.
21. **Errori di scrittura** (disco pieno): il file parziale viene cancellato e il lavoro si ferma. I messaggi di errore stanno su una riga, senza percorsi.
22. **Interruttore rinominato** in «Mantieni età, sesso, peso e date (serve per PET e studi dinamici)»: senza, orari e peso vanno persi.

Dopo la review di qualità del task 3 (lato Windows verificato sulla documentazione Microsoft e sul sorgente di pywin32):

23. **Nome del volume su Windows al massimo 15 caratteri** (limite IMAPI2/UDF); il nome automatico ne aveva 16.
24. **Verifica vera anche su Windows:** dopo la scrittura l'app rilegge i file dal disco e li confronta con l'originale (`filecmp`, svuotando la cache a ogni disco). Se il disco non diventa leggibile: «disco scritto ma non verificato». Su Mac resta la verifica completa di `drutil`.
25. **Stima dello spazio più prudente:** 3 settori in più per ogni file, confrontata con `drutil size`.
26. **Su Mac `drutil` non può bloccarsi all'infinito:** timeout di 90 minuti. La masterizzazione è riuscita solo se compare «Burn completed» (oppure «Test complete» in modalità prova, `BURN_TEST=1`).
27. **Un'espulsione fallita non trasforma una masterizzazione riuscita in un errore.** Se il CD originale non esce, l'app chiede di estrarlo a mano.
28. **Su Windows vengono elencati solo i masterizzatori veri** (niente lettori di sola lettura o unità virtuali). Gli errori diventano messaggi in italiano su una riga.

Dopo la review di qualità del task 4 (flussi provati pilotando la finestra con dialoghi finti):

29. **La finestra non si blocca mai in silenzio:**
    - un errore in qualunque fase diventa un messaggio, e la finestra resta usabile;
    - chiudere mentre si lavora ferma il lavoro e cancella i file temporanei e le copie parziali;
    - su Mac anche Cmd+Q passa dalla conferma;
    - il default della conferma è «No».
30. **Annullare dopo l'espulsione del CD originale propone di salvare l'esame anonimo in una cartella,** invece di buttarlo.
31. **Più pazienti:** ogni studio mostra il nome del paziente e viene preselezionato solo il primo.
32. **Interfaccia più robusta:**
    - barra di scorrimento nell'elenco delle serie;
    - le sezioni in basso restano visibili con schermi piccoli o caratteri grandi;
    - la barra spaziatrice include o esclude una serie;
    - controllo «troppo grande per un DVD» prima di anonimizzare;
    - nome vuoto → nome automatico;
    - errori del sistema (disco pieno, CD tolto…) in italiano;
    - finito il lavoro, il nome del paziente originale sparisce dallo schermo.
33. **Modalità `--selftest <cartella>`:** legge, anonimizza e controlla senza finestra, con codice di uscita. Verifica che l'app compilata (`.app` ed `.exe`) contenga tutto; il workflow GitHub la usa su entrambi i sistemi.

Dopo la review finale dell'intera applicazione (flusso completo provato su tutti i file di test di pydicom e su un CD simulato con 4 pazienti):

34. **CD scambiato tra lettura e masterizzazione:** i file di un altro paziente vengono scartati («il disco è cambiato: rileggi il CD»). Il controllo fughe usa anche gli identificativi letti durante l'anonimizzazione.
35. **Immagini non copiate:** se qualche file viene scartato (illeggibile, troncato, disco cambiato), l'app chiede conferma prima di espellere o salvare. Default «No».
36. **L'interruttore «Mantieni…» torna spento a ogni nuovo esame.** Lo stesso paziente scritto in modi diversi (`ROSSI^MARIO^^^`) è riconosciuto come uno solo. L'espulsione avviene solo se la sorgente era un CD.
37. **Windows:** pywin32 mancante o rotto produce un errore chiaro, non «nessun masterizzatore». `--selftest` controlla che pywin32 sia incluso nell'`.exe`.
38. **Chiavette USB su Mac:** i file nascosti `._*` e `.DS_Store` creati dal Finder non fanno scattare il controllo fughe.
39. **Varie:**
    - errori nascosti mostrati anche nell'app compilata;
    - cartelle temporanee rimaste da un crash eliminate all'avvio;
    - timeout di `drutil` portato a 150 minuti;
    - workflow con timeout e senza finestra di traceback;
    - app Mac solo per chip Apple (per Mac Intel va compilata su un Mac Intel);
    - istruzioni di primo avvio aggiornate a macOS 15.

## Note per chi esegue

- La cartella del progetto **non è un repository git**: i passi di commit sono omessi. Se viene messa sotto git, fare un commit alla fine di ogni task.
- Tutti i comandi si eseguono dalla cartella del progetto. Python del progetto: `.venv/bin/python` su Mac, `.venv\Scripts\python` su Windows.
- I test si avviano con `python test_anon.py`: stampano `ok  test_…` per ogni test e `TUTTI I TEST PASSATI` alla fine. Al primo `assert` fallito si fermano con un traceback.

## Struttura dei file

| File | Responsabilità |
|---|---|
| `requirements.txt` | Dipendenze con versioni fissate |
| `scanner.py` | Trova i file DICOM, li raggruppa in serie, segna le serie a rischio, trova i CD con DICOMDIR |
| `anonymizer.py` | Regole di anonimizzazione, scrittura dei file e del DICOMDIR, controllo fughe |
| `burner.py` | Elenco masterizzatori, spazio libero, espulsione, masterizzazione (Mac/Windows) |
| `app.py` | Finestra Tkinter; il lavoro lungo gira in un thread |
| `test_anon.py` | Tutti i test (assert semplici, nessun framework) |
| `.github/workflows/build.yml` | Compila `.exe` e `.app` su GitHub (task 6) |
| `README.md` | Uso, compilazione, avvertenze |

---

### Task 0: Ambiente

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: Crea `requirements.txt`**

```text
pydicom==3.0.2
pywin32==308; sys_platform == "win32"
```

- [ ] **Step 2: Crea l'ambiente virtuale e installa**

Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
Expected: `Successfully installed pydicom-3.0.2`

- [ ] **Step 3: Verifica pydicom e Tk**

Run: `.venv/bin/python -c "import pydicom, tkinter; print(pydicom.__version__, tkinter.TkVersion)"`
Expected: `3.0.2 8.6`

---

### Task 1: Scanner

**Files:**
- Create: `test_anon.py`
- Create: `scanner.py`

- [ ] **Step 1: Scrivi i test dello scanner**

Crea `test_anon.py` con questo contenuto:

```python
"""Test di scanner, anonimizzatore e parser del masterizzatore. Avvio: python test_anon.py"""
import os
import shutil
import tempfile
from pathlib import Path

import pydicom
from pydicom.data import get_testdata_file
from pydicom.dataset import Dataset

import scanner

CT = get_testdata_file("CT_small.dcm")
MR = get_testdata_file("MR_small.dcm")
SC = get_testdata_file("SC_rgb_small_odd.dcm")
ORIGINAL = ["ROSSI", "MARIO", "PID12345", "ACC998877", "19600312", "VERDI", "OSPEDALE TEST"]

def make_source(root: Path, n: int = 3, **extra) -> Path:
    """n immagini TC della stessa serie con dati identificativi realistici, più un file non DICOM."""
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        ds = pydicom.dcmread(CT)
        ds.PatientName = "ROSSI^MARIO"
        ds.PatientID = "PID12345"
        ds.PatientBirthDate = "19600312"
        ds.AccessionNumber = "ACC998877"
        ds.InstitutionName = "Ospedale Test"
        ds.ReferringPhysicianName = "VERDI^LUCA"
        ds.PatientAge = "063Y"
        ds.PatientSex = "M"
        ds.StudyDate = "20260312"
        ds.SOPInstanceUID = f"1.2.3.4.{i + 1}"
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.InstanceNumber = i + 1
        ds.add_new(0x00090010, "LO", "ACME")  # tag privato
        ds.add_new(0x00091001, "LO", "ROSSI MARIO")
        ds.add_new(0x60000010, "US", 4)  # overlay
        ref = Dataset()
        ref.ReferencedSOPClassUID = ds.SOPClassUID
        ref.ReferencedSOPInstanceUID = "1.2.3.4.1"
        ref.add_new(0x0040A123, "PN", "ROSSI^MARIO")  # nome dentro una sequenza
        ds.ReferencedImageSequence = [ref]
        for keyword, value in extra.items():
            setattr(ds, keyword, value)
        ds.save_as(root / f"img{i}.dcm")
    (root / "LEGGIMI.txt").write_text("viewer del CD")
    return root


def test_scan_groups_and_flags(tmp: Path):
    src = make_source(tmp / "src")
    deep = src / "sub" / "deep"
    deep.mkdir(parents=True)
    shutil.copy(MR, deep / "mr")
    shutil.copy(SC, deep / "sc")
    shutil.copy(get_testdata_file("DICOMDIR"), src / "DICOMDIR")  # non va contato
    shutil.copy(src / "img0.dcm", src / "copia_img0")  # immagine duplicata
    (src / "rotto").write_bytes(bytes(128) + b"DICM" + b"spazzatura")
    series, ignored, unreadable = scanner.scan(src)
    assert ignored == 1, ignored  # LEGGIMI.txt
    assert unreadable == 1, unreadable  # rotto
    by_modality = {s.modality: s for s in series}
    assert len(by_modality["CT"].files) == 3
    for word in ("ROSSI", "MARIO", "PID12345", "ACC998877", "19600312"):
        assert word in by_modality["CT"].identifiers, word
    assert by_modality["CT"].risk is None
    assert by_modality["MR"].risk is None
    sc = [s for s in series if s.modality not in ("CT", "MR")][0]
    assert sc.risk, "la secondary capture deve essere a rischio"


def test_scan_unreadable(tmp: Path):
    if os.name == "nt" or os.geteuid() == 0:
        print("   (saltato: servono permessi POSIX e utente non root)")
        return
    src = make_source(tmp / "src")
    locked = src / "bloccata"
    locked.mkdir()
    shutil.copy(src / "img0.dcm", locked / "x.dcm")
    trashes = src / ".Trashes"
    trashes.mkdir()
    (src / "img1.dcm").chmod(0)
    locked.chmod(0)
    trashes.chmod(0)
    try:
        series, ignored, unreadable = scanner.scan(src)
    finally:
        for d in (locked, trashes):
            d.chmod(0o755)
        (src / "img1.dcm").chmod(0o644)
    assert unreadable == 2, unreadable  # file e cartella; .Trashes è esclusa
    assert ignored == 1, ignored


def test_scan_merges_files(tmp: Path):
    src = make_source(tmp / "src")
    ds = pydicom.dcmread(src / "img0.dcm")
    ds.SOPInstanceUID = "1.2.3.4.99"
    ds.OtherPatientIDs = ["ALTROID77"]
    ds.BurnedInAnnotation = "YES"
    ds.save_as(src / "img1b.dcm")
    (ct,), _, _ = scanner.scan(src)
    assert len(ct.files) == 4
    assert {"PID12345", "ALTROID77"} <= ct.identifiers
    assert ct.risk


def test_scan_sort(tmp: Path):
    src = tmp / "src"
    src.mkdir()
    for i, (study, series, date) in enumerate([("1.9.1", "1.9.1.1", "20260102"),
                                               ("1.9.2", "1.9.2.1", "20260103"),
                                               ("1.9.1", "1.9.1.2", "20260105")]):
        ds = pydicom.dcmread(CT)
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.StudyDate = study, series, date
        ds.SOPInstanceUID = f"1.9.9.{i}"
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.save_as(src / f"f{i}.dcm")
    order = [s.study_uid for s in scanner.scan(src)[0]]
    assert order == ["1.9.1", "1.9.1", "1.9.2"], order


def test_risk_rules():
    ds = Dataset()
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT
    ds.Modality = "CT"
    ds.ImageType = ["DERIVED", "SECONDARY", "REFORMATTED"]  # MPR: va tenuta
    assert scanner.risk_reason(ds) is None
    ds.SeriesDescription = "Dose Report"
    assert scanner.risk_reason(ds)
    for ok in ("TORACE LOW DOSE", "LDCT Lung Screening", "TORACE BASSA DOSE", "TORACE LOW  DOSE"):
        ds.SeriesDescription = ok
        assert scanner.risk_reason(ds) is None, ok
    del ds.SeriesDescription
    ds.BurnedInAnnotation = "YES"
    assert scanner.risk_reason(ds)
    del ds.BurnedInAnnotation
    ds.ImageType = ["DERIVED", "SECONDARY", "SCREEN SAVE"]
    assert scanner.risk_reason(ds)
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    for sop in ("1.2.840.10008.5.1.4.1.1.77.1.4", "1.2.840.10008.5.1.4.1.1.7"):
        ds.SOPClassUID = sop
        assert scanner.risk_reason(ds), sop
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.77"  # non è .7
    assert scanner.risk_reason(ds) is None
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.6.1"  # ecografia
    ds.Modality = "IVUS"
    assert scanner.risk_reason(ds) == "ecografia"
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.Modality = "US"
    assert scanner.risk_reason(ds) == "ecografia"


def test_identifiers():
    ds = Dataset()
    ds.PatientName = "DE ROSSI^MARIA"
    ds.PatientBirthName = "BIANCHI^ANNA"
    ds.OtherPatientIDs = ["RSSMRA60C52H501X", "12"]
    ds.PatientBirthDate = "19600312"
    found = scanner.identifiers(ds)
    for word in ("ROSSI", "MARIA", "BIANCHI", "RSSMRA60C52H501X", "19600312"):
        assert word in found, word
    assert "DE" not in found and "12" not in found
    item = Dataset()
    item.PatientID = "RSSMRA60C52H501X"
    item.IssuerOfPatientID = "CF"
    other = Dataset()
    other.OtherPatientIDsSequence = [item]
    assert "RSSMRA60C52H501X" in scanner.identifiers(other)


if __name__ == "__main__":
    import inspect

    for name, test in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as tmp:
                args = [Path(tmp)] if inspect.signature(test).parameters else []
                test(*args)
            print("ok ", name)
    print("TUTTI I TEST PASSATI")
```

- [ ] **Step 2: Verifica che fallisca**

Run: `.venv/bin/python test_anon.py`
Expected: `ModuleNotFoundError: No module named 'scanner'`

- [ ] **Step 3: Crea `scanner.py`**

```python
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
```

- [ ] **Step 4: Verifica che passi**

Run: `.venv/bin/python test_anon.py`
Expected: 6 righe `ok  test_…` (scan, unreadable, merges_files, sort, risk_rules, identifiers) e `TUTTI I TEST PASSATI`

---

### Task 2: Anonimizzatore, DICOMDIR e controllo fughe

**Files:**
- Modify: `test_anon.py` (import in cima + nuovi test prima di `if __name__`)
- Create: `anonymizer.py`

- [ ] **Step 1: Aggiungi i test dell'anonimizzatore**

In `test_anon.py`, sostituisci tutto ciò che sta tra la docstring iniziale e `def make_source` (import e costanti) con:

```python
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pydicom
from pydicom.data import get_testdata_file
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.datadict import tag_for_keyword
from pydicom.fileset import FileSet
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian

import anonymizer as an
import scanner

CT = get_testdata_file("CT_small.dcm")
MR = get_testdata_file("MR_small.dcm")
MR_IMPLICIT = get_testdata_file("MR_small_implicit.dcm")
SC = get_testdata_file("SC_rgb_small_odd.dcm")
ORIGINAL = ["ROSSI", "MARIO", "PID12345", "ACC998877", "19600312", "VERDI", "OSPEDALE TEST"]
```

Poi inserisci questi test prima della riga `if __name__ == "__main__":`:

```python
def run(tmp: Path, keep_clinical: bool = False, **extra):
    series, *_ = scanner.scan(make_source(tmp / "src", **extra))
    out = tmp / "out" / "CASO_01"
    report = an.anonymize(series, out, "CASO_01", keep_clinical)
    return series, out, report


def outputs(out: Path) -> list[Dataset]:
    return [pydicom.dcmread(p) for p in sorted(out.rglob("IM*"))]


def test_roiname_kept():
    assert "ROIName" not in an.REMOVE_KEYWORDS


def test_keywords_valid():
    for keyword in an.REMOVE_KEYWORDS + an.CLINICAL_KEYWORDS + list(an.META_REMOVE):
        assert tag_for_keyword(keyword) is not None, keyword
    assert len(set(an.REMOVE_KEYWORDS)) == len(an.REMOVE_KEYWORDS)


def test_anonymize_removes_identifiers(tmp: Path):
    series, out, report = run(tmp)
    assert report.written == 3 and not report.skipped, report
    assert an.check_leaks(out, series) == []
    images = outputs(out)
    for ds in images:
        assert str(ds.PatientName) == "CASO_01" and ds.PatientID == "CASO_01"
        assert not ds.get("PatientBirthDate") and "PatientAge" not in ds and not ds.get("PatientSex")
        assert not ds.get("ReferringPhysicianName") and not ds.get("AccessionNumber")
        assert "InstitutionName" not in ds
        for keyword in ("PatientBirthDate", "PatientSex", "ReferringPhysicianName", "AccessionNumber"):
            assert keyword in ds, keyword  # Type 2: presenti ma vuoti
        assert ds.StudyDate == "19000101"
        assert not any(e.tag.is_private for e in ds.iterall())
        assert not any(e.tag.group == 0x6000 for e in ds)
        assert ds.PatientIdentityRemoved == "YES"
        assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
        assert ds.SOPClassUID == "1.2.840.10008.5.1.4.1.1.2"  # classe standard invariata
        raw = Path(ds.filename).read_bytes().upper()
        for value in ORIGINAL:
            assert value.encode() not in raw, value
    assert len({ds.SeriesInstanceUID for ds in images}) == 1
    assert len({ds.StudyInstanceUID for ds in images}) == 1
    assert len({ds.SOPInstanceUID for ds in images}) == 3
    assert "1.2.3.4.1" not in {ds.SOPInstanceUID for ds in images}
    first = next(ds for ds in images if ds.InstanceNumber == 1)
    ref = images[1].ReferencedImageSequence[0]
    assert ref.ReferencedSOPInstanceUID == first.SOPInstanceUID  # riferimenti ancora collegati
    assert ref.PersonName == "ANONIMO"  # PN dentro una sequenza: valore fittizio, non il nome vero


def test_dicomdir_valid(tmp: Path):
    _, out, _ = run(tmp)
    fs = FileSet(out / "DICOMDIR")
    assert len(fs) == 3
    assert all(Path(instance.path).is_file() for instance in fs)
    assert len(fs.find(PatientID="CASO_01")) == 3
    names = [p.relative_to(out).parts for p in out.rglob("*") if p.is_file()]
    assert all(len(part) <= 8 and part == part.upper() for parts in names for part in parts)


def test_keep_clinical(tmp: Path):
    series, out, _ = run(tmp, keep_clinical=True)
    assert an.check_leaks(out, series) == []
    ds = outputs(out)[0]
    assert ds.StudyDate == "20260312" and ds.PatientAge == "063Y" and ds.PatientSex == "M"
    assert not ds.get("PatientBirthDate")


def test_leak_detected(tmp: Path):
    series, out, _ = run(tmp, SeriesDescription="Torace di Rossi")
    leaks = an.check_leaks(out, series)
    assert leaks and "SeriesDescription" in leaks[0], leaks


def test_encapsulated_pdf_skipped(tmp: Path):
    series, out, report = run(tmp, SOPClassUID="1.2.840.10008.5.1.4.1.1.104.1")
    assert report.written == 0 and len(report.skipped) == 3, report


def test_names():
    assert an.disc_label("caso 01-a") == "CASO_01_A"
    assert len(an.disc_label("x" * 64)) == 16
    assert an.NAME_RE.fullmatch(an.auto_name())
    for bad in ("", "Rossì", "a/b", "x" * 65, " ", "   ", " a"):
        assert not an.NAME_RE.fullmatch(bad), bad
    assert an.NAME_RE.fullmatch("caso 01") and an.NAME_RE.fullmatch("x" * 64)
    assert len(an.METHOD_BASIC) <= 64 and len(an.METHOD_KEEP) <= 64


def test_leak_pattern():
    pattern = an._pattern({"48213", "ROSSI", "MARIO", "NICOLÒ", "26004417"})
    for ok in ("TC TORACE ROSSI", "TC_ROSSI", "48213", "PID-48213.", "ACC26004417", "PZ48213",
               "ROSSI2", "MARIO1960", "NICOLÒ2"):
        assert pattern.search(ok), ok
    for bad in ("ROSSINI", "IM048213", "1048213.5", "1099.3100585938", "PZ482130"):
        assert not pattern.search(bad), bad
    assert an._pattern(set()) is None


def _fake_series(*ids) -> list:
    return [SimpleNamespace(identifiers=set(ids))]


def test_check_leaks_ignores_uid_and_non_dicom(tmp: Path):
    out = tmp / "out"
    out.mkdir()
    ds = pydicom.dcmread(CT)
    ds.StudyInstanceUID = "2.25.48213.7"  # UID: non può contenere dati del paziente
    ds.StudyDescription = "TC 48213"
    ds.save_as(out / "IM000001")
    (out / "LEGGIMI.txt").write_text("viewer")
    leaks = an.check_leaks(out, _fake_series("48213"))
    assert any(line.startswith("StudyDescription") for line in leaks), leaks
    assert not any("StudyInstanceUID" in line for line in leaks), leaks
    assert any("LEGGIMI.txt" in line and "non DICOM" in line for line in leaks), leaks
    assert an.check_leaks(out, _fake_series()) == ["LEGGIMI.txt: file non DICOM nell'esame"]  # senza dati: solo i file estranei


def test_check_leaks_file_meta(tmp: Path):
    out = tmp / "out"
    out.mkdir()
    ds = pydicom.dcmread(CT)
    ds.file_meta.SourceApplicationEntityTitle = "OSP_ROSSI"
    ds.save_as(out / "IM000001")
    leaks = an.check_leaks(out, _fake_series("ROSSI"))
    assert any(line.startswith("SourceApplicationEntityTitle") for line in leaks), leaks


def test_more_rules(tmp: Path):
    src = make_source(tmp / "src", n=1)
    ds = pydicom.dcmread(src / "img0.dcm")
    study_uid = str(ds.StudyInstanceUID)
    ds.file_meta.SourceApplicationEntityTitle = "OSP_ROSSI"
    ds.RetrieveAETitle = "OSP_ROSSI_PACS"
    ds.StationAETitle = "OSP_ROSSI_PACS"
    ds.StorageMediaFileSetID = "ROSSI MARIO"
    ds.RetrieveURL = f"http://pacs.ospedale.it/studies/{study_uid}"
    ds.add_new(0x60020010, "US", 4)
    ds.add_new(0x50000005, "US", 4)  # curva
    code = Dataset()
    code.CodeValue = "T-28000"
    code.CodingSchemeDesignator = "SRT"
    code.CodingSchemeUID = "2.16.840.1.113883.6.96"
    code.CodeMeaning = "Lung"
    ds.ProcedureCodeSequence = [code]
    ds.StudyTime = "101500"
    del ds.Modality
    ds.save_as(src / "img0.dcm")
    series, *_ = scanner.scan(src)
    out = tmp / "out"
    report = an.anonymize(series, out, "CASO_01", False)
    assert report.written == 1 and not report.skipped, report
    (res,) = outputs(out)
    for keyword in ("RetrieveAETitle", "StationAETitle", "StorageMediaFileSetID", "RetrieveURL"):
        assert keyword not in res, keyword
    assert "SourceApplicationEntityTitle" not in res.file_meta
    assert not any(e.tag.group in (0x6002, 0x5000) for e in res.iterall())
    assert res.ProcedureCodeSequence[0].CodingSchemeUID == "2.16.840.1.113883.6.96"
    assert res.StudyTime == "000000"
    assert res.Modality == "OT"
    for path in (p for p in out.rglob("*") if p.is_file()):
        raw = path.read_bytes()
        for value in (b"OSP_ROSSI", b"1.2.3.4.", study_uid.encode()):
            assert value not in raw, (path.name, value)


def test_dicomdir_two_studies(tmp: Path):
    src = make_source(tmp / "src")
    shutil.copy(MR, src / "mr.dcm")
    series, *_ = scanner.scan(src)
    out = tmp / "out"
    report = an.anonymize(series, out, "CASO_01", False)
    assert report.written == 4 and not report.skipped, report
    fs = FileSet(out / "DICOMDIR")
    assert len(fs) == report.written
    assert all(Path(instance.path).is_file() for instance in fs)
    assert len({i.StudyInstanceUID for i in fs}) == 2
    assert len({i.SeriesInstanceUID for i in fs}) == 2
    # FileSet non segue i collegamenti tra fratelli: li percorro io dalla radice
    dd = pydicom.dcmread(out / "DICOMDIR")
    by_offset = {r.seq_item_tell: r for r in dd.DirectoryRecordSequence}
    seen = []

    def walk(offset):
        chain = []
        while offset:
            assert offset in by_offset and offset not in seen, offset
            seen.append(offset)
            chain.append(offset)
            if by_offset[offset].OffsetOfReferencedLowerLevelDirectoryEntity:
                walk(by_offset[offset].OffsetOfReferencedLowerLevelDirectoryEntity)
            offset = by_offset[offset].OffsetOfTheNextDirectoryRecord
        return chain

    root = walk(dd.OffsetOfTheFirstDirectoryRecordOfTheRootDirectoryEntity)
    assert root[-1] == dd.OffsetOfTheLastDirectoryRecordOfTheRootDirectoryEntity
    assert len(seen) == len(by_offset)  # nessun record irraggiungibile


def test_implicit_vr_reencoded(tmp: Path):
    src = tmp / "src"
    src.mkdir()
    shutil.copy(MR_IMPLICIT, src / "mr")
    original = pydicom.dcmread(MR_IMPLICIT)
    assert original.file_meta.TransferSyntaxUID == ImplicitVRLittleEndian
    series, *_ = scanner.scan(src)
    out = tmp / "out"
    assert an.anonymize(series, out, "CASO_01", False).written == 1
    (res,) = outputs(out)
    assert res.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert not res.original_encoding[0]  # VR esplicito
    assert res.PixelData == original.PixelData
    (instance,) = FileSet(out / "DICOMDIR")
    assert instance.ReferencedTransferSyntaxUIDInFile == ExplicitVRLittleEndian


def test_implicit_vr_ambiguous_fallback(tmp: Path):
    src = tmp / "src"
    src.mkdir()
    ds = pydicom.dcmread(MR_IMPLICIT)
    ds.add_new(0x00281100, "OW", b"\x00\x01\x00\x00\x10\x00")  # GrayLookupTableDescriptor: VR ambigua
    ds.save_as(src / "mr")
    series, *_ = scanner.scan(src)
    out = tmp / "out"
    report = an.anonymize(series, out, "CASO_01", False)
    assert report.written == 1 and not report.skipped, report
    (res,) = outputs(out)
    assert res.file_meta.TransferSyntaxUID == ImplicitVRLittleEndian  # ripiego: resta Implicit VR
    (instance,) = FileSet(out / "DICOMDIR")
    assert instance.ReferencedTransferSyntaxUIDInFile == ImplicitVRLittleEndian


def test_skip_message_first_line(tmp: Path):
    series, *_ = scanner.scan(make_source(tmp / "src"))
    original = Dataset.save_as

    def failing(self, filename, *args, **kwargs):
        raise RuntimeError("prima riga\nTraceback /Users/x/segreto.py")

    Dataset.save_as = failing
    try:
        report = an.anonymize(series, tmp / "out", "CASO_01", False)
    finally:
        Dataset.save_as = original
    assert report.skipped and all("\n" not in r and "Traceback" not in r for r in report.skipped), report


def test_sr_person_name_dummy():
    ds = pydicom.dcmread(CT)
    item = Dataset()
    item.ValueType = "PNAME"
    item.PersonName = "ROSSI^MARIO"
    ds.ContentSequence = [item]
    observer = Dataset()
    observer.VerifyingObserverName = "VERDI^LUCA"
    ds.VerifyingObserverSequence = [observer]
    ds.OperatorsName = "BIANCHI^ANNA"
    ds.PhysiciansOfRecord = "NERI^PAOLO"
    an.anonymize_dataset(ds, "CASO_01", False, "salt")
    assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
    names = {e.keyword: str(e.value) for e in ds.iterall() if e.VR == "PN" and e.value}  # i PN vuoti (Type 2) vanno bene
    assert names == {
        "PersonName": "ANONIMO",
        "VerifyingObserverName": "ANONIMO",
        "PatientName": "CASO_01",
    }, names


def test_write_error_aborts(tmp: Path):
    series, *_ = scanner.scan(make_source(tmp / "src"))
    original = Dataset.save_as
    calls = []

    def failing(self, filename, *args, **kwargs):
        calls.append(filename)
        if len(calls) == 1:
            Path(filename).write_bytes(b"parziale")  # file scritto a metà
            raise exc
        return original(self, filename, *args, **kwargs)

    Dataset.save_as = failing
    try:
        exc = OSError("disco pieno")
        out = tmp / "out1"
        try:
            an.anonymize(series, out, "CASO_01", False)
            raise AssertionError("OSError atteso")
        except OSError:
            pass
        assert not list(out.rglob("IM*")), "file parziale rimasto"
        calls.clear()
        exc = RuntimeError("scrittura fallita")  # altri errori: file saltato, il resto prosegue
        out = tmp / "out2"
        report = an.anonymize(series, out, "CASO_01", False)
        assert report.written == 2 and len(report.skipped) == 1, report
        assert len(list(out.rglob("IM*"))) == 2
    finally:
        Dataset.save_as = original


def test_uid_salt_per_run(tmp: Path):
    series, *_ = scanner.scan(make_source(tmp / "src"))
    uids = []
    for n in (1, 2):
        an.anonymize(series, tmp / f"out{n}", "CASO_01", False)
        uids.append(outputs(tmp / f"out{n}")[0].StudyInstanceUID)
    assert uids[0] != uids[1]  # UID non ricostruibili dall'originale tra una sessione e l'altra


def test_out_root_not_empty(tmp: Path):
    series, *_ = scanner.scan(make_source(tmp / "src"))
    out = tmp / "out"
    out.mkdir()
    (out / "vecchio.txt").write_text("x")
    try:
        an.anonymize(series, out, "CASO_01", False)
        raise AssertionError("ValueError atteso")
    except ValueError:
        pass
    assert [p.name for p in out.iterdir()] == ["vecchio.txt"]


def test_swapped_disc_skipped(tmp: Path):
    series, *_ = scanner.scan(make_source(tmp / "src"))
    for path in (tmp / "src").glob("*.dcm"):  # altro paziente sugli stessi percorsi
        ds = pydicom.dcmread(path)
        ds.PatientName, ds.PatientID = "BIANCHI^ANNA", "PID999"
        ds.save_as(path)
    report = an.anonymize(series, tmp / "out", "CASO_01", False)
    assert report.written == 0 and len(report.skipped) == 3, report
    assert all("il disco è cambiato: rileggi il CD" in e for e in report.skipped), report.skipped
    assert {"BIANCHI", "ANNA", "PID999"} <= report.identifiers, report.identifiers
    # solo il nome diverso (stesso ID) è già un altro disco
    series, *_ = scanner.scan(make_source(tmp / "src2"))
    for path in (tmp / "src2").glob("*.dcm"):
        ds = pydicom.dcmread(path)
        ds.PatientName = "BIANCHI^ANNA"
        ds.save_as(path)
    assert an.anonymize(series, tmp / "out2", "CASO_01", False).written == 0


def test_name_format_difference_not_swapped(tmp: Path):
    series, *_ = scanner.scan(make_source(tmp / "src"))
    for path in (tmp / "src").glob("*.dcm"):
        ds = pydicom.dcmread(path)
        ds.PatientName = "rossi^mario^^^ "
        ds.save_as(path)
    assert an.anonymize(series, tmp / "out", "CASO_01", False).written == 3


def test_report_identifiers_collected(tmp: Path):
    _, _, report = run(tmp)
    assert {"ROSSI", "MARIO", "PID12345", "ACC998877"} <= report.identifiers, report.identifiers


def test_check_leaks_extra_identifiers(tmp: Path):
    out = tmp / "out"
    out.mkdir()
    ds = pydicom.dcmread(CT)
    ds.StudyDescription = "TC di BIANCHI"
    ds.save_as(out / "IM000001")
    assert an.check_leaks(out, _fake_series("ROSSI")) == []
    leaks = an.check_leaks(out, _fake_series("ROSSI"), extra={"BIANCHI"})
    assert len(leaks) == 1 and leaks[0].startswith("StudyDescription"), leaks


def test_check_leaks_ignores_finder_files(tmp: Path):
    out = tmp / "out"
    out.mkdir()
    pydicom.dcmread(CT).save_as(out / "IM000001")
    (out / "._IM000000").write_bytes(b"\x00\x05\x16\x07" + b"junk" * 10)
    (out / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    assert an.check_leaks(out, _fake_series()) == []
    (out / "LEGGIMI.txt").write_text("viewer")
    (out / "._altro").write_bytes(b"non appledouble")
    leaks = an.check_leaks(out, _fake_series())
    assert sorted(leaks) == ["._altro: file non DICOM nell'esame", "LEGGIMI.txt: file non DICOM nell'esame"], leaks


def test_truncated_image_skipped(tmp: Path):
    src = make_source(tmp / "src")
    ds = pydicom.dcmread(src / "img1.dcm")
    ds.PixelData = ds.PixelData[:-200]
    ds.save_as(src / "img1.dcm")
    series, *_ = scanner.scan(src)
    report = an.anonymize(series, tmp / "out", "CASO_01", False)
    assert report.written == 2, report
    assert len(report.skipped) == 1 and "img1.dcm: file incompleto (immagine troncata)" in report.skipped[0], report.skipped


def test_ybr_422_uncompressed_not_truncated(tmp: Path):
    src = tmp / "src"
    src.mkdir()
    ds = pydicom.dcmread(get_testdata_file("SC_ybr_full_422_uncompressed.dcm"))
    ds.PatientName, ds.PatientID = "ROSSI^MARIO", "PID12345"
    ds.save_as(src / "ybr.dcm")
    series, *_ = scanner.scan(src)
    report = an.anonymize(series, tmp / "out", "CASO_01", False)
    assert report.written == 1 and report.skipped == [], report
```

- [ ] **Step 2: Verifica che fallisca**

Run: `.venv/bin/python test_anon.py`
Expected: `ModuleNotFoundError: No module named 'anonymizer'`

- [ ] **Step 3: Crea `anonymizer.py`**

```python
"""Anonimizza serie DICOM e scrive una cartella con DICOMDIR pronta per il disco."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from itertools import chain
from pathlib import Path

import pydicom
from pydicom.datadict import tag_for_keyword
from pydicom.dataset import Dataset, FileMetaDataset
# ponytail: _four_level_record_type è privata; pydicom è fissato a 3.0.2 in requirements.txt
from pydicom.fileset import DIRECTORY_RECORDERS, _four_level_record_type
from pydicom.multival import MultiValue
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    MediaStorageDirectoryStorage,
    generate_uid,
)

from scanner import Series, identifiers, patient_key

# PS3.15 Table E.1-1 (Basic Profile): attributi X/Z da togliere, più StorageMediaFileSetID (etichetta del CD
# originale), RetrieveURL e RetrieveURI. Restano di proposito le sequenze strutturali i cui UID sono rimappati
# (ReferencedImageSequence, SourceImageSequence, ContentSequence, AcquisitionContextSequence) e le descrizioni
# cliniche (StudyDescription, SeriesDescription, ROIName: scelta di progetto, le controlla check_leaks).
# Nomi di persona (VR PN), AE title, UID, date e orari sono gestiti per tipo di dato in anonymize_dataset.
REMOVE_KEYWORDS = """
AccessionNumber AcquisitionComments AcquisitionContextDescription AcquisitionDeviceProcessingDescription
AcquisitionProtocolDescription ActualHumanPerformersSequence AdditionalPatientHistory AddressTrial AdmissionID
AdmittingDiagnosesCodeSequence AdmittingDiagnosesDescription Allergies AnnotationGroupDescription Arbitrary
AuthorObserverSequence BarcodeValue BeamDescription BolusDescription BranchOfService CameraOwnerName CassetteID
CertifiedTimestamp ChannelDerivationDescription ChannelLabel ClinicalTrialCoordinatingCenterName
ClinicalTrialProtocolEthicsCommitteeApprovalNumber ClinicalTrialProtocolName ClinicalTrialSeriesDescription
ClinicalTrialSeriesID ClinicalTrialSiteID ClinicalTrialSiteName ClinicalTrialSubjectID
ClinicalTrialSubjectReadingID ClinicalTrialTimePointDescription ClinicalTrialTimePointID CommentsOnRadiationDose
CommentsOnThePerformedProcedureStep CompensatorDescription ConceptualVolumeCombinationDescription
ConceptualVolumeDescription ConfidentialityConstraintOnPatientDataDescription
ConsultingPhysicianIdentificationSequence ContainerComponentID ContainerDescription ContainerIdentifier
ContentCreatorIdentificationCodeSequence ContributionDescription CountryOfResidence CurrentPatientLocation
CustodialOrganizationSequence DataSetTrailingPadding DecompositionDescription DerivationDescription DetectorID
DeviceAlternateIdentifier DeviceDescription DeviceSerialNumber DeviceSettingDescription
DigitalSignaturesSequence DischargeDiagnosisDescription DisplacementReferenceLabel DistributionAddress
DoseReferenceDescription EntityDescription EntityName EquipmentFrameOfReferenceDescription EthnicGroup
EthnicGroupCodeSequence FillerOrderNumberImagingServiceRequest FilterLookupTableDescription
FixationDeviceDescription FractionGroupDescription FractionationNotes FrameComments GPSAltitude GPSAltitudeRef
GPSAreaInformation GPSDOP GPSDestBearing GPSDestBearingRef GPSDestDistance GPSDestDistanceRef GPSDestLatitude
GPSDestLatitudeRef GPSDestLongitude GPSDestLongitudeRef GPSDifferential GPSImgDirection GPSImgDirectionRef
GPSLatitude GPSLatitudeRef GPSLongitude GPSLongitudeRef GPSMapDatum GPSMeasureMode GPSProcessingMethod
GPSSatellites GPSSpeed GPSSpeedRef GPSStatus GPSTrack GPSTrackRef GPSVersionID GantryID GeneratorID
HardcopyCreationDeviceID HumanPerformerOrganization IconImageSequence IdentifyingComments ImageComments
ImagePresentationComments ImagingServiceRequestComments Impressions InstanceOriginStatus InstitutionAddress
InstitutionCodeSequence InstitutionName InstitutionalDepartmentName InstitutionalDepartmentTypeCodeSequence
InsurancePlanIdentification IntendedRecipientsOfResultsIdentificationSequence InterpretationApproverSequence
InterpretationDiagnosisDescription InterpretationID InterpretationIDIssuer InterpretationText
IssuerOfAdmissionID IssuerOfAdmissionIDSequence IssuerOfClinicalTrialProtocolID IssuerOfClinicalTrialSeriesID
IssuerOfClinicalTrialSiteID IssuerOfClinicalTrialSubjectID IssuerOfClinicalTrialSubjectReadingID
IssuerOfClinicalTrialTimePointID IssuerOfPatientID IssuerOfPatientIDQualifiersSequence IssuerOfServiceEpisodeID
IssuerOfServiceEpisodeIDSequence IssuerOfTheContainerIdentifierSequence IssuerOfTheSpecimenIdentifierSequence
LabelText LensMake LensModel LensSerialNumber LensSpecification LongDeviceDescription MAC MakerNote
ManufacturerDeviceIdentifier MedicalAlerts MedicalRecordLocator MilitaryRank ModifiedAttributesSequence
ModifiedImageDescription ModifyingDeviceID MultienergyAcquisitionDescription MultiplexGroupLabel
NonconformingDataElementValue NonconformingModifiedAttributesSequence Occupation OperatorIdentificationSequence
OrderCallbackPhoneNumber OrderCallbackTelecomInformation OrderEntererLocation OriginalAttributesSequence
OtherClinicalTrialProtocolIDsSequence OtherPatientIDs OtherPatientIDsSequence ParticipantSequence PatientAddress
PatientBirthDate PatientBirthTime PatientComments PatientInstitutionResidence PatientInsurancePlanCodeSequence
PatientPrimaryLanguageCodeSequence PatientPrimaryLanguageModifierCodeSequence PatientReligiousPreference
PatientSetupPhotoDescription PatientSexNeutered PatientState PatientTelecomInformation PatientTelephoneNumbers
PatientTransportArrangements PatientTreatmentPreparationMethodDescription
PatientTreatmentPreparationProcedureParameterDescription PerformedLocation PerformedProcedureStepDescription
PerformedProcedureStepID PerformedStationAETitle PerformedStationGeographicLocationCodeSequence
PerformedStationName PerformedStationNameCodeSequence PerformingPhysicianIdentificationSequence PersonAddress
PersonIdentificationCodeSequence PersonTelecomInformation PersonTelephoneNumbers
PhysiciansOfRecordIdentificationSequence PhysiciansReadingStudyIdentificationSequence
PlacerOrderNumberImagingServiceRequest PlateID PositionAcquisitionTemplateDescription
PositionAcquisitionTemplateName PreMedication PrescriptionDescription PrescriptionNotes
PrescriptionNotesSequence PriorTreatmentDoseDescription PyramidDescription PyramidLabel ROICreatorSequence
ROIDescription ROIGenerationDescription ROIInterpreterSequence ROIObservationDescription ROIObservationLabel
RTAccessoryDeviceSlotID RTAccessoryHolderSlotID RTPhysicianIntentNarrative RTPlanDescription RTPlanName
RTTreatmentApproachLabel RadiationGenerationModeDescription ReasonForOmissionDescription
ReasonForRequestedProcedureCodeSequence ReasonForStudy ReasonForSuperseding ReasonForTheImagingServiceRequest
ReasonForTheRequestedProcedure ReasonForVisit ReasonForVisitCodeSequence ReferencedDigitalSignatureSequence
ReferencedPatientAliasSequence ReferencedPatientPhotoSequence ReferencedPatientSequence
ReferencedPerformedProcedureStepSequence ReferencedSOPInstanceMACSequence ReferencedStudySequence
ReferringPhysicianAddress ReferringPhysicianIdentificationSequence ReferringPhysicianTelephoneNumbers
RegionOfResidence RequestAttributesSequence RequestedContrastAgent RequestedProcedureComments
RequestedProcedureDescription RequestedProcedureID RequestedProcedureLocation RequestedSeriesDescription
RequestingPhysicianIdentificationSequence RequestingService RespiratoryMotionCompensationTechniqueDescription
ResponsibleOrganization ResultsComments ResultsDistributionListSequence ResultsID ResultsIDIssuer RetrieveURI
RetrieveURL ScheduledHumanPerformersSequence ScheduledPatientInstitutionResidence
ScheduledPerformingPhysicianIdentificationSequence ScheduledProcedureStepDescription ScheduledProcedureStepID
ScheduledProcedureStepLocation ScheduledStationAETitle ScheduledStationGeographicLocationCodeSequence
ScheduledStationName ScheduledStationNameCodeSequence ScheduledStudyLocation ScheduledStudyLocationAETitle
SecondaryCaptureDeviceID ServiceEpisodeDescription ServiceEpisodeID SetupTechniqueDescription
ShieldingDeviceDescription SlideIdentifier SourceManufacturer SourceOfPreviousValues SourceSerialNumber
SpecialNeeds SpecimenAccessionNumber SpecimenDetailedDescription SpecimenIdentifier SpecimenPreparationSequence
SpecimenShortDescription StationName StorageMediaFileSetID StructureSetDescription StructureSetName
StudyComments StudyIDIssuer TelephoneNumberTrial TextComments TextString TimezoneOffsetFromUTC TopicAuthor
TopicKeywords TopicSubject TopicTitle TransducerIdentificationSequence TreatmentMachineName TreatmentSite
TreatmentSites TreatmentTechniqueNotes UDISequence UniqueDeviceIdentifier
VerbalSourceIdentifierCodeSequenceTrial VerifyingObserverIdentificationCodeSequence VerifyingOrganization
VisitComments WaveformAnnotationSequence WaveformFilterDescription XRayDetectorLabel
""".split()
CLINICAL_KEYWORDS = "PatientAge PatientSex PatientSize PatientWeight PregnancyStatus SmokingStatus".split()
META_REMOVE = (
    "SourceApplicationEntityTitle",
    "SendingApplicationEntityTitle",
    "ReceivingApplicationEntityTitle",
    "PrivateInformationCreatorUID",
    "PrivateInformation",
    "SourcePresentationAddress",
    "SendingPresentationAddress",
    "ReceivingPresentationAddress",
)
REMOVE = {tag_for_keyword(k) for k in REMOVE_KEYWORDS}
CLINICAL = {tag_for_keyword(k) for k in CLINICAL_KEYWORDS}
DUMMY = {"DA": "19000101", "TM": "000000", "DT": "19000101000000"}
DICOM_ROOT = "1.2.840.10008."  # UID definiti dallo standard: non vanno mai cambiati
ENCAPSULATED_DOC = "1.2.840.10008.5.1.4.1.1.104"
METHOD_BASIC = "Basic Profile modified: descriptions kept"
METHOD_KEEP = "Basic Profile modified: descriptions, dates, patient chars kept"
NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9 _-]{0,63}")
KEEP_PN = {"PersonName", "VerifyingObserverName"}  # Type 1 nei report strutturali: valore fittizio, non cancellati
PREFIXES = ("PT", "ST", "SE", "IM")


@dataclass
class Report:
    written: int = 0
    skipped: list[str] = field(default_factory=list)  # "file: motivo"
    identifiers: set[str] = field(default_factory=set)  # dati originali di ogni file letto, per check_leaks


def auto_name() -> str:
    return f"ANON-{date.today():%Y%m%d}-{secrets.token_hex(2).upper()}"


def disc_label(name: str) -> str:
    """Etichetta del disco: maiuscolo, solo A-Z 0-9 _, max 16 caratteri (limite Joliet)."""
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())[:16]


def _remap(value, salt: str):
    if isinstance(value, (list, MultiValue)):
        return [_remap(v, salt) for v in value]
    value = str(value)
    if not value or value.startswith(DICOM_ROOT):
        return value
    # stesso UID originale -> stesso nuovo UID in tutta la sessione
    return generate_uid(entropy_srcs=[salt, value])


def _why(exc: Exception) -> str:
    """Solo la prima riga del messaggio: niente traceback né percorsi locali."""
    return (str(exc).splitlines() or [type(exc).__name__])[0]


def anonymize_dataset(ds: Dataset, new_name: str, keep_clinical: bool, salt: str) -> None:
    """Applica le regole della spec a un dataset (in place, sequenze comprese)."""
    ds.remove_private_tags()

    def rule(dataset: Dataset, elem) -> None:
        tag = elem.tag
        if elem.VR == "PN" and elem.keyword in KEEP_PN:
            elem.value = "ANONIMO"
        elif tag in REMOVE or elem.VR in ("PN", "AE") or (tag.group & 0xFF00) in (0x5000, 0x6000):
            del dataset[tag]
        elif not keep_clinical and tag in CLINICAL:
            del dataset[tag]
        elif elem.VR == "UI":
            if not elem.keyword.endswith("ClassUID") and elem.keyword not in ("TransferSyntaxUID", "CodingSchemeUID"):
                elem.value = _remap(elem.value, salt)
        elif not keep_clinical and elem.VR in DUMMY and elem.value:
            elem.value = DUMMY[elem.VR]

    ds.walk(rule)
    ds.PatientName = new_name
    ds.PatientID = new_name
    ds.StudyID = "1"
    if not ds.get("StudyDate"):
        ds.StudyDate = DUMMY["DA"]
    if not ds.get("StudyTime"):
        ds.StudyTime = DUMMY["TM"]
    # attributi Type 2: presenti ma vuoti, così anche i PACS più rigidi accettano i file
    for keyword in ("ReferringPhysicianName", "AccessionNumber", "PatientBirthDate"):
        setattr(ds, keyword, "")
    if not keep_clinical or "PatientSex" not in ds:
        ds.PatientSex = ""
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = METHOD_KEEP if keep_clinical else METHOD_BASIC
    meta = ds.file_meta
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    for keyword in META_REMOVE:
        if keyword in meta:
            delattr(meta, keyword)
    # Explicit VR (senza perdita): i profili media DICOM lo richiedono
    if meta.TransferSyntaxUID == ImplicitVRLittleEndian:
        meta.TransferSyntaxUID = ExplicitVRLittleEndian


def _records(ds: Dataset) -> list[Dataset]:
    """Record DICOMDIR PATIENT, STUDY, SERIES e foglia per un'istanza."""
    records = []
    for record_type in ("PATIENT", "STUDY", "SERIES", _four_level_record_type(ds)):
        record = DIRECTORY_RECORDERS[record_type](ds)
        record.OffsetOfTheNextDirectoryRecord = 0
        record.RecordInUseFlag = 0xFFFF
        record.OffsetOfReferencedLowerLevelDirectoryEntity = 0
        record.DirectoryRecordType = record_type
        if "SpecificCharacterSet" in ds:
            record.SpecificCharacterSet = ds.SpecificCharacterSet
        records.append(record)
    leaf = records[-1]
    leaf.ReferencedSOPClassUIDInFile = ds.SOPClassUID
    leaf.ReferencedSOPInstanceUIDInFile = ds.SOPInstanceUID
    leaf.ReferencedTransferSyntaxUIDInFile = ds.file_meta.TransferSyntaxUID
    return records


def _keys(ds: Dataset) -> tuple[str, str, str, str]:
    return str(ds.PatientID), str(ds.StudyInstanceUID), str(ds.SeriesInstanceUID), str(ds.SOPInstanceUID)


def _file_id(tree: dict, keys) -> list[str]:
    """Percorso PTxxxxxx/STxxxxxx/SExxxxxx/IMxxxxxx (ISO 9660), senza modificare l'albero."""
    level, parts = tree, []
    for prefix, key in zip(PREFIXES, keys):
        node = level.get(key)
        parts.append(f"{prefix}{node[2] if node else len(level):06d}")
        level = node[1] if node else {}
    return parts


def _insert(tree: dict, keys, records) -> None:
    level = tree
    for key, record in zip(keys, records):
        if key not in level:
            level[key] = (record, {}, len(level))  # (record, figli, indice)
        level = level[key][1]


def write_dicomdir(root: Path, tree: dict, fileset_id: str) -> None:
    """Scrive root/DICOMDIR in tempo lineare."""
    flat: list[Dataset] = []
    links: list[list] = []  # per record: [indice del successivo, indice del primo figlio]

    def walk(level: dict) -> list[int]:
        indexes = []
        for record, children, _ in level.values():
            i = len(flat)
            flat.append(record)
            links.append([None, None])
            kids = walk(children)
            links[i][1] = kids[0] if kids else None
            indexes.append(i)
        for a, b in zip(indexes, indexes[1:]):
            links[a][0] = b
        return indexes

    top = walk(tree)
    ds = Dataset()
    ds.FileSetID = fileset_id
    ds.OffsetOfTheFirstDirectoryRecordOfTheRootDirectoryEntity = 0
    ds.OffsetOfTheLastDirectoryRecordOfTheRootDirectoryEntity = 0
    ds.FileSetConsistencyFlag = 0
    ds.DirectoryRecordSequence = flat
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = MediaStorageDirectoryStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    path = Path(root) / "DICOMDIR"
    # ponytail: due passaggi. Gli offset sono UL a lunghezza fissa, quindi il secondo
    # file ha la stessa struttura del primo e le posizioni lette restano valide.
    ds.save_as(path, enforce_file_format=True)
    tells = [item.seq_item_tell for item in pydicom.dcmread(path).DirectoryRecordSequence]
    for record, (next_i, child_i) in zip(flat, links):
        record.OffsetOfTheNextDirectoryRecord = 0 if next_i is None else tells[next_i]
        record.OffsetOfReferencedLowerLevelDirectoryEntity = 0 if child_i is None else tells[child_i]
    ds.OffsetOfTheFirstDirectoryRecordOfTheRootDirectoryEntity = tells[top[0]]
    ds.OffsetOfTheLastDirectoryRecordOfTheRootDirectoryEntity = tells[top[-1]]
    ds.save_as(path, enforce_file_format=True)


def _check_complete(ds: Dataset) -> None:
    """Immagini non compresse: i pixel devono coprire Rows x Columns x campioni x frame."""
    if "PixelData" not in ds or ds.file_meta.TransferSyntaxUID.is_compressed:
        return
    try:
        samples = 2 if ds.get("PhotometricInterpretation") == "YBR_FULL_422" else int(ds.get("SamplesPerPixel") or 1)
        bits = int(ds.Rows) * int(ds.Columns) * samples * int(ds.BitsAllocated) * int(ds.get("NumberOfFrames") or 1)
    except (AttributeError, ValueError):
        return  # attributi mancanti: lo decidono gli altri controlli
    if len(ds.PixelData) < -(-bits // 8):
        raise ValueError("file incompleto (immagine troncata)")


def anonymize(
    series: list[Series],
    out_root: Path,
    new_name: str,
    keep_clinical: bool,
    progress=lambda done, total, text: None,
) -> Report:
    """Anonimizza i file delle serie in out_root/PT…/ST…/SE…/IM… e scrive il DICOMDIR."""
    if not NAME_RE.fullmatch(new_name):
        raise ValueError("Nome non valido: usa lettere, numeri, spazio, _ o - (max 64 caratteri).")
    out_root = Path(out_root)
    if out_root.exists() and any(out_root.iterdir()):
        raise ValueError(f"La cartella {out_root} esiste già e non è vuota: sceglierne un'altra.")
    salt = secrets.token_hex(16)
    out_root.mkdir(parents=True, exist_ok=True)
    tree: dict = {}
    report = Report()
    jobs = [(si, ii, path) for si, s in enumerate(series) for ii, path in enumerate(s.files)]
    # ponytail: un solo processo. Da CD il collo di bottiglia è la lettura del disco;
    # passare a ProcessPoolExecutor solo se da cartella locale risulta lento.
    for done, (si, ii, path) in enumerate(jobs, 1):
        progress(done, len(jobs), "Anonimizzazione")
        try:
            ds = pydicom.dcmread(path)
            report.identifiers |= identifiers(ds)
            s = series[si]
            if patient_key(str(ds.get("PatientName", "")), str(ds.get("PatientID", ""))) != patient_key(
                s.patient_name, s.patient_id
            ):
                raise ValueError("il disco è cambiato: rileggi il CD")
            implicit = ds.file_meta.TransferSyntaxUID == ImplicitVRLittleEndian
            if str(ds.get("SOPClassUID", "")).startswith(ENCAPSULATED_DOC):
                raise ValueError("documento PDF/CDA, non anonimizzabile")
            _check_complete(ds)
            anonymize_dataset(ds, new_name, keep_clinical, salt)
            if ds.get("SeriesNumber") in (None, ""):
                ds.SeriesNumber = si + 1
            if ds.get("InstanceNumber") in (None, ""):
                ds.InstanceNumber = ii + 1
            if not ds.get("Modality"):
                ds.Modality = "OT"  # serve al record SERIES del DICOMDIR
            records = _records(ds)
            keys = _keys(ds)
            file_id = _file_id(tree, keys)
            records[-1].ReferencedFileID = file_id
            dest = out_root.joinpath(*file_id)
            dest.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            report.skipped.append(f"{path.name}: {_why(exc)}")
            continue
        try:
            try:
                ds.save_as(dest, enforce_file_format=True)
            except ValueError:
                if ds.file_meta.TransferSyntaxUID != ExplicitVRLittleEndian or not implicit:
                    raise
                # tag ritirati con VR ambigua: in Explicit VR non si scrivono, si resta in Implicit VR
                ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
                records[-1].ReferencedTransferSyntaxUIDInFile = ImplicitVRLittleEndian
                ds.save_as(dest, enforce_file_format=True)
        except OSError:
            dest.unlink(missing_ok=True)  # disco pieno o non scrivibile: inutile proseguire
            raise
        except Exception as exc:
            dest.unlink(missing_ok=True)
            report.skipped.append(f"{path.name}: {_why(exc)}")
            continue
        _insert(tree, keys, records)
        report.written += 1
    if report.written:
        write_dicomdir(out_root, tree, disc_label(new_name))
    return report


def _pattern(ids: set[str]) -> re.Pattern | None:
    """Regex che trova gli identificativi come token: solo cifre -> non attaccato ad altre cifre
    (così ACC26004417 e PZ48213 si trovano, ma non IM048213 né dentro un UID); con lettere -> non
    attaccato ad altre lettere (ROSSI2 sì, ROSSINI no). Limite noto: nome e cognome incollati
    (ROSSIMARIO) non si trovano."""
    if not ids:
        return None
    alternatives = []
    for i in sorted(ids, key=len, reverse=True):
        edge = "0-9" if i.isdigit() else "A-Z"
        alternatives.append(rf"(?<![{edge}])({re.escape(i)})(?![{edge}])")
    return re.compile("|".join(alternatives))


def _texts(ds: Dataset):
    """(campo, valore in maiuscolo) per ogni tag, sequenze comprese, pixel e UID esclusi."""
    for elem in chain(ds.file_meta.iterall(), ds.iterall()):
        # gli UID sono rigenerati o standard: non portano dati del paziente
        if elem.VR in ("SQ", "UI") or elem.tag.group == 0x7FE0:
            continue
        value = elem.value
        text = value.decode("latin-1") if isinstance(value, bytes) else str(value)
        yield elem.keyword or str(elem.tag), text.upper()


def _is_finder_junk(path: Path) -> bool:
    """.DS_Store e i file AppleDouble (._*) che macOS crea sulle chiavette."""
    if path.name == ".DS_Store":
        return True
    if not path.name.startswith("._"):
        return False
    with open(path, "rb") as f:
        return f.read(4) == b"\x00\x05\x16\x07"


def check_leaks(out_root: Path, series: list[Series], extra=frozenset()) -> list[str]:
    """Campi dell'esame anonimo che contengono ancora dati originali, una riga per campo e dato, più i file non DICOM. Vuoto = ok.
    extra: identificativi letti dai file al momento dell'anonimizzazione (Report.identifiers)."""
    pattern = _pattern(set().union(extra, *(s.identifiers for s in series)))
    out_root = Path(out_root)
    found: dict[tuple[str, str], str] = {}  # (campo, dato originale) -> esempio di valore
    problems: list[str] = []
    for path in sorted(p for p in out_root.rglob("*") if p.is_file()):
        if _is_finder_junk(path):
            continue
        rel = path.relative_to(out_root).as_posix()
        if pattern:
            for match in pattern.finditer(rel.upper()):
                found.setdefault(("nome del file", match.group(match.lastindex)), rel)
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception:
            problems.append(f"{rel}: file non DICOM nell'esame")
            continue
        for where, text in _texts(ds) if pattern else ():
            for match in pattern.finditer(text):
                found.setdefault((where, match.group(match.lastindex)), text[:60])
    return [f"{where} = «{example}» contiene «{i}»" for (where, i), example in found.items()] + problems
```

- [ ] **Step 4: Verifica che passi**

Run: `.venv/bin/python test_anon.py`
Expected: 33 righe `ok  test_…` e `TUTTI I TEST PASSATI`

- [ ] **Step 5: Verifica che i test intercettino un errore vero**

Togli temporaneamente ` or elem.VR == "PN"` dalla funzione `rule` in `anonymizer.py`.
Run: `.venv/bin/python test_anon.py`
Expected: `AssertionError` in `test_anonymize_removes_identifiers`. Poi ripristina la riga e rilancia: tutto verde.

- [ ] **Step 6: Benchmark su 5000 immagini**

Run:
```bash
.venv/bin/python - <<'EOF'
import time, tempfile
from pathlib import Path
import pydicom
from pydicom.data import get_testdata_file
import scanner, anonymizer as an
tmp = Path(tempfile.mkdtemp()); src = tmp / "src"; src.mkdir()
ds = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
for s in range(5):
    for i in range(1000):
        ds.SeriesInstanceUID = f"1.2.3.{s}"; ds.SeriesNumber = s + 1; ds.InstanceNumber = i + 1
        ds.SOPInstanceUID = f"1.2.3.{s}.{i}"; ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.save_as(src / f"f{s}_{i}")
t = time.time(); series, *_ = scanner.scan(src)
report = an.anonymize(series, tmp / "out" / "X", "CASO_01", False)
leaks = an.check_leaks(tmp / "out" / "X", series)
print(report.written, "immagini,", len(leaks), "fughe,", round(time.time() - t), "s")
EOF
```
Expected: `5000 immagini, 0 fughe, ~22 s` (accettabile fino a 60 s)

---

### Task 3: Masterizzatore

**Files:**
- Modify: `test_anon.py`
- Create: `burner.py`

- [ ] **Step 1: Aggiungi i test dei parser**

In `test_anon.py`, sostituisci di nuovo tutto ciò che sta tra la docstring iniziale e `def make_source` con:

```python
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pydicom
from pydicom.data import get_testdata_file
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.datadict import tag_for_keyword
from pydicom.fileset import FileSet
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian

import anonymizer as an
import burner
import scanner

CT = get_testdata_file("CT_small.dcm")
MR = get_testdata_file("MR_small.dcm")
MR_IMPLICIT = get_testdata_file("MR_small_implicit.dcm")
SC = get_testdata_file("SC_rgb_small_odd.dcm")
ORIGINAL = ["ROSSI", "MARIO", "PID12345", "ACC998877", "19600312", "VERDI", "OSPEDALE TEST"]
```

Poi inserisci prima di `if __name__ == "__main__":`:

```python
def test_burner_helpers(tmp: Path):
    drives = burner._parse_drutil_list(
        "   Vendor   Product           Rev   Bus       SupportLevel\n"
        "1  HL-DT-ST DVDRW  GX50N      RP09  USB       Unsupported\n"
    )
    assert [d.id for d in drives] == ["1"]
    blank = (
        "     Space Free:   79:59:74         blocks:   359849 / 736.97MB / 702.81MiB\n"
        "    Writability: appendable, blank, overwritable\n"
    )
    assert burner._parse_drutil_free(blank) == 359849 * 2048
    assert burner._parse_drutil_free(blank.replace("blank, ", "")) is None
    assert burner._parse_drutil_free("Type: No Media Inserted\n") is None
    (tmp / "a").write_bytes(b"x" * 3000)
    assert burner.needed_bytes(tmp) == (2 + 3) * 2048 + 2 * 1024 * 1024

def test_drutil_parsers_realistic():
    dvd = (
        "     Space Free:  510:01:16         blocks:  2295104 /   4.70GB /   4.38GiB\n"
        "    Writability: appendable, blank, overwritable\n"
    )
    assert burner._parse_drutil_free(dvd) == 2295104 * 2048
    two = (
        "   Vendor   Product           Rev   Bus       SupportLevel\n"
        "1  HL-DT-ST DVDRW  GX50N      RP09  USB       Unsupported\n"
        "2  MATSHITA DVD-R   UJ-868    HA13  ATAPI     Unsupported\n"
    )
    drives = burner._parse_drutil_list(two)
    assert [d.id for d in drives] == ["1", "2"]
    assert drives[1].name == "MATSHITA DVD-R UJ-868 HA13 ATAPI Unsupported"


def test_needed_bytes_vs_drutil(tmp: Path):
    if sys.platform != "darwin" or not shutil.which("drutil"):
        print("    (saltato: serve drutil su Mac)")
        return
    for i in range(300):
        (tmp / f"f{i}.dcm").write_bytes(b"x" * (100 + i))
    for i, size in enumerate([50_000, 700_000, 2048 * 100, 3_000_000]):
        (tmp / "sub").mkdir(exist_ok=True)
        (tmp / "sub" / f"big{i}.dcm").write_bytes(b"y" * size)
    out = subprocess.run(["drutil", "size", "-nohfsplus", "-nodrive", str(tmp)],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    match = re.search(r"Blocks needed:\s*(\d+)", out)
    assert match, out
    assert burner.needed_bytes(tmp) >= int(match.group(1)) * 2048, (out, burner.needed_bytes(tmp))


class _FakeError(Exception):
    def __init__(self, excepinfo=None, hresult=None):
        self.excepinfo, self.hresult = excepinfo, hresult


def test_win_error():
    signed = 0xC0AA0202 - (1 << 32)  # com_error restituisce HRESULT con segno
    assert burner._win_error(_FakeError((0, "src", "desc", None, 0, signed))) == "nessun disco"
    assert burner._win_error(_FakeError(hresult=0xC0AAB120)) == "esame troppo grande, usa un DVD"
    assert burner._win_error(_FakeError((0, "s", "Prima riga\nseconda", None, 0, 0x80004005))) == "Prima riga"
    assert burner._win_error(_FakeError((0, "s", None, None, 0, 0x80004005))) == "errore 0x80004005"
    assert burner._win_error(_FakeError(hresult=-2147467259)) == "errore 0x80004005"


class _FakeProc:
    pid = 4242

    def __init__(self, args, output="", returncode=0, hang=False):
        self.args, self.output, self.returncode, self.hang = args, output, returncode, hang
        self.killed = self.terminated = False

    def communicate(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.output, None

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True


def _mac_burn(output, returncode=0, hang=False, env=None):
    """Esegue burner.burn con drutil finto; restituisce (eccezione o None, processi creati)."""
    procs = []

    def popen(args, **kwargs):
        proc = _FakeProc(args, output, returncode, hang) if args[0] == "drutil" else _FakeProc(args)
        procs.append(proc)
        return proc

    saved = (subprocess.Popen, sys.platform, dict(os.environ))
    subprocess.Popen, sys.platform = popen, "darwin"
    os.environ.pop("BURN_TEST", None)
    os.environ.update(env or {})
    try:
        burner.burn(Path("/tmp/CASO_01"), burner.Drive("1", "finto"))
        error = None
    except burner.BurnError as exc:
        error = exc
    finally:
        subprocess.Popen, sys.platform = saved[:2]
        os.environ.clear()
        os.environ.update(saved[2])
    return error, procs


def test_mac_burn():
    error, procs = _mac_burn("Burning...\nBurn completed successfully\n")
    assert error is None
    assert procs[1].args[:3] == ["caffeinate", "-i", "-w"] and procs[1].terminated
    assert "-test" not in procs[0].args
    error, _ = _mac_burn("Burn failed: device busy /Users/mario/CASO_01\n", returncode=1)
    assert str(error) == "Burn failed: device busy", str(error)
    error, _ = _mac_burn("Nothing\n", returncode=3)
    assert str(error) == "drutil ha restituito 3"
    error, _ = _mac_burn("Done but silent\n")
    assert error is not None and "\n" not in str(error)
    error, _ = _mac_burn("Burning /tmp/fail_dir/CASO\nBurn completed successfully\n")
    assert error is None
    error, procs = _mac_burn("Burn completed\n", env={"BURN_TEST": "1"})
    assert error is None and "-test" in procs[0].args
    error, procs = _mac_burn("", hang=True)
    assert str(error) == "tempo scaduto: il masterizzatore non ha finito" and procs[0].killed


def test_compare_tree(tmp: Path):
    src, dst = tmp / "src", tmp / "dst"
    (src / "IMAGES").mkdir(parents=True)
    (src / "DICOMDIR").write_bytes(b"abc")
    (src / "IMAGES" / "IM1").write_bytes(b"0123456789")
    shutil.copytree(src, dst)
    assert burner._compare_tree(src, dst) is None
    (dst / "IMAGES" / "IM1").write_bytes(b"0123456788")
    assert "IM1" in burner._compare_tree(src, dst)
    (dst / "IMAGES" / "IM1").unlink()
    assert "IM1" in burner._compare_tree(src, dst)


def test_win_burn(tmp: Path):
    src = tmp / "CASO_ABCDEFGHIJKL"  # 17 caratteri, VolumeName ne accetta 15
    (src / "IMAGES").mkdir(parents=True)
    (src / "DICOMDIR").write_bytes(b"dir")
    (src / "IMAGES" / "IM1").write_bytes(b"0123456789")
    disc = tmp / "disc"
    disc.mkdir()
    state = {"corrupt": False, "eject_fails": False, "names": [], "ejected": False, "awake": []}

    class Recorder:
        VolumePathNames = (str(disc),)
        ProductId = " DVDRW "

        def InitializeDiscRecorder(self, drive_id):
            pass

        def EjectMedia(self):
            state["ejected"] = True
            if state["eject_fails"]:
                raise _FakeError(hresult=-1)

    class Fmt:
        def Write(self, stream):
            state["closed"] = self.ForceMediaToBeClosed
            shutil.copytree(state["tree"], disc, dirs_exist_ok=True)
            if state["corrupt"]:
                (disc / "IMAGES" / "IM1").write_bytes(b"XXXXXXXXXX")

    class FakeRoot:
        def AddTree(self, path, include_base):
            state["tree"] = path

    class Image:
        Root = FakeRoot()

        def ChooseImageDefaults(self, recorder):
            state["defaults"] = True

        def __setattr__(self, name, value):
            if name == "VolumeName":
                state["names"].append(value)
            if name == "FileSystemsToCreate":
                state["fs"] = value
            object.__setattr__(self, name, value)

        def CreateResultImage(self):
            return SimpleNamespace(ImageStream=None)

    objects = {"IMAPI2.MsftDiscRecorder2": Recorder, "IMAPI2.MsftDiscFormat2Data": Fmt,
               "IMAPI2FS.MsftFileSystemImage": Image}
    saved = (burner._com, burner._keep_awake, sys.platform, burner._VERIFY_TIMEOUT, burner._VERIFY_POLL)
    burner._com = lambda progid: objects[progid]()
    burner._keep_awake = state["awake"].append
    sys.platform = "win32"
    burner._VERIFY_TIMEOUT, burner._VERIFY_POLL = 0.2, 0.01
    try:
        drive = burner.Drive("x", "finto")
        burner.burn(src, drive)
        assert state["names"] == ["CASO_ABCDEFGHIJ"] and len(state["names"][0]) <= 15
        assert state["ejected"] and state["awake"] == [True, False]
        assert state["closed"] is True and state["defaults"] and state["fs"] == 7
        state["eject_fails"] = True  # espulsione fallita dopo la scrittura: non è un errore
        shutil.rmtree(disc)
        disc.mkdir()
        burner.burn(src, drive)
        state["corrupt"] = True  # file diverso sul disco: la verifica deve fallire
        shutil.rmtree(disc)
        disc.mkdir()
        try:
            burner.burn(src, drive)
            raise AssertionError("BurnError atteso")
        except burner.BurnError as exc:
            assert str(exc).startswith("verifica fallita") and "IM1" in str(exc)
        assert state["awake"][-1] is False
        shutil.rmtree(disc)  # disco mai leggibile: timeout
        disc.mkdir()
        state["corrupt"] = False
        Fmt.Write = lambda self, stream: None
        try:
            burner.burn(src, drive)
            raise AssertionError("BurnError atteso")
        except burner.BurnError as exc:
            assert str(exc).startswith("disco scritto ma non verificato")
    finally:
        burner._com, burner._keep_awake, sys.platform, burner._VERIFY_TIMEOUT, burner._VERIFY_POLL = saved


def test_compare_tree_cache(tmp: Path):
    """filecmp ricorda i confronti per (percorso, dimensione, mtime): un secondo disco allo stesso percorso non deve usare la cache."""
    src, dst = tmp / "src", tmp / "dst"
    src.mkdir()
    (src / "IM1").write_bytes(b"0123456789")
    st = (src / "IM1").stat()

    def put(data: bytes):
        dst.mkdir(exist_ok=True)
        (dst / "IM1").write_bytes(data)
        os.utime(dst / "IM1", (st.st_atime, st.st_mtime))  # IMAPI copia gli mtime

    put(b"0123456780")
    assert burner._compare_tree(src, dst) is not None
    put(b"0123456789")
    assert burner._compare_tree(src, dst) is None
    put(b"0123456780")
    assert burner._compare_tree(src, dst) is not None


def test_mac_burn_confirmations():
    error, _ = _mac_burn("Test complete.\n", env={"BURN_TEST": "1"})
    assert error is None
    error, _ = _mac_burn("Test complete.\n")
    assert str(error) == "drutil non ha confermato la masterizzazione"
    error, _ = _mac_burn("Burn completed\n", env={"BURN_TEST": "1"})
    assert error is None
    error, _ = _mac_burn("niente\n", env={"BURN_TEST": "1"})
    assert str(error) == "drutil non ha confermato la masterizzazione"


def test_mac_error_io():
    assert burner._mac_error("x\nBurn failed: I/O error on /Users/m/CASO_01/a\n", 1) == "Burn failed: I/O error on"
    assert burner._mac_error("Burn failed: device busy\n", 1) == "Burn failed: device busy"


def test_win_error_zero_code():
    assert burner._win_error(_FakeError((0, "s", "d", None, 0, 0), hresult=0xC0AA0209)) == "disco protetto da scrittura"
    assert burner._win_error(_FakeError((0, "s", "d", None, 0, 0), hresult=0)) == "d"


def test_win_verify(tmp: Path):
    src, disc = tmp / "src", tmp / "disc"
    src.mkdir()
    disc.mkdir()
    (src / "DICOMDIR").write_bytes(b"dir")
    calls = []

    class Rec:
        VolumePathNames = (str(disc),)

        def EjectMedia(self):
            calls.append("eject")
            raise _FakeError(hresult=-1)  # errori ignorati

        def CloseTray(self):
            calls.append("close")
            shutil.copytree(src, disc, dirs_exist_ok=True)  # dopo il ciclo il disco si legge

    saved = (burner._VERIFY_TIMEOUT, burner._VERIFY_POLL)
    burner._VERIFY_TIMEOUT, burner._VERIFY_POLL = 0.4, 0.01
    try:
        burner._win_verify(Rec(), src)
        assert calls == ["eject", "close"]
        shutil.rmtree(disc)
        disc.mkdir()
        Rec.CloseTray = lambda self: calls.append("close")  # il disco non appare mai
        try:
            burner._win_verify(Rec(), src)
            raise AssertionError("BurnError atteso")
        except burner.BurnError as exc:
            assert str(exc) == "disco scritto ma non verificato: controllalo su un altro computer"
        (disc / "DICOMDIR").write_bytes(b"dir")
        real = burner._compare_tree

        def boom(a, b):
            raise OSError("settore")

        burner._compare_tree = boom
        try:
            burner._win_verify(Rec(), src)
            raise AssertionError("BurnError atteso")
        except burner.BurnError as exc:
            assert str(exc) == "verifica fallita: errore di lettura del disco"
        finally:
            burner._compare_tree = real
    finally:
        burner._VERIFY_TIMEOUT, burner._VERIFY_POLL = saved


def _with_win(objects, fn):
    saved = (burner._com, sys.platform, sys.modules.get("pywintypes"))
    burner._com, sys.platform = (lambda progid: objects[progid]() if callable(objects[progid]) else objects[progid]), "win32"
    sys.modules["pywintypes"] = SimpleNamespace(com_error=_FakeError)  # com_error finto: pywin32 non c'è su Mac
    try:
        return fn()
    finally:
        burner._com, sys.platform = saved[:2]
        _restore_module("pywintypes", saved[2])


def _restore_module(name, module):
    if module is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = module


def test_win_list_drives():
    class Master:
        def __iter__(self):
            return iter(["ok", "ro", "bad"])

    class Rec:
        def __init__(self):
            self.id = None

        def InitializeDiscRecorder(self, drive_id):
            if drive_id == "bad":
                raise _FakeError(hresult=-1)
            self.id = drive_id

        VolumePathNames = ("E:\\",)
        ProductId = " DVDRW "

    class Fmt:
        def IsRecorderSupported(self, recorder):
            return recorder.id != "ro"

    drives = _with_win({"IMAPI2.MsftDiscMaster2": Master, "IMAPI2.MsftDiscRecorder2": Rec,
                        "IMAPI2.MsftDiscFormat2Data": Fmt}, burner.list_drives)
    assert [(d.id, d.name) for d in drives] == [("ok", "E:\\ DVDRW")]

    def unavailable(progid):
        raise _FakeError(hresult=-1)

    assert _with_win({}, lambda: _with_com(unavailable, lambda: (
        burner.list_drives(), burner.media_free_bytes(burner.Drive("x", "x")), burner.eject(burner.Drive("x", "x"))
    ))) == ([], None, False)


def _with_com(com, fn):
    saved = burner._com
    burner._com = com
    try:
        return fn()
    finally:
        burner._com = saved


def test_win_media_free_bytes():
    def free(blank, supported=True, boom=False):
        class Rec:
            def InitializeDiscRecorder(self, drive_id):
                if boom:
                    raise _FakeError(hresult=-1)

        class Fmt:
            MediaHeuristicallyBlank = blank
            FreeSectorsOnMedia = 1000

            def IsCurrentMediaSupported(self, recorder):
                return supported

        return _with_win({"IMAPI2.MsftDiscRecorder2": Rec, "IMAPI2.MsftDiscFormat2Data": Fmt},
                         lambda: burner.media_free_bytes(burner.Drive("x", "x")))

    assert free(True) == 1000 * 2048
    assert free(False) is None
    assert free(True, supported=False) is None
    assert free(True, boom=True) is None


def test_mac_eject():
    saved = (burner._drutil, sys.platform)
    sys.platform = "darwin"
    try:
        burner._drutil = lambda *a: SimpleNamespace(returncode=1)
        assert burner.eject(burner.Drive("1", "x")) is False
        burner._drutil = lambda *a: SimpleNamespace(returncode=0)
        assert burner.eject(burner.Drive("1", "x")) is True
    finally:
        burner._drutil, sys.platform = saved


def test_win_only_com_errors_are_swallowed():
    drive = burner.Drive("x", "x")

    def broken(progid):
        raise ImportError("DLL load failed")  # pywin32 rotto: non è «nessun masterizzatore»

    for fn in (burner.list_drives, lambda: burner.media_free_bytes(drive), lambda: burner.eject(drive)):
        try:
            _with_win({}, lambda: _with_com(broken, fn))
            raise AssertionError("ImportError atteso")
        except ImportError:
            pass

    class Master:
        def __iter__(self):
            return iter(["a"])

    class Rec:
        def InitializeDiscRecorder(self, drive_id):
            raise RuntimeError("non è un errore COM")

    objects = {"IMAPI2.MsftDiscMaster2": Master, "IMAPI2.MsftDiscRecorder2": Rec, "IMAPI2.MsftDiscFormat2Data": object}
    try:
        _with_win(objects, burner.list_drives)
        raise AssertionError("RuntimeError atteso")
    except RuntimeError:
        pass
    assert burner._BURN_TIMEOUT == 150 * 60
```

- [ ] **Step 2: Verifica che fallisca**

Run: `.venv/bin/python test_anon.py`
Expected: `ModuleNotFoundError: No module named 'burner'`

- [ ] **Step 3: Crea `burner.py`**

```python
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
```

- [ ] **Step 4: Verifica che passi**

Run: `.venv/bin/python test_anon.py`
Expected: 49 righe `ok  test_…` e `TUTTI I TEST PASSATI`

- [ ] **Step 5: Controllo su questo Mac senza masterizzatore**

Run: `.venv/bin/python -c "import burner; print(burner.list_drives())"`
Expected: `[]` (oppure il masterizzatore, se ne è collegato uno)

---

### Task 4: Finestra

**Files:**
- Modify: `test_anon.py`
- Create: `app.py`

- [ ] **Step 1: Aggiungi i test della finestra**

Inserisci prima di `if __name__ == "__main__":`:

```python
_REAL = {}  # funzioni vere di messagebox/filedialog/burner, salvate alla prima finestra


def _restore(gui):
    for (obj, name), real in _REAL.items():
        setattr(obj, name, real)


def _new_window():
    """Finestra nascosta (senza esame); None se non c'è un display. Ripristina i dialoghi veri prima di sostituirli."""
    import gc
    import tkinter as tk

    import app as gui

    if not _REAL:
        for obj, names in (
            (gui.messagebox, "showinfo showerror showwarning askyesno askokcancel askretrycancel"),
            (gui.filedialog, "askdirectory"),
            (gui.burner, "list_drives eject media_free_bytes burn"),
        ):
            for name in names.split():
                _REAL[(obj, name)] = getattr(obj, name)
    _restore(gui)
    # la finestra del test precedente va distrutta qui, nel thread principale:
    # se lo facesse il garbage collector in un thread di lavoro, Tk andrebbe in crash
    gc.collect()
    try:
        window = gui.App()
    except tk.TclError:
        print("   (nessun display: salto il test della finestra)")
        return None, None
    window.withdraw()
    messages = []  # titoli dei dialoghi mostrati; "?" = domanda (risposta No), "!" = avviso
    gui.messagebox.showinfo = lambda title, text, **kw: messages.append(title)
    gui.messagebox.showerror = lambda title, text, **kw: messages.append(title + ": " + text)
    gui.messagebox.showwarning = lambda title, text, **kw: messages.append("!" + title)
    for name in ("askyesno", "askokcancel", "askretrycancel"):
        setattr(gui.messagebox, name, lambda title, text, **kw: messages.append("?" + title) or False)
    return window, messages


def _window(tmp: Path, **extra):
    """Finestra nascosta con l'esame di prova già caricato; None se non c'è un display."""
    window, messages = _new_window()
    if window:
        window.load(make_source(tmp / "src", **extra))
        _pump(window)
        assert len(window.rows) == 1 and all(window.checked.values())
        window.name.set("CASO 07")
    return window, messages


def _close(window):
    import tkinter as tk

    import app as gui

    _restore(gui)
    try:
        window.destroy()
    except tk.TclError:  # già chiusa dal test
        pass


def _pump(window, seconds: float = 60):
    import time

    end = time.time() + seconds
    window.update()
    while (window.busy or not window.events.empty()) and time.time() < end:
        window.update()
        time.sleep(0.02)
    window.update()


def _fake_burner(gui, free=lambda drive: 700 * 1024 * 1024, burn_error=None):
    """Masterizzatore finto: ritorna il registro delle chiamate."""
    calls = {"burn": [], "eject": 0}

    def burn(folder, drive):
        calls["burn"].append(folder.name)
        if burn_error and len(calls["burn"]) <= burn_error[0]:
            raise burner.BurnError("disco difettoso")

    def eject(drive):
        calls["eject"] += 1
        return True

    gui.burner.list_drives = lambda: [burner.Drive("1", "finto")]
    gui.burner.eject = eject
    gui.burner.media_free_bytes = free
    gui.burner.burn = burn
    return calls


def test_app_save_flow(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    window.on_save()
    _pump(window)
    assert (tmp / "dest" / "CASO_07" / "DICOMDIR").is_file(), list((tmp / "dest").iterdir())
    assert messages == ["Salvato"], messages
    assert window.partial is None and not window.rows  # esame tolto dalla finestra
    _close(window)


def test_app_burn_flow(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    disc = tmp / "disc"
    calls = _fake_burner(gui)
    gui.burner.burn = lambda folder, drive: calls["burn"].append(folder.name) or shutil.copytree(folder, disc)
    gui.messagebox.askokcancel = lambda title, text, **kw: True
    window.on_burn()
    _pump(window)
    assert calls["burn"] == ["CASO_07"], calls
    assert (disc / "DICOMDIR").is_file()
    assert messages == ["Fatto"], messages
    assert window.tmp is None  # cartella temporanea cancellata
    assert not window.rows and "nascita" not in window.lbl_patient.cget("text")
    _close(window)


def test_app_leak_asks_before_saving(tmp: Path):
    window, messages = _window(tmp, SeriesDescription="Torace di Rossi")
    if not window:
        return
    import app as gui

    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    asked = []
    gui.messagebox.askyesno = lambda title, text, **kw: asked.append(text) or False  # l'utente risponde No
    window.on_save()
    _pump(window)
    assert asked and "SeriesDescription" in asked[0], asked
    assert not any((tmp / "dest").iterdir())  # niente salvato
    assert messages == [], messages
    assert "Annullato" in window.lbl_status.cget("text")
    _close(window)


def test_app_poll_survives_callback_error(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return

    def boom(_):
        raise RuntimeError("boom")

    window.run_bg(lambda p: 1, boom)
    _pump(window)
    assert not window.busy and messages == ["Errore: boom"], messages
    window.load(tmp / "src")  # l'app è ancora viva
    _pump(window)
    assert len(window.rows) == 1
    _close(window)


def test_app_close_during_save_cleans_up(tmp: Path):
    window, messages = _window(tmp, n=200)
    if not window:
        return
    import time

    import app as gui

    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    gui.messagebox.askyesno = lambda title, text, **kw: True
    window.on_save()
    end = time.time() + 30
    while window.bar.cget("value") < 1 and time.time() < end:  # almeno un file già scritto
        window.update()
        time.sleep(0.01)
    assert window.busy and window.partial is not None
    worker = window.worker
    window.on_close()
    assert not worker.is_alive()
    assert not any((tmp / "dest").iterdir()), list((tmp / "dest").iterdir())
    assert window.tmp is None and window.partial is None
    _close(window)


def test_app_two_patients(tmp: Path):
    window, messages = _new_window()
    if not window:
        return
    src = make_source(tmp / "src")
    ds = pydicom.dcmread(MR)
    ds.PatientName, ds.PatientID, ds.StudyDate = "BIANCHI^ANNA", "PID999", "20260313"  # dopo il primo paziente
    ds.save_as(src / "mr.dcm")
    window.load(src)
    _pump(window)
    first = [i for i, s in window.rows.items() if s.patient_name == "ROSSI^MARIO"]
    second = [i for i, s in window.rows.items() if s.patient_name == "BIANCHI^ANNA"]
    assert first and second
    assert all(window.checked[i] for i in first) and not any(window.checked[i] for i in second)
    texts = [window.tree.item(i, "text") for i in window.tree.get_children()]
    assert any("BIANCHI ANNA" in t for t in texts) and any("ROSSI MARIO" in t for t in texts), texts
    for i in second:
        window.checked[i] = True
    assert window._ready() is None and messages == ["!Più pazienti"], messages
    _close(window)


def test_app_leak_no_on_burn(tmp: Path):
    window, messages = _window(tmp, SeriesDescription="Torace di Rossi")
    if not window:
        return
    import app as gui

    calls = _fake_burner(gui)
    window.on_burn()  # askyesno = No per default
    _pump(window)
    assert window.tmp is None and not calls["burn"] and calls["eject"] == 0
    assert "?Possibili dati del paziente" in messages and "Annullato" in window.lbl_status.cget("text")
    _close(window)


def test_app_cancel_disc_then_save_or_not(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    calls = _fake_burner(gui)
    window.on_burn()  # askokcancel e askyesno = No
    _pump(window)
    assert "?Inserisci un disco vuoto" in messages and "?Salvare l'esame anonimo?" in messages, messages
    assert window.tmp is None and not calls["burn"] and not window.rows  # CD fuori: esame tolto dalla finestra

    # stesso percorso, ma rispondo Sì e scelgo la cartella
    window.load(tmp / "src")
    _pump(window)
    window.name.set("CASO 07")
    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    gui.messagebox.askyesno = lambda title, text, **kw: title == "Salvare l'esame anonimo?"
    window.on_burn()
    _pump(window)
    assert (tmp / "dest" / "CASO_07" / "DICOMDIR").is_file(), list((tmp / "dest").iterdir())
    assert window.tmp is None and not calls["burn"] and not window.rows
    _close(window)


def test_app_burn_error_retry_and_cancel(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    calls = _fake_burner(gui, burn_error=(1,))  # il primo disco è difettoso
    gui.messagebox.askokcancel = lambda title, text, **kw: True
    gui.messagebox.askretrycancel = lambda title, text, **kw: True
    window.on_burn()
    _pump(window)
    assert calls["burn"] == ["CASO_07", "CASO_07"] and messages[-1] == "Fatto", (calls, messages)
    assert window.tmp is None

    window.load(tmp / "src")
    _pump(window)
    window.name.set("CASO 07")
    messages.clear()
    calls = _fake_burner(gui, burn_error=(9,))
    gui.messagebox.askretrycancel = lambda title, text, **kw: False  # Annulla
    window.on_burn()
    _pump(window)
    assert messages == ["?Salvare l'esame anonimo?"], messages  # risposta No
    assert window.tmp is None and calls["burn"] == ["CASO_07"], (window.tmp, calls)
    _close(window)


def test_app_disc_too_small_then_ok(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    answers = iter([None, 1000, 700 * 1024 * 1024])
    calls = _fake_burner(gui, free=lambda drive: next(answers))
    gui.messagebox.askokcancel = lambda title, text, **kw: True
    window.on_burn()
    _pump(window)
    assert messages == ["!Disco non trovato", "!Disco troppo piccolo", "Fatto"], messages
    assert calls["eject"] == 2 and calls["burn"] == ["CASO_07"]  # dopo l'anonimizzazione e dopo il disco piccolo
    _close(window)


def test_app_no_drive_and_eject_failure(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    _fake_burner(gui)
    gui.burner.list_drives = lambda: []
    window.on_burn()
    assert messages == ["?Nessun masterizzatore"], messages

    messages.clear()
    calls = _fake_burner(gui)
    gui.burner.eject = lambda drive: False
    window.on_burn()
    _pump(window)
    assert messages[:2] == ["Espulsione", "?Inserisci un disco vuoto"], messages
    _close(window)


def test_app_burn_size_guard(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    calls = _fake_burner(gui)
    gui.burner.list_drives = lambda: calls.update(listed=True) or []
    next(iter(window.rows.values())).size_bytes = gui.DVD_BYTES + 1
    window.on_burn()
    assert messages == ["!Esame troppo grande"] and "listed" not in calls and window.tmp is None, messages
    _close(window)


def test_app_empty_name_uses_auto_name(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    window.name.set("   ")
    window.on_save()
    _pump(window)
    name = window.name.get()
    assert name.startswith("ANON-") and messages == ["Salvato"], (name, messages)
    assert (tmp / "dest" / an.disc_label(name) / "DICOMDIR").is_file()
    _close(window)


def test_app_toggle_and_busy_controls(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    iid = next(iter(window.rows))
    window.toggle(iid)
    assert not window.checked[iid]
    study = window.tree.parent(iid)
    window.rows[iid].risk = "testo nell'immagine"
    window.toggle(study)  # studio con sole serie a rischio: non include nulla
    assert not window.checked[iid] and messages == ["Serie a rischio"], messages
    window._set_busy(True)
    assert str(window.ent_name.cget("state")) == "disabled" and str(window.chk_keep.cget("state")) == "disabled"
    window._set_busy(False)
    assert str(window.ent_name.cget("state")) == "normal"
    _close(window)


def test_app_closing_pauses_flow_and_skips_burn_join(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    seen = []

    def ask(title, text, **kw):
        seen.append(window.closing)
        window.events.put(("done", lambda v: seen.append("callback"), None))
        window._poll()  # con il dialogo aperto la coda non si svuota
        return False

    gui.messagebox.askyesno = ask
    window.busy = True
    window.on_close()
    assert seen == [True] and not window.closing and window.events.qsize() == 1, seen
    window.busy = False
    window.events.get_nowait()

    # durante la masterizzazione non si aspetta il thread
    window.busy, window.burning = True, True
    gui.messagebox.askyesno = lambda *a, **kw: True
    window.worker = SimpleNamespace(join=lambda t: seen.append("join"))
    window.on_close()
    assert "join" not in seen
    _close(window)


def test_app_offer_save_copy_error_and_give_up_clears(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    out = tmp / "out" / "CASO_07"
    out.mkdir(parents=True)
    (out / "f").write_text("x")
    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    answers = iter([True, False])
    gui.messagebox.askyesno = lambda *a, **kw: next(answers)
    real = gui.shutil.copytree
    gui.shutil.copytree = lambda *a, **kw: (_ for _ in ()).throw(shutil.Error([("/p/a", "/q/a", "x" * 99)]))
    try:
        window._give_up(out)
    finally:
        gui.shutil.copytree = real
    assert messages == ["Errore: Copia non riuscita: cartella piena o non scrivibile."], messages
    assert not any((tmp / "dest").iterdir()) and not window.rows  # esame tolto in ogni caso

    report = SimpleNamespace(written=0, skipped=["a.dcm: [Errno 2] No such file or directory: '/Volumes/CD/a.dcm'"])
    messages.clear()
    assert not window._prepared_ok((report, []))
    assert "Reinserisci" in messages[0], messages
    assert "/Volumes" not in messages[0]
    _close(window)


def test_friendly_errors():
    import errno

    import app as gui

    assert gui.friendly(OSError(errno.ENOSPC, "x", "/a/b")) == "Disco pieno: libera spazio e riprova."
    assert "scrivibile" in gui.friendly(PermissionError(errno.EACCES, "x", "/a/b"))
    assert "scrivibile" in gui.friendly(OSError(errno.EROFS, "x"))
    assert "reinseriscilo" in gui.friendly(FileNotFoundError(errno.ENOENT, "x", "/a/b"))
    assert gui.friendly(ValueError("prima riga\nseconda")) == "prima riga"


def test_selftest_mode(tmp):
    import app as gui

    assert gui.selftest(make_source(tmp / "src")) == 0
    empty = tmp / "vuota"
    empty.mkdir()
    (empty / "note.txt").write_text("niente DICOM")
    assert gui.selftest(empty) == 2


def test_app_keep_switch_reset_on_new_exam(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    window.keep.set(True)
    window.load(tmp / "src")
    _pump(window)
    assert window.keep.get() is False
    _close(window)


def _truncate(path: Path):
    ds = pydicom.dcmread(path)
    ds.PixelData = ds.PixelData[:-200]
    ds.save_as(path)


def test_app_skipped_images_ask_before_continuing(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    _truncate(tmp / "src" / "img1.dcm")  # dopo la lettura: un'immagine non si copia
    (tmp / "dest").mkdir()
    gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
    asked = []
    gui.messagebox.askyesno = lambda title, text, **kw: asked.append((title, text, kw)) or False
    window.on_save()
    _pump(window)
    assert asked == [("Immagini non copiate",
                      "1 immagini non sono state copiate (illeggibili o disco cambiato). Continuare comunque?",
                      {"icon": "warning", "default": "no"})], asked
    assert not any((tmp / "dest").iterdir()) and window.partial is None
    assert "Annullato" in window.lbl_status.cget("text") and messages == []

    gui.messagebox.askyesno = lambda title, text, **kw: title == "Immagini non copiate"
    window.on_save()
    _pump(window)
    assert (tmp / "dest" / "CASO_07" / "DICOMDIR").is_file() and messages == ["Salvato"], messages

    # stessa domanda prima dell'espulsione del CD
    window.load(tmp / "src")
    _pump(window)
    window.name.set("CASO 07")
    messages.clear()
    calls = _fake_burner(gui)
    gui.messagebox.askyesno = lambda title, text, **kw: messages.append("?" + title) or False
    window.on_burn()
    _pump(window)
    assert "?Immagini non copiate" in messages and calls["eject"] == 0 and window.tmp is None, (messages, calls)
    _close(window)


def test_app_folder_source_skips_eject(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    assert window.source_is_cd is True  # load() senza altro = CD
    window.load(tmp / "src", cd=False)
    _pump(window)
    assert window.source_is_cd is False
    window.name.set("CASO 07")
    calls = _fake_burner(gui)
    asked = []
    gui.messagebox.askyesno = lambda title, text, **kw: asked.append(text) or False
    window.on_burn()  # askokcancel = No: nessun disco
    _pump(window)
    assert calls["eject"] == 0 and "?Inserisci un disco vuoto" in messages, (calls, messages)
    assert asked and "espulso" not in asked[0], asked

    window.load(tmp / "src")  # CD: espulso, e il testo lo dice
    _pump(window)
    window.name.set("CASO 07")
    asked.clear()
    window.on_burn()
    _pump(window)
    assert calls["eject"] == 1 and asked and "espulso" in asked[0], (calls, asked)
    _close(window)


def test_app_same_patient_name_formats(tmp: Path):
    import app as gui

    a = SimpleNamespace(patient_name="ROSSI^MARIO", patient_id="PID12345")
    b = SimpleNamespace(patient_name="Rossi^Mario^^^", patient_id=" PID12345 ")
    assert gui.who(a) == gui.who(b)
    window, messages = _new_window()
    if not window:
        return
    src = make_source(tmp / "src")
    ds = pydicom.dcmread(MR)
    ds.PatientName, ds.PatientID, ds.StudyDate = "ROSSI^MARIO^^^", "PID12345", "20260313"
    ds.save_as(src / "mr.dcm")
    window.load(src)
    _pump(window)
    assert len(window.rows) == 2 and all(window.checked.values())
    assert "Attenzione" not in window.lbl_patient.cget("text"), window.lbl_patient.cget("text")
    _close(window)


def test_app_leak_check_gets_report_identifiers(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    seen = []
    real = gui.an.check_leaks
    gui.an.check_leaks = lambda out, series, extra=frozenset(): seen.append(set(extra)) or []
    try:
        (tmp / "dest").mkdir()
        gui.filedialog.askdirectory = lambda **kw: str(tmp / "dest")
        window.on_save()
        _pump(window)
    finally:
        gui.an.check_leaks = real
    assert seen and {"ROSSI", "PID12345"} <= seen[0], seen
    _close(window)


def test_app_exception_in_handler_shown(tmp: Path):
    window, messages = _new_window()
    if not window:
        return

    def boom():
        raise RuntimeError("boom")

    window.after(0, boom)
    window.update()
    assert messages == ["Errore: boom"], messages
    window.report_callback_exception(ValueError, ValueError("diretta"), None)
    assert messages[-1] == "Errore: diretta", messages
    _close(window)


def test_app_poll_cancelled_on_destroy():
    window, messages = _new_window()
    if not window:
        return
    cancelled = []
    real = window.after_cancel
    window.after_cancel = lambda id: cancelled.append(id) or real(id)
    pending = window._poll_id
    window.destroy()
    assert pending in cancelled, (pending, cancelled)


def test_app_burn_error_titles(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    _fake_burner(gui)
    gui.messagebox.askokcancel = lambda title, text, **kw: True
    for text, title in (("disco scritto ma non verificato: controllalo su un altro computer", "Disco da controllare"),
                        ("verifica fallita: il file a.dcm è diverso", "Masterizzazione non riuscita")):
        def burn(folder, drive, text=text):
            raise burner.BurnError(text)

        gui.burner.burn = burn
        messages.clear()
        window.on_burn()
        _pump(window)
        assert messages[:2] == ["?" + title, "?Salvare l'esame anonimo?"], messages
        window.load(tmp / "src")
        _pump(window)
        window.name.set("CASO 07")
    _close(window)


def test_app_no_blank_disc_message(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    _fake_burner(gui, free=lambda drive: None)
    gui.messagebox.askokcancel = lambda title, text, **kw: len(shown) < 1
    shown = []
    gui.messagebox.showwarning = lambda title, text, **kw: shown.append((title, text))
    window.on_burn()
    _pump(window)
    assert shown and shown[0][0] == "Disco non trovato", shown
    assert "Usa un disco nuovo (CD-R/DVD-R) oppure cancella prima un riscrivibile." in shown[0][1], shown
    _close(window)


def test_app_startup_sweeps_old_temp_folders(tmp: Path):
    import time

    import app as gui

    old, fresh, other = tmp / "anon_dicom_old", tmp / "anon_dicom_new", tmp / "altro_vecchio"
    for folder in (old, fresh, other):
        (folder / "x").mkdir(parents=True)
    past = time.time() - 13 * 3600
    for folder in (old, other):
        os.utime(folder, (past, past))
    saved = tempfile.tempdir
    tempfile.tempdir = str(tmp)
    try:
        window, _ = _new_window()
        if not window:
            return
        assert not old.exists() and fresh.exists() and other.exists()
        _close(window)
    finally:
        tempfile.tempdir = saved


def test_selftest_checks_pywin32_bundle(tmp: Path):
    import app as gui

    src = make_source(tmp / "src")
    names = ("pythoncom", "win32com", "win32com.client")
    saved = (sys.platform, {n: sys.modules.get(n) for n in names})
    sys.platform = "win32"
    try:
        for n in names:  # None in sys.modules = import fallito
            sys.modules[n] = None
        assert gui.selftest(src) == 1
        for n in names:
            sys.modules[n] = SimpleNamespace()
        assert gui.selftest(src) == 0
    finally:
        sys.platform = saved[0]
        for n, module in saved[1].items():
            _restore_module(n, module)
```

- [ ] **Step 2: Verifica che fallisca**

Run: `.venv/bin/python test_anon.py`
Expected: `ModuleNotFoundError: No module named 'app'` in `test_app_save_flow`

- [ ] **Step 3: Crea `app.py`**

```python
"""Anonimizza e Masterizza DICOM: finestra principale."""
from __future__ import annotations

import errno
import queue
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from itertools import groupby
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import anonymizer as an
import burner
import scanner

CD_BYTES = 700 * 1024 * 1024
DVD_BYTES = 4_700_000_000
ON, OFF = "☑", "☐"
HINT = "Inserisci il CD dell'esame e premi «Leggi CD»."


def mb(n: int) -> str:
    return f"{n / 1_000_000:,.0f} MB".replace(",", ".")


def nice_date(d: str) -> str:
    return f"{d[6:8]}/{d[4:6]}/{d[:4]}" if len(d) == 8 else d


def friendly(exc: Exception) -> str:
    """Messaggio breve per l'utente; mai il percorso completo per gli errori di sistema più comuni."""
    code = exc.errno if isinstance(exc, OSError) else None
    if code == errno.ENOSPC:
        return "Disco pieno: libera spazio e riprova."
    if code in (errno.EROFS, errno.EACCES):
        return "Cartella non scrivibile: scegline un'altra."
    if code == errno.ENOENT:
        return "File non trovato: se hai tolto il CD originale, reinseriscilo."
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def who(s: scanner.Series) -> tuple[str, str]:
    """Il paziente di una serie (stessa chiave per l'etichetta e per il controllo prima di anonimizzare)."""
    return scanner.patient_key(s.patient_name, s.patient_id)


def sweep_temp(max_age: float = 12 * 3600) -> None:
    """Toglie le cartelle temporanee rimaste da un'uscita brusca (più vecchie di max_age secondi)."""
    for folder in Path(tempfile.gettempdir()).glob("anon_dicom_*"):
        try:
            if time.time() - folder.stat().st_mtime > max_age:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


class Annullato(Exception):
    """Lanciata da progress() nel thread di lavoro quando la finestra viene chiusa."""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        sweep_temp()
        self.title("Anonimizza e Masterizza DICOM")
        self.geometry("820x660")
        self.minsize(700, 560)
        self.series: list[scanner.Series] = []
        self.rows: dict[str, scanner.Series] = {}  # riga dell'albero -> serie
        self.checked: dict[str, bool] = {}
        self.tmp: Path | None = None  # cartella temporanea con l'esame anonimo
        self.source_is_cd = True  # False se l'esame viene da «Scegli cartella…»: niente espulsione
        self.partial: Path | None = None  # cartella di salvataggio in scrittura (creata dall'app)
        self.report: an.Report | None = None
        self.busy = False
        self.burning = False
        self.closing = False  # dialogo di uscita aperto: il flusso resta fermo
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self._last_click: tuple[str, float] = ("", 0.0)
        self.events: queue.Queue = queue.Queue()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.tk.call("tk", "windowingsystem") == "aqua":  # Cmd+Q, Dock > Esci, logout
            self.createcommand("::tk::mac::Quit", self.on_close)
        self._poll_id = self.after(100, self._poll)

    def report_callback_exception(self, exc, val, tb):
        """Negli eseguibili senza console gli errori dei pulsanti sarebbero invisibili."""
        self.show_error(val)

    def destroy(self):
        try:
            self.after_cancel(self._poll_id)
        except tk.TclError:
            pass
        super().destroy()

    # ---------- interfaccia ----------

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        # in basso per primi: se la finestra è piccola si restringe l'albero, non i pulsanti
        self.lbl_status = ttk.Label(self, text="")
        self.lbl_status.pack(side="bottom", fill="x", padx=12, pady=(2, 10))
        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(side="bottom", fill="x", padx=12)

        dest = ttk.LabelFrame(self, text="3  Destinazione")
        dest.pack(side="bottom", fill="x", **pad)
        self.lbl_size = ttk.Label(dest, text="Nessuna serie selezionata.")
        self.lbl_size.pack(anchor="w", padx=8, pady=(6, 0))
        bar = ttk.Frame(dest)
        bar.pack(fill="x", padx=8, pady=6)
        self.btn_burn = ttk.Button(bar, text="Masterizza", command=self.on_burn)
        self.btn_burn.pack(side="left")
        self.btn_save = ttk.Button(bar, text="Salva in cartella…", command=self.on_save)
        self.btn_save.pack(side="left", padx=6)

        anon = ttk.LabelFrame(self, text="2  Anonimizza")
        anon.pack(side="bottom", fill="x", **pad)
        ttk.Label(anon, text="Nuovo nome del paziente:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.name = tk.StringVar(value=an.auto_name())
        self.ent_name = ttk.Entry(anon, textvariable=self.name, width=34)
        self.ent_name.grid(row=0, column=1, sticky="w")
        self.keep = tk.BooleanVar(value=False)
        self.chk_keep = ttk.Checkbutton(
            anon, text="Mantieni età, sesso, peso e date (serve per PET e studi dinamici)", variable=self.keep
        )
        self.chk_keep.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))

        src = ttk.LabelFrame(self, text="1  Sorgente")
        src.pack(fill="both", expand=True, **pad)
        bar = ttk.Frame(src)
        bar.pack(fill="x", padx=8, pady=6)
        self.btn_cd = ttk.Button(bar, text="Leggi CD", command=self.on_read_cd)
        self.btn_cd.pack(side="left")
        self.btn_folder = ttk.Button(bar, text="Scegli cartella…", command=self.on_choose_folder)
        self.btn_folder.pack(side="left", padx=6)
        self.lbl_patient = ttk.Label(src, text=HINT)
        self.lbl_patient.pack(fill="x", padx=8)
        box = ttk.Frame(src)
        box.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree = ttk.Treeview(box, columns=("img", "mb", "note"), height=10)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.heading("#0", text="Studio / serie (clic per includere o escludere)")
        self.tree.heading("img", text="Immagini")
        self.tree.heading("mb", text="Dimensione")
        self.tree.heading("note", text="Note")
        self.tree.column("#0", width=380)
        self.tree.column("img", width=80, anchor="e")
        self.tree.column("mb", width=90, anchor="e")
        self.tree.column("note", width=220)
        self.tree.tag_configure("risk", foreground="#b45309")
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<space>", self.on_tree_space)
        self._set_busy(False)

    def status(self, text: str):
        self.lbl_status.config(text=text)

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_cd.config(state=state)
        self.btn_folder.config(state=state)
        self.ent_name.config(state=state)
        self.chk_keep.config(state=state)
        ready = "normal" if self.series and not busy else "disabled"
        self.btn_burn.config(state=ready)
        self.btn_save.config(state=ready)
        if not busy:
            self.bar.stop()
            self.bar.config(mode="determinate", value=0)

    def _show_progress(self, done: int, total: int, text: str):
        if total:
            if str(self.bar.cget("mode")) != "determinate":
                self.bar.stop()
                self.bar.config(mode="determinate")
            self.bar.config(maximum=total, value=done)
            self.status(f"{text}… {done}/{total}")
        else:
            self.bar.config(mode="indeterminate")
            self.bar.start(12)
            self.status(f"{text}…")

    # ---------- lavoro in background ----------

    def run_bg(self, work, on_done, on_error=None):
        """Esegue work(progress) in un thread; on_done/on_error tornano nel thread della finestra."""
        self.cancel.clear()
        self.burning = False
        self._set_busy(True)

        def target():
            try:
                self.events.put(("done", on_done, work(self.progress)))
            except Exception as exc:
                self.events.put(("done", on_error or self.show_error, exc))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def progress(self, done: int, total: int, text: str):  # chiamata dal thread di lavoro
        if self.cancel.is_set():
            raise Annullato()  # scanner e anonymizer chiamano progress a ogni file: si fermano subito
        self.events.put(("progress", done, total, text))

    def _poll(self):
        try:
            while not self.closing:
                kind, *args = self.events.get_nowait()
                if kind == "progress":
                    self._show_progress(*args)
                else:
                    callback, value = args
                    self._set_busy(False)
                    try:
                        callback(value)
                    except Exception as exc:  # un errore qui non deve fermare il ciclo
                        self.show_error(exc)
        except queue.Empty:
            pass
        finally:
            self._poll_id = self.after(100, self._poll)

    def show_error(self, exc: Exception):
        self.cleanup()
        self.status("")
        messagebox.showerror("Errore", friendly(exc))

    def cleanup(self):
        for folder in (self.tmp, self.partial):
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
        self.tmp = self.partial = None

    def _check(self, text: str, fn, *args):
        """Chiamata breve ma bloccante nel thread della finestra: prima mostro il perché."""
        self.status(text)
        self.update_idletasks()
        try:
            return fn(*args)
        finally:
            self.status("")

    def _clear_exam(self):
        """Svuota l'esame dalla finestra (privacy sui PC condivisi, o prima di leggerne un altro)."""
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.checked.clear()
        self.series = []
        self.lbl_patient.config(text=HINT)
        self._set_busy(self.busy)
        self._update_size()

    # ---------- 1 sorgente ----------

    def on_read_cd(self):
        def work(progress):
            progress(0, 0, "Ricerca del CD")  # può restare appeso su dischi lenti o di rete
            return scanner.find_dicom_sources()

        self.run_bg(work, self._after_sources)

    def _after_sources(self, sources):
        self.status("")
        if not sources:
            messagebox.showinfo(
                "Nessun CD trovato",
                "Non trovo un CD con esami DICOM.\n"
                "Inserisci il CD, attendi qualche secondo e riprova, oppure usa «Scegli cartella…».",
            )
            return
        root = sources[0]
        if len(sources) > 1:  # più dischi con esami: l'utente sceglie
            chosen = filedialog.askdirectory(
                title="Ho trovato più dischi con esami: scegli la cartella giusta", initialdir=str(root)
            )
            if not chosen:
                return
            root = Path(chosen)
        self.load(root, cd=True)

    def on_choose_folder(self):
        folder = filedialog.askdirectory(title="Cartella con l'esame DICOM")
        if folder:
            self.load(Path(folder), cd=False)

    def load(self, root: Path, cd: bool = True):
        self.source_is_cd = cd
        self.cleanup()
        self._clear_exam()  # se la lettura fallisce non deve restare selezionabile l'esame di prima
        self.lbl_patient.config(text=f"Lettura di {root}…")
        self.run_bg(lambda progress: scanner.scan(root, progress), self.show_series)

    def show_series(self, result):
        series, ignored, unreadable = result
        self.series = series
        self.status(f"{ignored} file non DICOM ignorati (viewer, documenti…)." if ignored else "")
        if unreadable:
            messagebox.showwarning(
                "File illeggibili",
                f"{unreadable} file non si riescono a leggere (disco graffiato o sporco?).\n"
                "L'esame potrebbe essere incompleto: controlla il numero di immagini.",
            )
        if not series:
            self.lbl_patient.config(text="Nessuna immagine DICOM trovata qui.")
            self._set_busy(False)
            self._update_size()
            return
        patients = list(dict.fromkeys(who(s) for s in series))
        first_patient = patients[0]
        for _, items in groupby(series, key=lambda s: s.study_uid):
            items = list(items)
            first = items[0]
            text = f"{nice_date(first.study_date)}  {first.study_desc or first.modality}"
            if len(patients) > 1:  # solo a schermo: aiuta a non confondere i pazienti
                text += f"  ·  {first.patient_name.replace('^', ' ')}"
            study = self.tree.insert("", "end", open=True, text=text)
            for s in items:
                iid = self.tree.insert(
                    study, "end",
                    values=(len(s.files), mb(s.size_bytes), f"⚠ {s.risk}" if s.risk else ""),
                    tags=("risk",) if s.risk else (),
                )
                self.rows[iid] = s
                self.checked[iid] = s.risk is None and who(s) == first_patient  # di default un solo paziente
                self._render(iid)
        if len(patients) > 1:
            text = (
                f"Attenzione: qui ci sono {len(patients)} pazienti diversi. "
                "Ho selezionato solo il primo: controlla prima di procedere."
            )
        else:
            name, pid = series[0].patient_name, series[0].patient_id
            text = (
                f"Paziente originale: {name.replace('^', ' ')}   ID {pid}   "
                f"data di nascita {nice_date(series[0].birth_date)}"
            )
        self.lbl_patient.config(text=text)
        self.name.set(an.auto_name())
        self.keep.set(False)  # la scelta vale per un solo esame
        self._set_busy(False)
        self._update_size()

    def _render(self, iid: str):
        s = self.rows[iid]
        mark = ON if self.checked[iid] else OFF
        self.tree.item(iid, text=f"{mark}  Serie {s.series_number}  {s.series_desc or s.modality}")

    def on_tree_click(self, event):
        iid = self.tree.identify_row(event.y)
        if self.busy or not iid or self.tree.identify_element(event.x, event.y).endswith("indicator"):
            return
        last_iid, last_time = self._last_click
        if iid == last_iid and time.monotonic() - last_time < 0.4:
            return  # secondo rilascio di un doppio clic
        self.toggle(iid)
        self._last_click = (iid, time.monotonic())

    def on_tree_space(self, event):
        iid = self.tree.focus()
        if iid and not self.busy:
            self.toggle(iid)
        return "break"

    def toggle(self, iid: str):
        if iid in self.rows:
            targets, turn_on = [iid], not self.checked[iid]
        else:  # riga dello studio: se c'è qualcosa di incluso tolgo tutto, altrimenti includo le serie sicure
            targets = list(self.tree.get_children(iid))
            turn_on = not any(self.checked[t] for t in targets)
            if turn_on:
                targets = [t for t in targets if self.rows[t].risk is None]
                if not targets:
                    messagebox.showinfo("Serie a rischio", "Le serie di questo studio sono a rischio: includile una per una.")
                    return
        for t in targets:
            s = self.rows[t]
            if turn_on and s.risk and not messagebox.askyesno(
                "Serie a rischio",
                f"«{s.series_desc or 'Serie ' + s.series_number}» può contenere il nome del paziente "
                f"scritto dentro le immagini ({s.risk}).\n\nIncludila solo se l'hai controllata. Includere?",
                icon="warning",
            ):
                continue
            self.checked[t] = turn_on
            self._render(t)
        self._update_size()

    def selected(self) -> list[scanner.Series]:
        return [self.rows[i] for i, on in self.checked.items() if on]

    def _update_size(self):
        total = sum(s.size_bytes for s in self.selected())
        if not total:
            text = "Nessuna serie selezionata."
        elif total <= CD_BYTES:
            text = f"Totale {mb(total)}: sta su un CD."
        elif total <= DVD_BYTES:
            text = f"Totale {mb(total)}: serve un DVD."
        else:
            text = f"Totale {mb(total)}: troppo grande anche per un DVD. Togli qualche serie."
        self.lbl_size.config(text=text)

    # ---------- 2 anonimizzazione ----------

    def _ready(self) -> list[scanner.Series] | None:
        chosen = self.selected()
        if not chosen:
            messagebox.showinfo("Niente da fare", "Seleziona almeno una serie.")
            return None
        if len({who(s) for s in chosen}) > 1:
            messagebox.showwarning(
                "Più pazienti", "Le serie selezionate sono di pazienti diversi. Seleziona un solo paziente per disco."
            )
            return None
        name = self.name.get().strip() or an.auto_name()
        self.name.set(name)
        if not an.NAME_RE.fullmatch(name):
            messagebox.showwarning(
                "Nome non valido", "Usa solo lettere senza accenti, numeri, spazio, _ o - (massimo 64 caratteri)."
            )
            return None
        return chosen

    def _prepare(self, chosen, out: Path, name: str, keep: bool, progress):
        """Thread di lavoro: anonimizza e controlla le fughe di dati."""
        report = an.anonymize(chosen, out, name, keep, progress)
        progress(0, 0, "Controllo di sicurezza")
        leaks = an.check_leaks(out, self.series, report.identifiers) if report.written else []
        return report, leaks

    def _prepared_ok(self, result) -> bool:
        report, leaks = result
        if not report.written:
            if report.skipped and all("Errno 2" in e or "WinError 2" in e or "WinError 3" in e for e in report.skipped):
                detail = "Reinserisci il CD originale e riprova."
            else:  # mai percorsi completi: al massimo il nome del file
                detail = "\n".join(e.partition(": ")[0] + ": " + e.partition(": ")[2].split(":")[0] for e in report.skipped[:10])
            messagebox.showerror("Nessuna immagine", "Nessuna immagine è stata anonimizzata.\n\n" + detail)
            return False
        if leaks and not messagebox.askyesno(
            "Possibili dati del paziente",
            "Questi campi dell'esame anonimo contengono ancora dati del paziente originale:\n\n"
            + "\n".join(leaks[:10]) + ("\n…" if len(leaks) > 10 else "")
            + "\n\nSe è una parola normale (es. il cognome «Testa» in «TC TESTA») puoi procedere.\n"
            "Procedere comunque?",
            icon="warning",
            default="no",
        ):
            return False
        if report.skipped and not messagebox.askyesno(
            "Immagini non copiate",
            f"{len(report.skipped)} immagini non sono state copiate (illeggibili o disco cambiato). Continuare comunque?",
            icon="warning",
            default="no",
        ):
            return False
        self.report = report
        return True

    # ---------- 3 destinazione ----------

    def on_save(self):
        chosen = self._ready()
        if not chosen:
            return
        folder = filedialog.askdirectory(title="Dove salvo l'esame anonimo?")
        if not folder:
            return
        name, keep = self.name.get().strip(), self.keep.get()
        out, n = Path(folder) / an.disc_label(name), 2
        while out.exists():
            out, n = Path(folder) / f"{an.disc_label(name)}_{n}", n + 1
        self.partial = out  # se la finestra si chiude a metà, la cartella parziale va tolta
        self.run_bg(lambda p: self._prepare(chosen, out, name, keep, p), lambda r: self._after_save(r, out))

    def _after_save(self, result, out: Path):
        if not self._prepared_ok(result):
            self.cleanup()
            self.status("Annullato: nessun file salvato.")
            return
        self.partial = None  # salvataggio completo: da qui in poi è dell'utente
        self.status(f"Salvato in {out}")
        self._clear_exam()
        messagebox.showinfo("Salvato", f"Esame anonimo salvato in:\n{out}\n\n{self._summary()}")

    def on_burn(self):
        chosen = self._ready()
        if not chosen:
            return
        if sum(s.size_bytes for s in chosen) > DVD_BYTES:
            messagebox.showwarning(
                "Esame troppo grande",
                "L'esame è troppo grande anche per un DVD: togli qualche serie o usa «Salva in cartella…».",
            )
            return
        try:
            drives = self._check("Controllo del masterizzatore…", burner.list_drives)
        except Exception as exc:  # IMAPI/drutil non disponibili: meglio un messaggio che un clic muto
            self.show_error(exc)
            return
        if not drives:
            if messagebox.askyesno(
                "Nessun masterizzatore", "Non trovo un masterizzatore collegato.\nVuoi salvare l'esame in una cartella?"
            ):
                self.on_save()
            return
        drive = drives[0]  # ponytail: primo masterizzatore; i PC di reparto ne hanno uno
        name, keep = self.name.get().strip(), self.keep.get()
        self.cleanup()
        self.tmp = Path(tempfile.mkdtemp(prefix="anon_dicom_"))
        out = self.tmp / an.disc_label(name)
        self.run_bg(
            lambda p: self._prepare(chosen, out, name, keep, p), lambda r: self._after_prepare(r, out, drive)
        )

    def _after_prepare(self, result, out: Path, drive):
        if not self._prepared_ok(result):
            self.cleanup()
            self.status("Annullato: nessun file salvato.")
            return
        if not self.source_is_cd:  # esame da cartella: nessun CD da espellere
            self._ask_disc(out, drive)
            return
        # il CD originale è spesso nello stesso masterizzatore: lo espello per far inserire il disco vuoto
        self.status("Espulsione del disco…")
        self.run_bg(lambda p: burner.eject(drive), lambda ok: self._after_eject(ok, out, drive))

    def _after_eject(self, ejected: bool, out: Path, drive):
        if not ejected:
            messagebox.showinfo("Espulsione", "Non riesco a espellere il disco: se c'è il CD originale, estrailo a mano.")
        self._ask_disc(out, drive)

    def _ask_disc(self, out: Path, drive):
        need = burner.needed_bytes(out)
        while True:
            if not messagebox.askokcancel(
                "Inserisci un disco vuoto",
                f"L'esame anonimo è pronto ({mb(need)}).\n\n"
                "Inserisci un CD o DVD vuoto nel masterizzatore, attendi qualche secondo e premi OK.",
            ):
                self._give_up(out)
                return
            free = self._check("Controllo del masterizzatore…", burner.media_free_bytes, drive)
            if free is None:
                messagebox.showwarning(
                    "Disco non trovato",
                    "Non trovo un disco vuoto nel masterizzatore.\nSe l'hai appena inserito, attendi qualche secondo.\n"
                    "Usa un disco nuovo (CD-R/DVD-R) oppure cancella prima un riscrivibile.",
                )
                continue
            if need > free:
                messagebox.showwarning(
                    "Disco troppo piccolo", f"Servono {mb(need)}, il disco ne ha {mb(free)}.\nInserisci un DVD."
                )
                self._check("Espulsione del disco…", burner.eject, drive)
                continue
            break

        def work(progress):
            progress(0, 0, "Masterizzazione e verifica: non rimuovere il disco")
            burner.burn(out, drive)

        self.run_bg(work, self._burn_done, lambda exc: self._burn_failed(exc, out, drive))
        self.burning = True  # una masterizzazione in corso non si può interrompere

    def _burn_done(self, _):
        self.cleanup()
        self.status("Disco pronto.")
        self._clear_exam()
        messagebox.showinfo("Fatto", f"Disco masterizzato e verificato.\n\n{self._summary()}\n\nIl disco è stato espulso.")

    def _burn_failed(self, exc, out: Path, drive):
        title = "Disco da controllare" if str(exc).startswith("disco scritto ma non verificato") else "Masterizzazione non riuscita"
        if messagebox.askretrycancel(title, f"{exc}\n\nVuoi riprovare con un altro disco?"):
            self._check("Espulsione del disco…", burner.eject, drive)
            self._ask_disc(out, drive)
        else:
            self._give_up(out)

    def _give_up(self, out: Path):
        """Niente disco: il CD originale è già stato espulso, quindi offro di tenere l'esame anonimo."""
        saved = self._offer_save(out)
        self.cleanup()
        self._clear_exam()  # il CD originale è già fuori: l'esame in finestra non è più riproducibile
        self.status(f"Salvato in {saved}" if saved else "Masterizzazione annullata.")

    def _offer_save(self, out: Path) -> Path | None:
        lead = "Il CD originale è stato espulso. " if self.source_is_cd else ""
        while messagebox.askyesno(
            "Salvare l'esame anonimo?",
            lead + "Vuoi salvare l'esame anonimo in una cartella, per masterizzarlo o copiarlo più tardi?",
        ):
            folder = filedialog.askdirectory(title="Dove salvo l'esame anonimo?")
            if not folder:
                continue
            dest, n = Path(folder) / out.name, 2
            while dest.exists():
                dest, n = Path(folder) / f"{out.name}_{n}", n + 1
            try:
                self._check("Copia in corso…", shutil.copytree, out, dest)
            except Exception as exc:
                shutil.rmtree(dest, ignore_errors=True)
                # shutil.Error raccoglie ogni file fallito, con i percorsi: troppo lungo da mostrare
                text = "Copia non riuscita: cartella piena o non scrivibile." if isinstance(exc, shutil.Error) else friendly(exc)
                messagebox.showerror("Errore", text)
                continue
            return dest
        return None

    def _summary(self) -> str:
        text = f"{self.report.written} immagini anonimizzate."
        if self.report.skipped:
            text += f"\n{len(self.report.skipped)} file saltati perché illeggibili o non anonimizzabili."
        return text

    def on_close(self):
        if self.busy:
            text = "Sto ancora lavorando. Uscire comunque?"
            if self.burning:
                text += "\nLa masterizzazione non si può interrompere: il disco diventerà inutilizzabile."
            self.closing = True
            try:
                if not messagebox.askyesno("Operazione in corso", text, icon="warning", default="no"):
                    return
            finally:
                self.closing = False
            self.cancel.set()
            if self.worker and not self.burning:
                self.worker.join(10)  # si ferma entro un file; una masterizzazione no, allora non aspetto oltre
        self.cleanup()
        self.destroy()


def selftest(folder) -> int:
    """Verifica senza finestre (app compilata): scansiona, anonimizza in una cartella temporanea e controlla. 0 = ok, 1 = errore, 2 = nessuna serie."""
    from pydicom.fileset import FileSet

    if sys.platform == "win32":  # la masterizzazione dipende da pywin32: l'eseguibile deve averlo dentro
        try:
            import pythoncom, win32com.client  # noqa: E401, F401
        except Exception as exc:
            print(f"SELFTEST: pywin32 non incluso ({type(exc).__name__})")
            return 1
    series = [s for s in scanner.scan(Path(folder))[0] if s.risk is None]
    if not series:
        print("SELFTEST: nessuna serie")
        return 2
    out = Path(tempfile.mkdtemp()) / "SELFTEST"
    try:
        report = an.anonymize(series, out, "SELFTEST", False)
        leaks = an.check_leaks(out, series, report.identifiers)
        try:
            fileset_ok = len(FileSet(out / "DICOMDIR")) == report.written
        except Exception:
            fileset_ok = False
    finally:
        shutil.rmtree(out.parent, ignore_errors=True)
    print(f"SELFTEST written={report.written} skipped={len(report.skipped)} leaks={len(leaks)} fileset={'ok' if fileset_ok else 'ERRORE'}")
    return 0 if report.written > 0 and not leaks and fileset_ok else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"] and len(sys.argv) > 2:
        sys.exit(selftest(sys.argv[2]))
    App().mainloop()
```

- [ ] **Step 4: Verifica che passi**

Run: `.venv/bin/python test_anon.py`
Expected: 78 righe `ok  test_…` e `TUTTI I TEST PASSATI`

- [ ] **Step 5: Controllo visivo**

Crea un esame di prova: 3 immagini TC e uno screenshot dello stesso paziente e dello stesso studio:
```bash
.venv/bin/python -c "
from pathlib import Path; import test_anon as t
src = t.make_source(Path('esempio'))
ct = t.pydicom.dcmread(src / 'img0.dcm'); sc = t.pydicom.dcmread(t.SC)
for kw in ('PatientName', 'PatientID', 'PatientBirthDate', 'StudyInstanceUID', 'StudyDate', 'StudyDescription'):
    setattr(sc, kw, getattr(ct, kw, ''))
sc.save_as(src / 'sc')"
```
Run: `.venv/bin/python app.py` → «Scegli cartella…» → `esempio`.
Expected: «Paziente originale: ROSSI MARIO   ID PID12345   data di nascita 12/03/1960»; studio con 2 serie; la serie TC è spuntata ☑; la serie screenshot non è spuntata ☐, ha ⚠ e testo arancione; «1 file non DICOM ignorati»; totale «sta su un CD». Cliccando sulla serie ⚠ compare la domanda di conferma. Fai uno screenshot della finestra e controlla che non ci siano testi tagliati. Tieni `esempio` per il task 5.

- [ ] **Step 6: Autoverifica senza finestra**

Run: `.venv/bin/python app.py --selftest esempio`
Expected: `SELFTEST written=3 skipped=0 leaks=0 fileset=ok`, codice di uscita 0 (la serie screenshot è esclusa perché a rischio).

---

### Task 5: App per Mac

**Files:** nessun file di codice nuovo (si usa la modalità `--selftest` di `app.py`, task 4)

- [ ] **Step 1: Installa PyInstaller**

Run: `.venv/bin/pip install pyinstaller==6.11.1`
Expected: `Successfully installed … pyinstaller-6.11.1`

- [ ] **Step 2: Compila**

Run: `.venv/bin/pyinstaller --noconfirm --windowed --name "Anonimizza DICOM" --osx-bundle-identifier local.anonimizza-dicom app.py`
Expected: `dist/Anonimizza DICOM.app` presente

- [ ] **Step 3: Dichiara l'italiano nell'app** (così i dialoghi di sistema non usano l'inglese)

Run:
```bash
plutil -replace CFBundleDevelopmentRegion -string it "dist/Anonimizza DICOM.app/Contents/Info.plist"
plutil -replace CFBundleLocalizations -json '["it"]' "dist/Anonimizza DICOM.app/Contents/Info.plist"
```
Expected: nessun output; `plutil -p "dist/Anonimizza DICOM.app/Contents/Info.plist" | grep -i -E "region|localiz"` mostra `it`.

La modifica invalida la firma dell'app: rifirmala (firma locale «ad-hoc», Gatekeeper avviserà comunque al primo avvio su altri Mac).

Run: `codesign --force --deep -s - "dist/Anonimizza DICOM.app" && codesign --verify --deep --strict "dist/Anonimizza DICOM.app"`
Expected: nessun errore.

- [ ] **Step 3a: Autoverifica dell'app compilata** (controlla che PyInstaller abbia incluso tutto pydicom)

Run: `"dist/Anonimizza DICOM.app/Contents/MacOS/Anonimizza DICOM" --selftest esempio`
Expected: `SELFTEST written=3 skipped=0 leaks=0 fileset=ok`, codice di uscita 0.

- [ ] **Step 3b: Avvia l'app compilata e ripeti il controllo visivo del task 4, step 5**

Run: `open "dist/Anonimizza DICOM.app"`
Expected: la stessa finestra; «Salva in cartella…» produce una cartella con DICOMDIR; i pulsanti dei dialoghi sono in italiano (Sì/No, Riprova/Annulla).

- [ ] **Step 4: Controlla la dimensione**

Run: `du -sh "dist/Anonimizza DICOM.app"`
Expected: circa 28 MB (limite 60 MB). Se è di più, `find "dist/Anonimizza DICOM.app" -size +2M` mostra cosa occupa spazio.

- [ ] **Step 5: Pulizia**

Run: `rm -rf build "Anonimizza DICOM.spec" esempio`
Expected: restano `dist/` e i file del progetto.

---

### Task 6: Windows (.exe) con GitHub Actions e README

PyInstaller non compila per un altro sistema: l'`.exe` va creato su Windows. Il modo più semplice senza un PC Windows è GitHub Actions. **Serve il consenso dell'utente** per creare un repository GitHub (privato) e caricarci il codice, che non contiene dati di pazienti.

**Files:**
- Create: `.github/workflows/build.yml`
- Create: `README.md`
- Create: `.gitignore`

- [ ] **Step 1: Crea `.github/workflows/build.yml`**

```yaml
name: build
on:
  push:
  workflow_dispatch:
jobs:
  build:
    timeout-minutes: 30
    strategy:
      matrix:
        os: [windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt pyinstaller==6.11.1
      - run: python test_anon.py
      - run: python -c "from pathlib import Path; import test_anon as t; t.make_source(Path('esempio'))"
      - if: runner.os == 'Windows'
        shell: pwsh
        run: |
          pyinstaller --noconfirm --onefile --windowed --disable-windowed-traceback --name "Anonimizza DICOM" app.py
          # app senza console: Start-Process -Wait per avere il codice di uscita
          $p = Start-Process -FilePath "dist/Anonimizza DICOM.exe" -ArgumentList "--selftest","esempio" -Wait -PassThru
          if ($p.ExitCode -ne 0) { throw "autoverifica fallita: $($p.ExitCode)" }
      - if: runner.os == 'macOS'
        run: |
          pyinstaller --noconfirm --windowed --osx-bundle-identifier local.anonimizza-dicom --name "Anonimizza DICOM" app.py
          plutil -replace CFBundleDevelopmentRegion -string it "dist/Anonimizza DICOM.app/Contents/Info.plist"
          plutil -replace CFBundleLocalizations -json '["it"]' "dist/Anonimizza DICOM.app/Contents/Info.plist"
          codesign --force --deep -s - "dist/Anonimizza DICOM.app"
          "dist/Anonimizza DICOM.app/Contents/MacOS/Anonimizza DICOM" --selftest esempio
          cd dist && ditto -c -k --keepParent "Anonimizza DICOM.app" "Anonimizza DICOM-mac.zip" && rm -rf "Anonimizza DICOM.app" "Anonimizza DICOM"
      - uses: actions/upload-artifact@v4
        with:
          name: anonimizza-dicom-${{ runner.os }}
          path: dist/
```

- [ ] **Step 2: Crea `.gitignore`**

```text
.venv/
build/
dist/
__pycache__/
*.spec
esempio/
artifacts/
.DS_Store
```

- [ ] **Step 3: Crea `README.md`**

```markdown
# Anonimizza e Masterizza DICOM

1. Inserisci il CD dell'esame e premi **Leggi CD** (oppure **Scegli cartella…**).
2. Controlla il nome del paziente e le serie. Le serie con ⚠ possono avere il nome scritto
   nelle immagini: sono escluse; includile solo dopo averle controllate.
3. Scrivi il nuovo nome (es. `CASO_01`). Spunta «Mantieni età, sesso, peso e date» se servono:
   è necessario per PET (SUV) e studi dinamici (perfusione, RM dinamiche), altrimenti orari e peso vanno persi.
4. **Masterizza**: il CD originale viene espulso, inserisci un CD/DVD vuoto e premi OK.
   Oppure **Salva in cartella…** per una chiavetta: crea una cartella `<NOME>` con il DICOMDIR dentro;
   se un viewer vuole il DICOMDIR nella radice della chiavetta, copia il CONTENUTO di quella cartella nella radice.

Prima di masterizzare il programma cerca nei dati anonimi nome, ID (anche codice fiscale),
data di nascita e accession number originali: se li trova li mostra e chiede conferma
(una parola normale, es. il cognome «Testa» in «TC TESTA», si può accettare).
Non controlla il testo dentro le immagini.
Se compare «File illeggibili» il CD è rovinato: l'esame potrebbe essere incompleto.

## Requisiti
- Mac con chip Apple (M1 o successivi). Per i Mac Intel l'app va compilata su un Mac Intel con lo stesso comando.
- Windows 8.1 o successivo.

## Primo avvio
- Mac (macOS 15 e successivi): prova ad aprire l'app, poi Impostazioni di Sistema → Privacy e sicurezza → «Apri comunque» (app non firmata).
- Windows: SmartScreen → «Ulteriori informazioni» → «Esegui comunque».
- Mac: se «Leggi CD» non trova il disco, concedi l'accesso ai «Volumi rimovibili» in
  Impostazioni di Sistema → Privacy e sicurezza → File e cartelle.
- Su PC ospedalieri l'antivirus o le policy IT possono bloccare l'app o la masterizzazione.
- Controllo dell'installazione: `"Anonimizza DICOM" --selftest <cartella con un esame>` (Mac: dentro `Contents/MacOS/`); stampa SELFTEST e termina con 0 se è tutto a posto.

## Sviluppo
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pyinstaller==6.11.1
    .venv/bin/python test_anon.py
    .venv/bin/python app.py

Su Windows: `python -m venv .venv`, poi `.venv\Scripts\pip install -r requirements.txt pyinstaller==6.11.1`
e `.venv\Scripts\python test_anon.py` / `.venv\Scripts\python app.py`.

## Compilazione
- Mac (compila solo per l'architettura del Mac che la esegue):

      pyinstaller --noconfirm --windowed --osx-bundle-identifier local.anonimizza-dicom --name "Anonimizza DICOM" app.py
      plutil -replace CFBundleDevelopmentRegion -string it "dist/Anonimizza DICOM.app/Contents/Info.plist"
      plutil -replace CFBundleLocalizations -json '["it"]' "dist/Anonimizza DICOM.app/Contents/Info.plist"
      codesign --force --deep -s - "dist/Anonimizza DICOM.app"

- Windows (su un PC Windows): `pyinstaller --noconfirm --onefile --windowed --disable-windowed-traceback --name "Anonimizza DICOM" app.py`
- Entrambe automaticamente: GitHub Actions (`.github/workflows/build.yml`), file in «Artifacts».
```

- [ ] **Step 4: Con il consenso dell'utente, crea il repository privato e carica il codice**

Run: `git init && git add . && git commit -m "Anonimizza e Masterizza DICOM" && gh repo create anonimizza-dicom --private --source . --push`
Expected: URL del repository; in «Actions» parte il workflow `build`.

- [ ] **Step 5: Scarica i risultati**

Run: `gh run watch` e poi `gh run download --dir artifacts`
Expected: `artifacts/anonimizza-dicom-Windows/Anonimizza DICOM.exe` e `artifacts/anonimizza-dicom-macOS/Anonimizza DICOM-mac.zip`; i test sono verdi su entrambi i sistemi.

---

### Task 7: Prova con hardware reale (con l'utente)

Non è automatizzabile: servono un CD vero e un masterizzatore.

- [ ] **Step 1: Mac + CD reale.** «Leggi CD» → il nome del paziente compare, le serie dose/screenshot hanno ⚠ e non sono spuntate.
- [ ] **Step 2: Salva in cartella** e apri il risultato in un viewer (Horos, OsiriX, RadiAnt, Weasis): immagini corrette, nome = pseudonimo, date come da interruttore.
- [ ] **Step 3: Mac + masterizzatore.** Inserisci un disco vuoto e lancia `drutil status`: verifica che compaiano `Space Free:` con `blocks:` e `Writability:` con `blank`. Se il formato è diverso, aggiorna `_parse_drutil_free` e il suo test con l'output reale.
- [ ] **Step 4: Prova senza scrivere:** avvia l'app con `BURN_TEST=1 .venv/bin/python app.py` e masterizza. `drutil` simula la scrittura e deve finire con «Test complete». Se il messaggio di successo reale è diverso, aggiorna `_mac_burn` e il suo test.
- [ ] **Step 5: Masterizzazione vera dall'app**, poi lettura del disco su un PC Windows con un viewer.
- [ ] **Step 6: Windows: avvia l'`.exe`**, leggi il CD, masterizza, rileggi il disco su Mac.
