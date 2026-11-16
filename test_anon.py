"""Test di scanner, anonimizzatore e parser del masterizzatore. Avvio: python test_anon.py"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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


# ---------- aggiornamenti automatici (rete finta: nessun test tocca internet) ----------


class _FakeResp:
    """Risposta finta di urlopen: read(), geturl() e context manager."""

    def __init__(self, data: bytes = b"", url: str = ""):
        self._data, self._url = io.BytesIO(data), url

    def read(self, n=-1):
        return self._data.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _release_opener(version: str, files: dict, sums: dict | None = None):
    """Opener finto di una release: serve i file e SHA256SUMS.txt (calcolato, o `sums` per falsarlo)."""
    import urllib.error

    import updater

    listed = sums if sums is not None else {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    served = dict(files)
    served["SHA256SUMS.txt"] = "".join(f"{h}  {n}\n" for n, h in listed.items()).encode()
    base = f"https://github.com/{updater.REPO}/releases/download/v{version}/"
    asked = []

    def opener(url, timeout=None):
        asked.append(url)
        if not url.startswith(base) or url[len(base):] not in served:
            raise urllib.error.URLError("non trovato")
        return _FakeResp(served[url[len(base):]], url)

    opener.asked = asked
    return opener


def test_updater_versions():
    import updater

    assert updater.parse_version("1.2.0") == (1, 2, 0)
    assert updater.parse_version("10.0.1") == (10, 0, 1)
    for bad in ("dev", "", "1.2.x", "v1.2.0", "1..2", None):
        assert updater.parse_version(bad) is None, bad
    assert updater.is_newer("1.2.0", "1.1.9")
    assert updater.is_newer("1.10.0", "1.9.0")  # confronto numerico, non di testo
    assert updater.is_newer("2.0.0", "1.99.99")
    assert not updater.is_newer("1.2.0", "1.2.0")
    assert not updater.is_newer("1.1.0", "1.2.0")
    assert not updater.is_newer("1.2.0", "dev") and not updater.is_newer("boh", "1.0.0")


def test_updater_latest_version():
    import urllib.error

    import updater

    def final(url):
        return lambda u, timeout=None: _FakeResp(url=url)

    base = f"https://github.com/{updater.REPO}/releases/tag/v"
    assert updater.latest_version(final(base + "1.2.0")) == "1.2.0"
    assert updater.latest_version(final(f"https://github.com/{updater.REPO}/releases")) is None  # nessuna release
    assert updater.latest_version(final(base + "beta")) is None
    seen = {}
    updater.latest_version(lambda u, timeout=None: seen.update(url=u, timeout=timeout) or _FakeResp(url=base + "1.0.0"))
    assert seen == {"url": f"https://github.com/{updater.REPO}/releases/latest", "timeout": 5}, seen

    def boom(exc):
        def opener(u, timeout=None):
            raise exc

        return opener

    for exc in (urllib.error.URLError("rete"), TimeoutError(), OSError("proxy"), ValueError("x")):
        assert updater.latest_version(boom(exc)) is None, exc


def test_updater_download_verified(tmp: Path):
    import updater

    data = b"contenuto dell'app" * 1000
    with mock.patch.object(sys, "platform", "win32"):
        name = updater.ASSETS["win32"]
        dest = tmp / "nuovo.bin"
        opener = _release_opener("1.2.0", {name: data})
        assert updater.download_verified("1.2.0", dest, opener) == dest
        assert dest.read_bytes() == data
        assert opener.asked[0].endswith("/v1.2.0/SHA256SUMS.txt") and opener.asked[1].endswith("/v1.2.0/" + name)
        # impronta sbagliata: file cancellato
        bad = tmp / "cattivo.bin"
        opener = _release_opener("1.2.0", {name: data}, sums={name: "0" * 64})
        try:
            updater.download_verified("1.2.0", bad, opener)
            assert False, "doveva rifiutare l'impronta sbagliata"
        except updater.UpdateError as exc:
            assert "impronta" in str(exc)
        assert not bad.exists()
        # asset non elencato in SHA256SUMS.txt
        missing = tmp / "manca.bin"
        opener = _release_opener("1.2.0", {name: data}, sums={"altro.zip": "a" * 64})
        try:
            updater.download_verified("1.2.0", missing, opener)
            assert False, "doveva rifiutare l'asset non elencato"
        except updater.UpdateError:
            pass
        assert not missing.exists()
        # rete assente (asset o impronte): errore e nessun file
        gone = tmp / "rete.bin"
        try:
            updater.download_verified("9.9.9", gone, _release_opener("1.2.0", {name: data}))
            assert False
        except OSError:  # URLError è un OSError
            pass
        assert not gone.exists()
    with mock.patch.object(sys, "platform", "linux"):
        try:
            updater.download_verified("1.2.0", tmp / "x", _release_opener("1.2.0", {}))
            assert False
        except updater.UpdateError as exc:
            assert "non supportato" in str(exc)


def _fail_move_in(app: Path):
    """os.replace che nega sempre di mettere al suo posto la versione nuova; il ripristino della vecchia riesce."""
    real = os.replace

    def replace(src, dst):
        if Path(dst) == app and not Path(src).name.endswith((".old", ".vecchia")):
            raise PermissionError("bloccato")
        return real(src, dst)

    return replace


def test_updater_install_windows(tmp: Path):
    import updater

    exe = tmp / "Anonimizza DICOM.exe"
    exe.write_bytes(b"vecchio")
    new = b"nuovo" * 500
    opener = _release_opener("1.2.0", {updater.ASSETS["win32"]: new})
    with mock.patch.object(sys, "platform", "win32"):
        assert updater.install("1.2.0", opener, path=exe) == exe
        assert exe.read_bytes() == new
        assert (tmp / "Anonimizza DICOM.exe.old").read_bytes() == b"vecchio"
        assert sorted(p.name for p in tmp.iterdir()) == ["Anonimizza DICOM.exe", "Anonimizza DICOM.exe.old"]  # nessun .new
        # un .old rimasto da un aggiornamento precedente non blocca il successivo
        assert updater.install("1.2.0", opener, path=exe) == exe
        assert (tmp / "Anonimizza DICOM.exe.old").read_bytes() == new

    # seconda rinomina fallita: si ripristina l'originale e non resta nulla
    exe2 = tmp / "b" / "App.exe"
    exe2.parent.mkdir()
    exe2.write_bytes(b"originale")
    with mock.patch.object(sys, "platform", "win32"), mock.patch.object(updater.os, "replace", _fail_move_in(exe2)), \
            mock.patch.object(updater.time, "sleep", lambda s: None):
        try:
            updater.install("1.2.0", opener, path=exe2)
            assert False
        except PermissionError:
            pass
    assert exe2.read_bytes() == b"originale"
    assert sorted(p.name for p in exe2.parent.iterdir()) == ["App.exe"]

    # impronta sbagliata: l'exe non si tocca
    exe3 = tmp / "c" / "App.exe"
    exe3.parent.mkdir()
    exe3.write_bytes(b"originale")
    bad = _release_opener("1.2.0", {updater.ASSETS["win32"]: new}, sums={updater.ASSETS["win32"]: "0" * 64})
    with mock.patch.object(sys, "platform", "win32"):
        try:
            updater.install("1.2.0", bad, path=exe3)
            assert False
        except updater.UpdateError:
            pass
    assert exe3.read_bytes() == b"originale" and [p.name for p in exe3.parent.iterdir()] == ["App.exe"]


def _bundle(folder: Path, content: bytes) -> Path:
    exe = folder / "Anonimizza DICOM.app" / "Contents" / "MacOS" / "Anonimizza DICOM"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(content)
    return folder / "Anonimizza DICOM.app"


def _fake_ditto(calls):
    """subprocess.run finto: `ditto -x -k <zip> <cartella>` con zipfile."""

    def run(cmd, **kw):
        calls.append(cmd)
        assert cmd[:3] == ["ditto", "-x", "-k"] and kw.get("check"), (cmd, kw)
        with zipfile.ZipFile(cmd[3]) as z:
            z.extractall(cmd[4])

    return run


def _zip_of(app_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Anonimizza DICOM.app/Contents/MacOS/Anonimizza DICOM", app_bytes)
        z.writestr("Anonimizza DICOM.app/Contents/Info.plist", b"plist")
    return buf.getvalue()


def test_updater_install_mac(tmp: Path):
    import updater

    apps = tmp / "Applications"
    apps.mkdir()
    app = _bundle(apps, b"vecchia")
    asset = updater.ASSETS["darwin"]
    opener = _release_opener("1.2.0", {asset: _zip_of(b"nuova")})
    calls, moves, real_replace = [], [], os.replace
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(updater.subprocess, "run", _fake_ditto(calls)), \
            mock.patch.object(updater.os, "replace", lambda s, d: moves.append((Path(s), Path(d))) or real_replace(s, d)):
        assert updater.install("1.2.0", opener, path=app) == app
    assert len(calls) == 1
    # il backup resta nella cartella dell'app (stesso genitore, nascosto): spostarlo altrove richiederebbe permessi sul bundle
    assert moves[0][0] == app and moves[0][1].parent == apps and moves[0][1].name.startswith("."), moves
    assert (app / "Contents" / "MacOS" / "Anonimizza DICOM").read_bytes() == b"nuova"
    assert [p.name for p in apps.iterdir()] == ["Anonimizza DICOM.app"]  # nessun temporaneo né backup

    # seconda mossa fallita: si ripristina il bundle vecchio e si pulisce
    app2 = _bundle(tmp, b"originale")
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(updater.subprocess, "run", _fake_ditto([])), \
            mock.patch.object(updater.os, "replace", _fail_move_in(app2)), \
            mock.patch.object(updater.time, "sleep", lambda s: None):
        try:
            updater.install("1.2.0", opener, path=app2)
            assert False
        except PermissionError:
            pass
    assert (app2 / "Contents" / "MacOS" / "Anonimizza DICOM").read_bytes() == b"originale"
    assert sorted(p.name for p in tmp.iterdir() if p.name.startswith(".") or p.suffix == ".app") == ["Anonimizza DICOM.app"]

    # impronta sbagliata: bundle intatto, niente avanzi, ditto mai chiamato
    (tmp / "z").mkdir()
    app3 = _bundle(tmp / "z", b"intatta")
    bad = _release_opener("1.2.0", {asset: _zip_of(b"nuova")}, sums={asset: "0" * 64})
    calls = []
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(updater.subprocess, "run", _fake_ditto(calls)):
        try:
            updater.install("1.2.0", bad, path=app3)
            assert False
        except updater.UpdateError:
            pass
    assert not calls and [p.name for p in app3.parent.iterdir()] == ["Anonimizza DICOM.app"]
    assert (app3 / "Contents" / "MacOS" / "Anonimizza DICOM").read_bytes() == b"intatta"

    # zip senza .app dentro: errore e bundle intatto
    (tmp / "w").mkdir()
    app4 = _bundle(tmp / "w", b"intatta")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("leggimi.txt", "niente")
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(updater.subprocess, "run", _fake_ditto([])):
        try:
            updater.install("1.2.0", _release_opener("1.2.0", {asset: empty.getvalue()}), path=app4)
            assert False
        except updater.UpdateError:
            pass
    assert [p.name for p in app4.parent.iterdir()] == ["Anonimizza DICOM.app"]


def test_updater_mac_double_failure_keeps_backup(tmp: Path):
    import updater

    app = _bundle(tmp, b"originale")
    asset = updater.ASSETS["darwin"]
    real_replace, count = os.replace, []

    def replace(src, dst):  # la prima mossa (app → backup) riesce, poi tutto fallisce, anche il ripristino
        count.append(1)
        if len(count) == 1:
            return real_replace(src, dst)
        raise PermissionError("bloccato")

    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(updater.subprocess, "run", _fake_ditto([])), \
            mock.patch.object(updater.os, "replace", replace), mock.patch.object(updater.time, "sleep", lambda s: None):
        try:
            updater.install("1.2.0", _release_opener("1.2.0", {asset: _zip_of(b"nuova")}), path=app)
            assert False
        except PermissionError:
            pass
    backup = tmp / f".{app.name}.vecchia"
    assert not app.exists() and (backup / "Contents" / "MacOS" / "Anonimizza DICOM").read_bytes() == b"originale"


def test_updater_target_and_relaunch(tmp: Path):
    import updater

    with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "executable", str(tmp / "App.exe")):
        assert updater.target() == tmp / "App.exe"
    exe = str(tmp / "Applications" / "Anonimizza DICOM.app" / "Contents" / "MacOS" / "Anonimizza DICOM")
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(sys, "executable", exe):
        assert updater.target() == tmp / "Applications" / "Anonimizza DICOM.app"
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(sys, "executable", "/usr/bin/python3"):
        try:
            updater.target()
            assert False
        except updater.UpdateError:
            pass

    launched = []
    with mock.patch.object(updater.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw))):
        with mock.patch.object(sys, "platform", "win32"):
            updater.relaunch(tmp / "App.exe")
        with mock.patch.object(sys, "platform", "darwin"):
            updater.relaunch(tmp / "App.app")
    assert launched[0][0] == [str(tmp / "App.exe")]
    assert launched[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"  # il nuovo onefile non riusa le cartelle del vecchio
    assert launched[1][0] == ["open", "-n", str(tmp / "App.app")]




















def test_updater_opener_and_tls():
    import ssl

    import updater

    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen.update(req=req, timeout=timeout, context=context)
        return _FakeResp(url="https://github.com/")

    contexts = []
    real_ctx = lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # il vero create_default_context legge lo store di Windows se sys.platform è finto
    with mock.patch.object(updater, "VERSION", "1.2.0"), mock.patch.object(updater.urllib.request, "urlopen", fake_urlopen), \
            mock.patch.object(updater.ssl, "create_default_context", lambda **kw: contexts.append(kw) or real_ctx()):
        for platform in ("darwin", "win32"):
            contexts.clear()
            with mock.patch.object(sys, "platform", platform):
                updater.open_url("https://github.com/x", timeout=7)
            assert seen["req"].get_header("User-agent") == "Anonimizza-DICOM/1.2.0" and seen["timeout"] == 7
            assert seen["context"] is not None
            # su Mac il pacchetto di CA di sistema (il Portachiavi non è letto da OpenSSL); su Windows il contesto predefinito
            assert contexts == ([{"cafile": updater.CAFILE}] if platform == "darwin" else [{}]), contexts
    # il file delle CA manca: errore di rete normale (OSError), non un'eccezione strana, e nessuna richiesta
    seen.clear()
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(updater, "CAFILE", "/non/esiste/cert.pem"), \
            mock.patch.object(updater.urllib.request, "urlopen", fake_urlopen):
        try:
            updater.open_url("https://github.com/x")
            assert False
        except OSError:
            pass
        assert not seen
        assert updater.latest_version() is None
    if sys.platform == "darwin":  # il pacchetto vero di macOS contiene delle CA
        assert updater.context().cert_store_stats()["x509_ca"] > 0


def test_selftest_checks_mac_ca_bundle(tmp: Path):
    import app as gui
    import updater

    src = make_source(tmp / "src")
    with mock.patch.object(sys, "platform", "darwin"):
        with mock.patch.object(updater, "context", lambda: SimpleNamespace(cert_store_stats=lambda: {"x509_ca": 0})):
            assert gui.selftest(src) == 1
        with mock.patch.object(updater, "CAFILE", "/non/esiste/cert.pem"):
            assert gui.selftest(src) == 1
        with mock.patch.object(updater, "context", lambda: SimpleNamespace(cert_store_stats=lambda: {"x509_ca": 5})):
            assert gui.selftest(src) == 0


def test_updater_can_replace(tmp: Path):
    import updater

    folder = tmp / "Applications"
    folder.mkdir()
    app = _bundle(folder, b"x")
    with mock.patch.object(sys, "platform", "darwin"):
        assert updater.can_replace(app) is None
        reason = updater.can_replace(Path("/private/var/folders/ab/T/AppTranslocation/1234/d/Anonimizza DICOM.app"))
        assert reason and "Applicazioni" in reason and "AppTranslocation" not in reason, reason  # niente percorsi
        # anche con separatori di Windows il percorso si legge a pezzi
        assert updater.can_replace(Path("C:/a/AppTranslocation/b/X.app")) is not None
    with mock.patch.object(sys, "platform", "win32"):
        assert updater.can_replace(tmp / "App.exe") is None
    if os.name == "posix" and os.geteuid() != 0:
        folder.chmod(0o555)
        try:
            with mock.patch.object(sys, "platform", "darwin"):
                reason = updater.can_replace(app)
            assert reason and "scrivibile" in reason and str(folder) not in reason, reason
        finally:
            folder.chmod(0o755)
        app.chmod(0o555)  # bundle di un altro amministratore: la cartella è scrivibile ma il bundle no
        try:
            with mock.patch.object(sys, "platform", "darwin"):
                reason = updater.can_replace(app)
            assert reason and "scrivibile" in reason, reason
        finally:
            app.chmod(0o755)
    gone = updater.can_replace(tmp / "non-esiste" / "App.exe")
    assert gone and "scrivibile" in gone


def test_updater_cleanup_old(tmp: Path):
    import updater

    exe = tmp / "App.exe"
    exe.write_bytes(b"x")
    old, new = tmp / "App.exe.old", tmp / "AppXY12.new"
    new_mine = tmp / "App.exeab12.new"
    for f in (old, new, new_mine):
        f.write_bytes(b"y")
    with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "executable", str(exe)), \
            mock.patch.object(updater, "enabled", lambda: False):
        updater.cleanup_old()  # non attivo: non tocca nulla
        assert old.exists() and new_mine.exists()
    with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "executable", str(exe)), \
            mock.patch.object(updater, "enabled", lambda: True):
        updater.cleanup_old()
        assert sorted(p.name for p in tmp.iterdir()) == ["App.exe", "AppXY12.new"]  # solo i resti di questa app
        updater.cleanup_old()  # niente da togliere: nessun errore
        with mock.patch.object(updater.Path, "unlink", side_effect=PermissionError("in uso")):
            old.write_bytes(b"y")
            updater.cleanup_old()  # .old ancora in uso: si ignora
        assert old.exists()
    folder = tmp / "Applicazioni"
    folder.mkdir()
    app = _bundle(folder, b"x")
    junk = [folder / ".aggiornamento_ab12", folder / ".Anonimizza DICOM.app.vecchia"]
    for j in junk:
        (j / "dentro").mkdir(parents=True)
    other = folder / ".altro"
    other.mkdir()
    with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(sys, "executable", str(app / "Contents" / "MacOS" / "x")), \
            mock.patch.object(updater, "enabled", lambda: True):
        updater.cleanup_old()
    assert sorted(p.name for p in folder.iterdir()) == [".altro", "Anonimizza DICOM.app"]


def test_updater_enabled(tmp: Path):
    import updater

    exe = tmp / "App.exe"
    with mock.patch.object(updater, "VERSION", "1.2.0"), mock.patch.object(sys, "platform", "win32"), \
            mock.patch.object(sys, "executable", str(exe)), mock.patch.dict(os.environ, clear=False):
        os.environ.pop("ANONIMIZZA_NO_AGGIORNAMENTI", None)
        assert not updater.enabled()  # non compilata
        with mock.patch.object(sys, "frozen", True, create=True):
            assert updater.enabled()
            with mock.patch.object(updater, "VERSION", "dev"):
                assert not updater.enabled()
            os.environ["ANONIMIZZA_NO_AGGIORNAMENTI"] = "1"  # interruttore dell'IT
            assert not updater.enabled()
            del os.environ["ANONIMIZZA_NO_AGGIORNAMENTI"]
            (tmp / "no-aggiornamenti").write_text("")  # file accanto all'app
            assert not updater.enabled()
        (tmp / "no-aggiornamenti").unlink()
    # Mac: il file sta accanto al bundle, non dentro
    (tmp / "Apps").mkdir()
    app = _bundle(tmp / "Apps", b"x")
    with mock.patch.object(updater, "VERSION", "1.2.0"), mock.patch.object(sys, "platform", "darwin"), \
            mock.patch.object(sys, "executable", str(app / "Contents" / "MacOS" / "x")), mock.patch.object(sys, "frozen", True, create=True):
        assert updater.enabled()
        (tmp / "Apps" / "no-aggiornamenti").write_text("")
        assert not updater.enabled()
    with mock.patch.object(updater, "VERSION", "1.2.0"), mock.patch.object(sys, "platform", "darwin"), \
            mock.patch.object(sys, "executable", "/usr/bin/python3"), mock.patch.object(sys, "frozen", True, create=True):
        assert not updater.enabled()  # bundle non trovato: meglio non fare nulla
    import version

    assert version.VERSION == "dev"  # nel repository non si aggiorna mai


def test_updater_retries_locked_replace(tmp: Path):
    import updater

    exe = tmp / "App.exe"
    exe.write_bytes(b"originale")
    opener = _release_opener("1.2.0", {updater.ASSETS["win32"]: b"nuovo"})
    real, fails, slept = os.replace, [3], []

    def flaky(src, dst):  # antivirus o indicizzazione: i primi tentativi vengono negati
        if fails[0]:
            fails[0] -= 1
            raise PermissionError("in uso")
        return real(src, dst)

    with mock.patch.object(sys, "platform", "win32"), mock.patch.object(updater.os, "replace", flaky), \
            mock.patch.object(updater.time, "sleep", slept.append):
        updater.install("1.2.0", opener, path=exe)
    assert exe.read_bytes() == b"nuovo" and slept == [updater.RETRY_DELAY] * 3, slept
    assert updater.RETRY_DELAY == 0.5 and updater.RETRIES == 10


def test_updater_windows_second_instance(tmp: Path):
    import updater

    exe = tmp / "App.exe"
    exe.write_bytes(b"originale")
    (tmp / "App.exe.old").write_bytes(b"in uso")
    opener = _release_opener("1.2.0", {updater.ASSETS["win32"]: b"nuovo"})
    real_unlink = updater.Path.unlink

    def unlink(self, *a, **kw):
        if self.name.endswith(".old"):
            raise PermissionError("in uso")
        return real_unlink(self, *a, **kw)

    with mock.patch.object(sys, "platform", "win32"), mock.patch.object(updater.Path, "unlink", unlink):
        try:
            updater.install("1.2.0", opener, path=exe)
            assert False
        except updater.UpdateError as exc:
            assert str(exc) == "Chiudi le altre finestre di Anonimizza DICOM e riprova."
    assert exe.read_bytes() == b"originale" and not opener.asked  # nemmeno scaricato
    assert sorted(p.name for p in tmp.iterdir()) == ["App.exe", "App.exe.old"]


def test_updater_explain():
    import errno

    import updater

    assert updater.explain(updater.UpdateError("Chiudi le altre finestre.")) == "Chiudi le altre finestre."
    assert "Disco pieno" in updater.explain(OSError(errno.ENOSPC, "x", "/a/b"))
    for exc in (subprocess.CalledProcessError(1, ["ditto", "/a/b"]), PermissionError("/a/b"), ValueError("English text"), OSError(0, "x")):
        text = updater.explain(exc)
        assert text == "Aggiornamento non riuscito: l'app è rimasta com'era. Riprova più tardi o scarica la nuova versione a mano.", text


def test_updater_url_checks():
    import updater

    assert updater.parse_version("١.٢.٣") is None  # cifre non ASCII
    base = "/releases/tag/v1.2.0"
    for url in ("https://evil.example.com" + base, "http://github.com" + base, "https://github.com.evil.example" + base):
        assert updater.latest_version(lambda u, timeout=None, url=url: _FakeResp(url=url)) is None, url
    assert updater.latest_version(lambda u, timeout=None: _FakeResp(url="https://github.com/" + updater.REPO + base)) == "1.2.0"


def _ask_log(gui, answer):
    asked = []
    gui.messagebox.askyesno = lambda title, text, **kw: asked.append((title, text)) or answer
    return asked


def test_app_offer_update_skipped_when_busy_exam_or_dialog(tmp: Path):
    window, messages = _window(tmp)
    if not window:
        return
    import app as gui

    asked = _ask_log(gui, False)
    window._offer_update("9.9.9")  # esame caricato
    window._clear_exam()
    window.busy = True
    window._offer_update("9.9.9")  # app occupata
    window.busy = False
    # finestra di scelta cartella aperta: la domanda non deve comparire sopra
    gui.filedialog.askdirectory = lambda **kw: window._offer_update("9.9.9") or ""
    window.on_choose_folder()
    assert not window.dialog_open
    gui.filedialog.askdirectory = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        window.on_choose_folder()
    except RuntimeError:
        pass
    assert not window.dialog_open  # anche se la finestra fallisce
    assert asked == [] and messages == [], (asked, messages)
    with mock.patch.object(gui.updater, "target", lambda: Path("/x/App.exe")), mock.patch.object(gui.updater, "can_replace", lambda p: None):
        window._offer_update("9.9.9")  # ferma e vuota: la domanda compare
    assert len(asked) == 1
    _close(window)


def test_app_call_event_leaves_busy_untouched(tmp: Path):
    window, messages = _new_window()
    if not window:
        return
    seen = []
    window.busy = True
    window.events.put(("call", lambda v: seen.append((v, window.busy)), "x"))
    window.update()
    import time

    end = time.time() + 5
    while not seen and time.time() < end:
        window.update()
        time.sleep(0.02)
    assert seen == [("x", True)] and window.busy
    window.busy = False
    _close(window)


def test_app_offer_update_declined_does_nothing(tmp: Path):
    window, messages = _new_window()
    if not window:
        return
    import app as gui

    with mock.patch.object(gui.updater, "VERSION", "1.0.0"), mock.patch.object(gui.updater, "install") as install, \
            mock.patch.object(gui.updater, "target", lambda: Path("/x/App.exe")), mock.patch.object(gui.updater, "can_replace", lambda p: None):
        asked = _ask_log(gui, False)
        window._offer_update("1.2.0")
        assert asked == [("Aggiornamento", "È disponibile la versione 1.2.0 (attuale 1.0.0). Aggiornare ora? "
                          "Ci vogliono circa 20 secondi. L'app si chiuderà e si riaprirà.")], asked
        install.assert_not_called()
    _close(window)


def test_app_offer_update_cannot_replace_asks_to_open_page(tmp: Path):
    window, messages = _new_window()
    if not window:
        return
    import app as gui

    opened = []
    with mock.patch.object(gui.updater, "target", lambda: Path("/x/App.exe")), \
            mock.patch.object(gui.updater, "can_replace", lambda p: "la cartella dell'app non è scrivibile"), \
            mock.patch.object(gui.updater, "install") as install, \
            mock.patch.object(gui.webbrowser, "open", lambda url: opened.append(url)):
        asked = _ask_log(gui, False)
        window._offer_update("1.2.0")
        assert opened == [] and len(asked) == 1  # No: niente browser
        assert asked[0][1] == ("È disponibile la versione 1.2.0, ma questa copia non può aggiornarsi da sola "
                               "(la cartella dell'app non è scrivibile). Aprire la pagina di download?"), asked
        _ask_log(gui, True)
        window._offer_update("1.2.0")
        _pump(window)
        install.assert_not_called()
    assert opened == [f"https://github.com/{gui.updater.REPO}/releases/latest"], opened
    assert messages == [], messages
    _close(window)


def test_app_offer_update_installs_in_background_then_relaunches(tmp: Path):
    import threading

    window, messages = _new_window()
    if not window:
        return
    import app as gui

    log, main = [], threading.current_thread()
    destroyed = []
    real_destroy = window.destroy
    window.destroy = lambda: destroyed.append(1)
    with mock.patch.object(gui.updater, "target", lambda: Path("/x/App.exe")), \
            mock.patch.object(gui.updater, "can_replace", lambda p: None), \
            mock.patch.object(gui.updater, "install", lambda v: log.append(("install", v, threading.current_thread() is main)) or Path("/x/App.exe")), \
            mock.patch.object(gui.updater, "relaunch", lambda p: log.append(("relaunch", p, bool(destroyed)))):
        _ask_log(gui, True)
        window._offer_update("1.2.0")
        _pump(window)
    assert log == [("install", "1.2.0", False), ("relaunch", Path("/x/App.exe"), False)], log
    assert destroyed == [1] and messages == [], (destroyed, messages)
    window.destroy = real_destroy
    _close(window)


def test_app_offer_update_install_error_shown_without_exit(tmp: Path):
    window, messages = _new_window()
    if not window:
        return
    import app as gui

    generic = "Aggiornamento non riuscito: l'app è rimasta com'era. Riprova più tardi o scarica la nuova versione a mano."
    for error, shown, pages in (
        (subprocess.CalledProcessError(1, ["ditto", "/a/b"]), generic, 1),  # niente testo grezzo, e pagina di download
        (gui.updater.UpdateError("Chiudi le altre finestre di Anonimizza DICOM e riprova."), "Chiudi le altre finestre di Anonimizza DICOM e riprova.", 0),
    ):
        def boom(version, error=error):
            raise error

        relaunched, destroyed, opened = [], [], []
        messages.clear()
        real_destroy = window.destroy
        window.destroy = lambda: destroyed.append(1)
        with mock.patch.object(gui.updater, "target", lambda: Path("/x/App.exe")), \
                mock.patch.object(gui.updater, "can_replace", lambda p: None), \
                mock.patch.object(gui.updater, "install", boom), \
                mock.patch.object(gui.updater, "relaunch", lambda p: relaunched.append(p)), \
                mock.patch.object(gui.webbrowser, "open", lambda url: opened.append(url)):
            _ask_log(gui, True)
            window._offer_update("1.2.0")
            _pump(window)
            window.update()
        assert messages == ["Errore: " + shown], messages
        assert not relaunched and not destroyed and not window.busy
        assert len(opened) == pages, opened
        window.destroy = real_destroy
    _close(window)


def test_app_startup_update_check(tmp: Path):
    import threading
    import time

    import app as gui

    offered, calls = [], []

    def latest():
        calls.append(threading.current_thread() is not threading.main_thread())
        return "9.9.9"

    real_offer = gui.App._offer_update
    gui.App._offer_update = lambda self, v: offered.append((v, self.busy))
    try:
        with mock.patch.object(gui.updater, "enabled", lambda: True), \
                mock.patch.object(gui.updater, "VERSION", "1.0.0"), \
                mock.patch.object(gui.updater, "latest_version", latest), \
                mock.patch.object(gui.updater, "cleanup_old", lambda: None):
            window, _ = _new_window()
            if not window:
                return
            end = time.time() + 5
            while not offered and time.time() < end:
                window.update()
                time.sleep(0.02)
            assert offered == [("9.9.9", False)] and calls == [True], (offered, calls)  # in un thread, senza occupare l'app
            assert window.title().endswith(" 1.0.0"), window.title()
            _close(window)
        # versione uguale o non attivo: nessuna domanda
        offered.clear()
        calls.clear()
        for enabled, version in ((True, "1.0.0"), (False, "9.9.9")):
            with mock.patch.object(gui.updater, "enabled", lambda e=enabled: e), \
                    mock.patch.object(gui.updater, "VERSION", "1.0.0"), \
                    mock.patch.object(gui.updater, "cleanup_old", lambda: None), \
                    mock.patch.object(gui.updater, "latest_version", lambda v=version: calls.append(1) or v):
                window, _ = _new_window()
                time.sleep(0.3)
                window.update()
                _close(window)
        assert offered == [] and calls == [1], (offered, calls)  # il secondo caso non ha nemmeno controllato
    finally:
        gui.App._offer_update = real_offer


def test_app_title_shows_version():
    import app as gui

    with mock.patch.object(gui.updater, "VERSION", "dev"):
        window, _ = _new_window()
        if not window:
            return
        assert window.title() == "Anonimizza e Masterizza DICOM"
        _close(window)
    with mock.patch.object(gui.updater, "VERSION", "1.2.0"):
        window, _ = _new_window()
        assert window.title() == "Anonimizza e Masterizza DICOM 1.2.0"
        _close(window)


def test_aggiorna_cli(tmp: Path):
    import contextlib

    import app as gui

    log = []

    def run(enabled=True, latest="1.2.0", current="1.0.0", reason=None, error=None):
        def install(v):
            log.append(("install", v))
            if error:
                raise error
            return Path("/x/App.exe")

        out = io.StringIO()
        with mock.patch.object(gui.updater, "enabled", lambda: enabled), \
                mock.patch.object(gui.updater, "VERSION", current), \
                mock.patch.object(gui.updater, "latest_version", lambda: latest), \
                mock.patch.object(gui.updater, "target", lambda: Path("/x/App.exe")), \
                mock.patch.object(gui.updater, "can_replace", lambda p: reason), \
                mock.patch.object(gui.updater, "install", install), \
                mock.patch.object(gui.updater, "relaunch", lambda p: log.append("relaunch")), \
                contextlib.redirect_stdout(out):
            code = gui.aggiorna_cli()
        lines = out.getvalue().strip().splitlines()
        assert len(lines) == 1 and "/x/" not in lines[0], lines  # una riga, senza percorsi
        return code

    assert run() == 0 and log == [("install", "1.2.0")]  # niente riavvio
    log.clear()
    assert run(latest="1.0.0") == 2 and not log
    assert run(latest="0.9.0") == 2 and not log
    assert run(latest=None) == 1 and not log  # niente rete
    assert run(enabled=False) == 1 and not log  # non compilata o dev
    assert run(reason="Cartella non scrivibile.") == 1 and not log
    assert run(error=RuntimeError("impronta non corrispondente")) == 1 and log == [("install", "1.2.0")]
    # collegamento nel __main__: un'app in versione dev non si aggiorna e non apre finestre
    done = subprocess.run([sys.executable, str(Path(__file__).with_name("app.py")), "--aggiorna"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and done.stdout.startswith("AGGIORNA"), (done.returncode, done.stdout, done.stderr)


if __name__ == "__main__":
    import inspect

    for name, test in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as tmp:
                args = [Path(tmp)] if inspect.signature(test).parameters else []
                test(*args)
            print("ok ", name)
    print("TUTTI I TEST PASSATI")
