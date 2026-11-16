# Anonimizza e Masterizza DICOM — Design

Data: 2026-11-16
Stato: design approvato in brainstorming, spec in revisione

## Obiettivo

App desktop per Windows e Mac, usata da medici e tecnici sui PC di reparto.
Legge un esame DICOM da CD o da cartella (con o senza DICOMDIR, cartelle annidate),
lo anonimizza e lo masterizza su CD/DVD, oppure lo salva in una cartella.
Deve essere rapida e usabile senza istruzioni: una sola finestra, tre passi.

## Decisioni prese

| Tema | Decisione |
|---|---|
| Dati clinici | Interruttore per esame "Mantieni età, sesso e date", spento di default |
| Nome sostitutivo | Scritto dall'utente; se vuoto, automatico `ANON-AAAAMMGG-XXXX` |
| Contenuto del disco | Solo DICOM + DICOMDIR, nessun viewer |
| Supporti | CD (700 MB) e DVD (4,7 GB), scelta in base al disco inserito |
| Testo nei pixel | Serie a rischio escluse di default e segnalate, reinseribili con spunta |
| Utenti | Colleghi/tecnici: app autonoma `.app` (Mac) e `.exe` (Windows), niente Python da installare |
| Tecnologia | Python 3.12 + pydicom + Tkinter; masterizzazione con strumenti del sistema operativo |

## Architettura

Cinque file, un compito ciascuno.

| File | Compito | Dipende da |
|---|---|---|
| `scanner.py` | Trova i file DICOM, li raggruppa in serie, segna le serie a rischio | pydicom |
| `anonymizer.py` | Anonimizza, scrive cartella + DICOMDIR, controllo fughe di dati | pydicom |
| `burner.py` | Elenca masterizzatori, legge capienza disco, masterizza, espelle | drutil (Mac), comtypes + IMAPI2 (Windows) |
| `app.py` | Finestra Tkinter, collega i pezzi, lavoro pesante in thread separato | i tre sopra |
| `test_anon.py` | Test automatici su scanner e anonimizzatore | pydicom (file di esempio inclusi) |

Dipendenze: `pydicom` (Python puro), `comtypes` solo su Windows. Nessun numpy: i pixel non vengono mai decodificati.
Build: PyInstaller (solo in fase di compilazione).

### Interfacce

```python
# scanner.py
@dataclass
class Series:
    patient_name: str; patient_id: str; birth_date: str; accession: str
    study_uid: str; study_date: str; study_desc: str
    series_uid: str; series_number: str; series_desc: str; modality: str
    files: list[Path]; size_bytes: int
    risk: str | None          # motivo del rischio testo-nei-pixel, None se sicura

def find_dicom_sources() -> list[Path]   # volumi ottici/montati con DICOMDIR
def scan(root: Path, progress) -> tuple[list[Series], int]   # serie, n. file saltati

# anonymizer.py
def anonymize(series: list[Series], out_root: Path, new_name: str,
              keep_clinical: bool, progress) -> Report
def check_leaks(out_root: Path, series: list[Series]) -> list[str]   # vuota = ok

# burner.py
def list_drives() -> list[Drive]
def media_free_bytes(drive: Drive) -> int | None     # None = nessun disco scrivibile
def burn(folder: Path, drive: Drive, progress) -> None   # solleva BurnError
```

`progress` è una funzione `(fatto, totale, testo)` chiamata dal thread di lavoro;
`app.py` la inoltra alla UI tramite `queue` + `root.after`.

## Flusso

1. **Sorgente.** "Leggi CD" chiama `find_dicom_sources()`: con un solo risultato lo apre,
   con più di uno chiede quale, con nessuno suggerisce "Scegli cartella…".
   `scan()` percorre tutte le sottocartelle e legge solo l'intestazione (`stop_before_pixels`).
   Il DICOMDIR originale non viene usato: si leggono i file, più affidabile.
   File senza marcatore `DICM` all'offset 128 (viewer, exe, html, pdf, autorun) sono saltati e contati.
2. **Anteprima.** Albero studio → serie con spunta, numero immagini, MB, ⚠ per le serie a rischio
   (non spuntate). Il nome del paziente originale compare solo a schermo per verificare il CD.
   Se il CD contiene più pazienti, si possono spuntare studi di un solo paziente per volta.
3. **Anonimizzazione** in una cartella temporanea locale (`tempfile.mkdtemp`), più processi in parallelo.
4. **Controllo fughe** (`check_leaks`): se trova qualcosa, si blocca e mostra file e campo.
5. **Destinazione.** Masterizza (dopo il controllo di capienza) oppure "Salva in cartella…".
6. **Pulizia.** La cartella temporanea viene cancellata a fine lavoro e alla chiusura dell'app (`try/finally`).

## Regole di anonimizzazione

