# Aggiornamenti automatici — Design

Data: 2026-11-16
Stato: design approvato dall'utente

## Decisioni

| Tema | Decisione |
|---|---|
| Fonte | Release di GitHub del repository `quiquo7qua/anonimizza-dicom`, reso pubblico (contiene solo codice) |
| Modalità | All'avvio, se c'è una versione nuova: «Aggiornare ora?» → Sì: scarica, verifica, sostituisce, riavvia |
| Protezione | Solo HTTPS da questo repository + impronta SHA-256 (`SHA256SUMS.txt` della stessa release) + 2FA sull'account GitHub |
| Implementazione | Modulo `updater.py`, solo libreria standard, nessuna nuova dipendenza |

## Versione

- `version.py` contiene `VERSION = "dev"` nel repository.
- Il workflow, solo quando parte da un tag `vX.Y.Z`, riscrive `version.py` con `VERSION = "X.Y.Z"` prima di compilare. Su Mac imposta anche `CFBundleShortVersionString` e `CFBundleVersion` nell'Info.plist.
- Il controllo degli aggiornamenti è attivo solo nell'app compilata (`sys.frozen`) e con una versione diversa da `"dev"`.

## Rilascio

- Push di un tag `v*` → il workflow esegue test, compilazione e autoverifica su Windows e su macOS. Poi un job `release` crea la GitHub Release del tag con tre file:
  - `Anonimizza-DICOM-Windows.exe`
  - `Anonimizza-DICOM-Mac.zip`
  - `SHA256SUMS.txt` (formato `sha256sum`: `<hash>  <nome>`)
- I push senza tag continuano a produrre solo gli artifact, senza release.

## Controllo all'avvio

- Thread in background, senza bloccare la finestra:
  - `GET https://github.com/<repo>/releases/latest` (timeout 5 s);
  - l'URL finale dopo il redirect (`…/releases/tag/vX.Y.Z`) dà la versione.
- Non si usa l'API REST: limite di 60 richieste all'ora per indirizzo IP, condiviso da tutti i PC dietro la stessa rete dell'ospedale.
- Qualunque errore (rete, proxy, formato) → nessun messaggio.
- Versione più recente maggiore di quella attuale (confronto numerico X.Y.Z) → la domanda compare solo se l'app è ferma e nessun esame è caricato. Altrimenti si salta per questa sessione.
- Domanda: «È disponibile la versione X (attuale Y). Aggiornare ora? Ci vogliono circa 20 secondi.»

## Installazione

1. Scarica `SHA256SUMS.txt` e l'asset della piattaforma da `https://github.com/<repo>/releases/download/vX.Y.Z/<nome>`. Se l'impronta non corrisponde: messaggio, il file scaricato viene cancellato, l'app resta com'è.
2. **Windows:**
   - l'asset si scarica accanto all'`.exe` come `<exe>.new` (stesso volume);
   - poi `<exe>` → `<exe>.old`, `<exe>.new` → `<exe>`, avvio del nuovo `.exe` e uscita;
   - all'avvio successivo si cancella `<exe>.old`;
   - se la sostituzione fallisce a metà, si ripristina l'originale.
3. **Mac:**
   - lo zip si estrae con `ditto -x -k` in una cartella temporanea;
   - il bundle in uso (`sys.executable` → `…/X.app`) viene spostato in un backup temporaneo e il nuovo bundle prende il suo posto;
   - poi `open -n <nuovo .app>` e uscita;
   - in caso di errore si ripristina il backup.
4. **Casi in cui l'app non si può sostituire:** cartella non scrivibile, oppure Mac avviato in «App Translocation» (percorso con `/AppTranslocation/`, quando l'app gira da Download senza essere stata spostata). Messaggio con istruzioni («sposta l'app in Applicazioni» / «scarica a mano») e apertura della pagina della release nel browser.

## Riga di comando

`--aggiorna`: controllo e installazione senza finestre, per prove e per gli script dell'IT. Codici di uscita:

| Codice | Significato |
|---|---|
| 0 | aggiornata (il nuovo `.exe`/`.app` è al suo posto, senza riavvio) |
| 2 | già all'ultima versione |
| 1 | errore |

## Test

- Unitari, con rete finta:
  - confronto delle versioni;
  - versione letta dall'URL di redirect;
  - impronta sbagliata → nessuna modifica;
  - sostituzione su Windows con file finti (`.exe` finto in una cartella temporanea), compreso il ripristino in caso di errore;
  - sostituzione su Mac con bundle finti;
  - casi «non sostituibile»;
  - pulizia di `.old`;
  - nessun controllo se l'app non è compilata o è in versione `dev`.
- End-to-end:
  - Mac, in locale: release v1.0.0, installazione, release v1.0.1, `--aggiorna` sulla v1.0.0 → bundle sostituito, nessuna quarantena, `--selftest` ok.
  - Windows: workflow manuale `prova-aggiornamento` che scarica l'`.exe` della release precedente, lancia `--aggiorna` e confronta l'impronta del file risultante con `SHA256SUMS.txt` dell'ultima release.
