"""Curated registry of approved regulatory guidelines and scientific evidence chunks."""

import hashlib

from pharma_ai.features.research.schemas import DocumentMetadata, EvidenceChunk

_DOCUMENTS_META: list[dict] = [
    {
        "source_id": "fda-nitrosamines-rev1",
        "title": "Control of Nitrosamine Impurities in Human Drugs: Guidance for Industry (Rev 1)",
        "publisher": "FDA",
        "publication_date": "2021-02-24",
        "version": "Revision 1",
        "url_or_identifier": "FDA-2020-D-1530",
        "notes": "Guidance on acceptable intake limits, root causes, and three-step risk evaluation.",
    },
    {
        "source_id": "ich-q8-r2-pharma-dev",
        "title": "ICH Harmonised Tripartite Guideline: Pharmaceutical Development Q8(R2)",
        "publisher": "ICH",
        "publication_date": "2009-08-01",
        "version": "Step 4",
        "url_or_identifier": "ICH-Q8-R2",
        "notes": "Quality Target Product Profile (QTPP), CQAs, CPPs, and Design Space principles.",
    },
    {
        "source_id": "ich-q3d-elemental-impurities",
        "title": "ICH Guideline Q3D(R2) on Elemental Impurities",
        "publisher": "ICH",
        "publication_date": "2022-04-26",
        "version": "Revision 2",
        "url_or_identifier": "ICH-Q3D-R2",
        "notes": "Classification and Permitted Daily Exposure (PDE) limits for elemental impurities.",
    },
]