Applicate a ogni dataset, in modo ricorsivo (anche dentro le sequenze):

1. **Tag privati** rimossi (`remove_private_tags`, ricorsivo).
2. **Overlay e curve** (gruppi `60xx`, `50xx`) rimossi.
3. **Nomi di persona** (VR `PN`): tutti rimossi, poi `PatientName` = nome scelto.
4. **Identificativi**: rimossi i campi della PS3.15 Table E.1-1 (Basic Application Level
   Confidentiality Profile). La lista completa sta in `anonymizer.py`; tra questi:
   altri ID e nomi paziente, data e ora di nascita, indirizzo, telefono, accession number,
   istituto, indirizzo e reparto, nome stazione, numero di serie dell'apparecchio,
   `RequestAttributesSequence`, ID di procedura, commenti (`ImageComments`, `PatientComments`,
   `AdditionalPatientHistory` ecc.), `SourceApplicationEntityTitle` nel file meta.
5. **UID** (VR `UI`): ogni UID che non è un UID standard DICOM (classi SOP, sintassi di trasferimento)
   viene rimappato con `generate_uid(entropy_srcs=[sale_sessione, uid_originale])`.
   La rimappatura è deterministica nella sessione, quindi studi, serie e riferimenti restano collegati
   anche tra processi diversi. `MediaStorageSOPInstanceUID` segue il nuovo `SOPInstanceUID`.
6. **Interruttore spento** ("Mantieni età, sesso e date"): tutti i campi `DA`, `DT`, `TM` svuotati;
   rimossi `PatientAge`, `PatientSex`, `PatientSize`, `PatientWeight`.
   **Interruttore acceso**: date, orari, età, sesso, peso, altezza restano. La data di nascita è sempre rimossa.
7. **Descrizioni conservate sempre**: `StudyDescription`, `SeriesDescription`, `ProtocolName`,
   `BodyPartExamined` (le copre il controllo fughe).
