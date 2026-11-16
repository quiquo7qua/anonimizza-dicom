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
import webbrowser
from itertools import groupby
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import anonymizer as an
import burner
import scanner
import updater

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
        updater.cleanup_old()  # Windows: l'.exe precedente rimasto dopo un aggiornamento
        self.title("Anonimizza e Masterizza DICOM" + ("" if updater.VERSION == "dev" else f" {updater.VERSION}"))
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
        self.dialog_open = False  # scelta cartella aperta: niente domande sopra
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
        if updater.enabled():
            threading.Thread(target=self._check_update, daemon=True).start()

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
                else:  # "done": fine di un lavoro; "call": avviso dal thread, non tocca lo stato di lavoro
                    callback, value = args
                    if kind == "done":
                        self._set_busy(False)
                    try:
                        callback(value)
                    except Exception as exc:  # un errore qui non deve fermare il ciclo
                        self.show_error(exc)
        except queue.Empty:
            pass
        finally:
            self._poll_id = self.after(100, self._poll)

    # ---------- aggiornamenti ----------

    def _check_update(self):  # thread di sfondo: ogni errore è già un None
        version = updater.latest_version()
        if version and updater.is_newer(version, updater.VERSION):
            self.events.put(("call", self._offer_update, version))

    def _ask_folder(self, **options) -> str:
        self.dialog_open = True
        try:
            return filedialog.askdirectory(**options)
        finally:
            self.dialog_open = False

    def _offer_update(self, version: str):
        if self.busy or self.series or self.dialog_open:  # niente domande a metà lavoro: per questa sessione si salta
            return
        try:
            reason = updater.can_replace(updater.target())
        except updater.UpdateError as exc:
            reason = str(exc).rstrip(".").lower()
        if reason:  # inutile promettere un aggiornamento che non può riuscire
            if messagebox.askyesno(
                "Aggiornamento",
                f"È disponibile la versione {version}, ma questa copia non può aggiornarsi da sola ({reason}). "
                "Aprire la pagina di download?",
            ):
                webbrowser.open(updater.RELEASE_PAGE)
            return
        if not messagebox.askyesno(
            "Aggiornamento",
            f"È disponibile la versione {version} (attuale {updater.VERSION}). Aggiornare ora? "
            "Ci vogliono circa 20 secondi. L'app si chiuderà e si riaprirà.",
        ):
            return

        def work(progress):
            progress(0, 0, "Aggiornamento in corso")
            return updater.install(version)

        def failed(exc):
            self.show_error(updater.UpdateError(updater.explain(exc)))
            if not isinstance(exc, updater.UpdateError) and updater.explain(exc) == updater.FAILED:
                webbrowser.open(updater.RELEASE_PAGE)  # il motivo è ignoto: si scarica a mano

        def done(path):
            updater.relaunch(path)
            self.after(0, self.on_close)  # fuori da _poll, che sta ancora girando

        self.run_bg(work, done, failed)

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
            chosen = self._ask_folder(
                title="Ho trovato più dischi con esami: scegli la cartella giusta", initialdir=str(root)
            )
            if not chosen:
                return
            root = Path(chosen)
        self.load(root, cd=True)

    def on_choose_folder(self):
        folder = self._ask_folder(title="Cartella con l'esame DICOM")
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
        folder = self._ask_folder(title="Dove salvo l'esame anonimo?")
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
            folder = self._ask_folder(title="Dove salvo l'esame anonimo?")
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
    if sys.platform == "darwin":  # l'app compilata non ha un suo pacchetto di CA: senza, gli aggiornamenti non funzionerebbero mai
        try:
            if updater.context().cert_store_stats()["x509_ca"] == 0:
                raise OSError("nessuna CA")
        except Exception as exc:
            print(f"SELFTEST: certificati di sistema non disponibili ({type(exc).__name__})")
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


def aggiorna_cli() -> int:
    """Controllo e installazione senza finestre e senza riavvio. 0 = aggiornata, 2 = già all'ultima, 1 = errore."""
    if not updater.enabled():
        print("AGGIORNA: non disponibile (versione di sviluppo o aggiornamenti disattivati)")
        return 1
    version = updater.latest_version()
    if not version:
        print("AGGIORNA: impossibile controllare gli aggiornamenti (rete?)")
        return 1
    if not updater.is_newer(version, updater.VERSION):
        print(f"AGGIORNA: già all'ultima versione ({updater.VERSION})")
        return 2
    try:
        reason = updater.can_replace(updater.target())
        if reason:
            print(f"AGGIORNA: {reason[0].upper()}{reason[1:]}.")
            return 1
        updater.install(version)
    except Exception as exc:
        print(f"AGGIORNA: {updater.explain(exc)}")
        return 1
    print(f"AGGIORNA: aggiornata alla versione {version}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"] and len(sys.argv) > 2:
        sys.exit(selftest(sys.argv[2]))
    if sys.argv[1:2] == ["--aggiorna"]:
        sys.exit(aggiorna_cli())
    App().mainloop()
