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