8. **Campi obbligatori del DICOMDIR**, che non possono restare vuoti:
   - `PatientID` = nome scelto;
   - `StudyID` = `1`;
   - `StudyDate` / `StudyTime` = `19000101` / `000000` se vuoti (interruttore spento o mancanti nell'originale);
   - `SeriesNumber` / `InstanceNumber` = numero progressivo se mancanti.
9. **Marcatura**: `PatientIdentityRemoved = YES`; `DeidentificationMethod` descrive il profilo usato
   (con o senza "Retain Longitudinal Temporal Information, Retain Patient Characteristics").
10. **Pixel**: mai toccati, copia identica senza ricompressione.

Nome scelto: ammessi lettere, numeri, spazio, `_` e `-`, massimo 64 caratteri.
Etichetta del disco: il nome in maiuscolo, caratteri non `A-Z0-9_` sostituiti da `_`, massimo 16 caratteri
(limite più restrittivo tra ISO 9660 e UDF).

## Serie a rischio testo-nei-pixel

Una serie è a rischio (esclusa di default) se almeno una di queste condizioni è vera:
- `Modality` tra `SC`, `OT`, `DOC`, `SR`, `KO`, `PR`, `US`;
- `SOPClassUID` di Secondary Capture (tutte le varianti), Encapsulated PDF/CDA o Structured Report;
- `ImageType` contiene `SCREEN SAVE` o `DOSE`;
- `SeriesDescription` contiene `DOSE`, `REPORT`, `SCREEN` o `PROTOCOL` (dose report e schede protocollo dei vendor);
- `BurnedInAnnotation = YES`.

`SECONDARY` in `ImageType` volutamente **non** è un criterio: lo hanno anche le ricostruzioni MPR/MIP
delle TC, che vanno tenute.

Il motivo è mostrato accanto alla serie ("dose report", "ecografia", "screenshot"…).

## Controllo fughe di dati (`check_leaks`)

Dopo l'anonimizzazione rilegge ogni file prodotto e il DICOMDIR, percorrendo tutti i valori dei tag
(sequenze comprese) **tranne `PixelData`**, e controlla anche i nomi dei file.
Cerca, senza distinzione tra maiuscole e minuscole, gli identificativi originali di tutti i pazienti di partenza:
- ogni parte del nome lunga almeno 3 caratteri (`ROSSI^MARIO` → `ROSSI`, `MARIO`);
- `PatientID` e `AccessionNumber` lunghi almeno 3 caratteri;
- data di nascita (`AAAAMMGG`).

Una corrispondenza blocca la masterizzazione. Limite noto: parti di nome di 1-2 lettere non vengono cercate,
perché causerebbero falsi positivi.

## Struttura del disco

```
<ETICHETTA>/            ← nome cartella = etichetta del volume
  DICOMDIR
  PT000000/ST000000/SE000000/IM000000 …   (layout predefinito di pydicom FileSet)
```

Tutti i nomi sono di massimo 8 caratteri maiuscoli senza estensione, quindi compatibili ISO 9660 livello 1
come richiesto da DICOM PS3.12. File system: ISO 9660 + Joliet + UDF (bridge), senza HFS+.
Stima dello spazio: somma dei file arrotondati a settori da 2048 byte, più 2 MB.

## Masterizzazione

**Mac**: `drutil -drive N burn -nohfsplus -eject <cartella>` (ISO 9660, Joliet e UDF sono già attivi di default;
la verifica dopo la scrittura resta attiva). Capienza letta da `drutil status`.
Simulazione senza scrittura: `drutil burn -test` (usata solo nei test manuali).

**Windows**: IMAPI2 via `comtypes` dentro il processo Python:
`MsftFileSystemImage` (`FileSystemsToCreate = 7`, cioè ISO 9660 + Joliet + UDF, `Root.AddTree`)
→ `MsftDiscFormat2Data.Write(image_stream)`; capienza da `FreeSectorsOnMedia × 2048`;
progresso dagli eventi `Update`.
Scelto al posto di uno script PowerShell perché sui PC ospedalieri PowerShell è spesso in
Constrained Language Mode, che blocca gli oggetti COM.

## Gestione errori

| Situazione | Comportamento |
|---|---|
| File illeggibili o non DICOM | Saltati, contati nel riepilogo |
| Nessun CD DICOM trovato | Messaggio + invito a "Scegli cartella…" |
| Nessun masterizzatore / nessun disco vuoto | Messaggio + proposta "Salva in cartella…" |
| Esame più grande del disco | Mostra MB necessari e disponibili, chiede un DVD |
| Masterizzazione fallita | Errore del sistema a schermo; i file anonimi restano pronti per riprovare |
| Controllo fughe fallito | Blocco, elenco file e campo; nessuna masterizzazione |
| Chiusura dell'app durante il lavoro | Conferma, poi cancellazione della cartella temporanea |

Nessun log con dati del paziente. Nessuna tabella di corrispondenza tra originale e pseudonimo.

## Test

`test_anon.py` (assert semplici, nessun framework), con i file di esempio inclusi in pydicom
(`CT_small.dcm`, `MR_small.dcm`, il set `dicomdirtests` con DICOMDIR, una Secondary Capture):
- lo scanner trova le serie e salta un file non DICOM aggiunto;
- dopo l'anonimizzazione `check_leaks` è vuoto e nessun valore originale di nome, ID o nascita è presente;
- le immagini di una serie condividono lo stesso nuovo `SeriesInstanceUID` e `StudyInstanceUID`;
- il DICOMDIR prodotto si rilegge con `FileSet` e referenzia tutti i file;
- interruttore spento: nessuna data e nessuna età; acceso: data dello studio ed età presenti, nascita assente;
- la Secondary Capture è marcata a rischio;
- `check_leaks` trova un nome lasciato apposta in `SeriesDescription`.

Masterizzazione: prova manuale con masterizzatore reale su Mac (prima con `-test`) e su Windows.

## Distribuzione

- Mac: `pyinstaller --windowed --name "Anonimizza DICOM" app.py` → `.app`, compilata su Mac.
- Windows: `pyinstaller --onefile --windowed app.py` → `.exe`, compilato su Windows
  (PyInstaller non compila per un altro sistema). Workflow GitHub Actions opzionale, se il codice va su GitHub.
- `multiprocessing.freeze_support()` in `app.py`, necessario con PyInstaller.
- App non firmate:
  - Mac: primo avvio con clic destro → Apri.
  - Windows: SmartScreen, "Esegui comunque".
  - L'antivirus dell'ospedale può bloccare l'`.exe`: potrebbe servire un'eccezione da parte dell'IT.
- Da verificare con l'IT: se è consentito usare software esterno e se la masterizzazione è disabilitata da policy.

## Modifiche decise nel piano

Il piano (`docs/superpowers/plans/2026-11-16-dicom-anon-burner.md`, sezione "Cambiamenti rispetto alla spec")
prevale su questa spec dove le due differiscono. In sintesi:
- DICOMDIR scritto in proprio, perché `FileSet` è quadratico;
- un solo processo;
- date fittizie invece di vuote;
- pywin32 invece di comtypes;
- progresso della masterizzazione indeterminato;
- PC tenuto sveglio durante la masterizzazione;
- espulsione del CD originale prima di chiedere il disco vuoto;
- foto ed endoscopie tra le serie a rischio;
- PDF incapsulati sempre esclusi.

## Fuori scope (per ora)

- Anteprima delle immagini delle serie a rischio (servirebbero decoder JPEG e numpy, +40 MB).
- Oscuramento dei pixel.
- Viewer sul disco.
- Ricompressione per far stare esami grandi su CD.
- Firma del codice.