_RAW_EVIDENCE_CHUNKS: list[dict] = [
    {
        "chunk_id": "fda-nitro-chunk-01",
        "source_id": "fda-nitrosamines-rev1",
        "document_title": "Control of Nitrosamine Impurities in Human Drugs: Guidance for Industry (Rev 1)",
        "section": "Section III. Acceptable Intake Limits",
        "page": 10,
        "text": (
            "The recommended acceptable intake (AI) limits for nitrosamines in human pharmaceuticals are: "
            "NDMA (N-Nitrosodimethylamine): 96 ng/day; "
            "NDEA (N-Nitrosodiethylamine): 26.5 ng/day; "
            "NMBA (N-Nitroso-N-methyl-4-aminobutyric acid): 96 ng/day; "
            "NMPA (N-Nitroso-N-methyl-4-aminobutyric acid): 26.5 ng/day; "
            "NIPEA (N-Nitrosoisopropylethylamine): 26.5 ng/day; "
            "NDIPA (N-Nitrosodiisopropylamine): 26.5 ng/day."
        ),
    },
    {
        "chunk_id": "fda-nitro-chunk-02",
        "source_id": "fda-nitrosamines-rev1",
        "document_title": "Control of Nitrosamine Impurities in Human Drugs: Guidance for Industry (Rev 1)",
        "section": "Section IV. Root Causes and Excipient Risk Factors",
        "page": 14,
        "text": (
            "Nitrosamines can form when a secondary, tertiary, or quaternary amine reacts with a nitrosating agent "
            "(such as nitrites) under acidic conditions during drug substance synthesis or drug product manufacturing and storage. "
            "Nitrites may be present as trace impurities in commonly used excipients, including microcrystalline cellulose, "
            "povidone, crospovidone, and pregelatinized starch, which can facilitate nitrosamine formation in the finished dosage form."
        ),
    },
    {
        "chunk_id": "fda-nitro-chunk-03",
        "source_id": "fda-nitrosamines-rev1",
        "document_title": "Control of Nitrosamine Impurities in Human Drugs: Guidance for Industry (Rev 1)",
        "section": "Section V. Control Strategy & Manufacturer Actions",
        "page": 18,
        "text": (
            "Manufacturers should conduct a three-step risk management program: "
            "Step 1: Perform risk assessments to identify potential nitrosamine risks in APIs and drug products. "
            "Step 2: Conduct confirmatory testing when risk is identified using validated and sensitive analytical methods (e.g., LC-MS/MS). "
            "Step 3: Implement control strategies and report changes to regulatory authorities in drug applications."
        ),
    },
    {
        "chunk_id": "ich-q8-chunk-01",
        "source_id": "ich-q8-r2-pharma-dev",
        "document_title": "ICH Harmonised Tripartite Guideline: Pharmaceutical Development Q8(R2)",
        "section": "Section 2.1 Quality Target Product Profile (QTPP)",
        "page": 3,
        "text": (
            "The Quality Target Product Profile (QTPP) forms the basis of design for the development of the product. "
            "Considerations for the QTPP include: intended use in clinical setting, route of administration, dosage form, "
            "delivery systems, dosage strength, container closure system, therapeutic moiety release or delivery, and "
            "drug product quality criteria (e.g., sterility, purity, stability, and drug release) appropriate for the intended product."
        ),
    },
    {
        "chunk_id": "ich-q8-chunk-02",
        "source_id": "ich-q8-r2-pharma-dev",
        "document_title": "ICH Harmonised Tripartite Guideline: Pharmaceutical Development Q8(R2)",
        "section": "Section 2.2 Critical Quality Attributes (CQAs)",
        "page": 4,
        "text": (
            "A Critical Quality Attribute (CQA) is a physical, chemical, biological, or microbiological property or characteristic "
            "that should be within an appropriate limit, range, or distribution to ensure the desired product quality. "
            "CQAs are typically associated with the drug substance, excipients, intermediates (in-process materials), and drug product."
        ),
    },
    {
        "chunk_id": "ich-q8-chunk-03",
        "source_id": "ich-q8-r2-pharma-dev",
        "document_title": "ICH Harmonised Tripartite Guideline: Pharmaceutical Development Q8(R2)",
        "section": "Section 2.4 Design Space",
        "page": 7,
        "text": (
            "The multidimensional combination and interaction of input variables (e.g., material attributes) and process parameters "
            "that have been demonstrated to provide assurance of quality is termed the Design Space. "
            "Working within the design space is not considered as a change. Movement out of the design space is considered to be "
            "a change and would normally initiate a regulatory post-approval change process."
        ),
    },
    {
        "chunk_id": "ich-q3d-chunk-01",
        "source_id": "ich-q3d-elemental-impurities",
        "document_title": "ICH Guideline Q3D(R2) on Elemental Impurities",
        "section": "Section 5.1 Element Classification & Oral PDEs",
        "page": 12,
        "text": (
            "Class 1 elements are human toxicants that have limited or no use in the manufacture of pharmaceuticals. "
            "Their presence is typically derived from commonly used materials (e.g., mined excipients). "
            "The permitted daily exposure (PDE) limits for Class 1 elemental impurities administered by the oral route are: "
            "Cadmium (Cd): 5.0 µg/day; Lead (Pb): 5.0 µg/day; Inorganic Arsenic (As): 15.0 µg/day; Inorganic Mercury (Hg): 30.0 µg/day."
        ),
    },
]


def get_approved_documents() -> list[DocumentMetadata]:
    """Retrieve all approved documents with computed SHA-256 hashes."""
    documents = []
    for doc in _DOCUMENTS_META:
        content_repr = doc["title"] + doc["source_id"] + doc["version"]
        h = hashlib.sha256(content_repr.encode("utf-8")).hexdigest()
        documents.append(
            DocumentMetadata(
                source_id=doc["source_id"],
                title=doc["title"],
                publisher=doc["publisher"],
                publication_date=doc["publication_date"],
                version=doc["version"],
                url_or_identifier=doc["url_or_identifier"],
                content_hash=h,
                notes=doc["notes"],
            )
        )
    return documents


def get_raw_evidence_chunks() -> list[EvidenceChunk]:
    """Retrieve all pre-indexed authentic regulatory evidence chunks."""
    return [
        EvidenceChunk(
            chunk_id=c["chunk_id"],
            source_id=c["source_id"],
            document_title=c["document_title"],
            section=c["section"],
            page=c["page"],
            text=c["text"],
            similarity_score=0.0,
        )
        for c in _RAW_EVIDENCE_CHUNKS
    ]
