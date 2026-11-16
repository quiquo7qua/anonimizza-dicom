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
