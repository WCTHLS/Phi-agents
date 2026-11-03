# Open Knowledge Sources for Literature and Regulatory Research

**Project:** AI-enabled platform for pharmaceutical formulation scientists
**Phase:** 0 (Align and Prepare) – source inventory for Phase 1 (Trusted Research)
**Last reviewed:** 18 October 2026

---

## 1. Purpose

This document lists freely accessible knowledge bases that can feed the literature and regulatory research use case. It records what each source provides, how it can be accessed, and any licensing constraint that affects ingestion into a commercial client's platform.

Most entries are **open data** or **open access** rather than open-source software. "Free to read" does not always mean "free to ingest and redistribute," so every source must be logged in the source register (Section 8) before it is used.

## 2. How to Use This Document

1. Start with Tier A and Tier B for the pilot corpus (Section 7).
2. Check the **Licensing and Access Notes** (Section 5) before ingesting anything.
3. Prefer official bulk downloads and APIs over web scraping.
4. Record each approved source in the source register with its license, owner, and retrieval details.
5. Re-verify links and terms at ingestion time, as access conditions change.

---

## 3. Source Catalog

### Tier A – Regulatory and Guidance Sources (ingest first)

| Source | Content | Access | Link |
|---|---|---|---|
| ICH Guidelines | Q8 (pharmaceutical development), Q9 (risk management), Q10, Q11, Q1A–F (stability), Q3A–D (impurities, elemental impurities), Q6A, M7 (mutagenic impurities) | Free PDFs | https://www.ich.org/page/quality-guidelines |
| FDA Guidance Documents | Guidance for industry, including QbD for ANDAs, nitrosamine control, dissolution, BCS, bioequivalence | Free PDF/HTML | https://www.fda.gov/regulatory-information/search-fda-guidance-documents |
| FDA Product-Specific Guidances | Bioequivalence study recommendations for individual generic products | Free, per product | https://www.accessdata.fda.gov/scripts/cder/psg/index.cfm |
| Drugs@FDA | Approval history, labels, review documents | Web search, downloadable data | https://www.accessdata.fda.gov/scripts/cder/daf/ |
| Orange Book | Approved products, reference listed drugs, patents and exclusivity | Web search, downloadable data files | https://www.accessdata.fda.gov/scripts/cder/ob/ |
| DailyMed | Current drug labels (SPL), including inactive ingredients of reference products | Bulk files and API | https://dailymed.nlm.nih.gov/dailymed/ |
| openFDA | Labels, NDC, and other FDA datasets | Free API | https://open.fda.gov/ |
| FDA Inactive Ingredient Database (IID) | Excipients present in approved products, by route and potency | Search and quarterly downloads | https://www.accessdata.fda.gov/scripts/cder/iig |
| FDA IID Download Files | Versioned quarterly IID files | Delimited text and Excel | https://www.fda.gov/drugs/drug-approvals-and-databases/inactive-ingredients-database-download |
| FDA Dissolution Methods Database | Recommended dissolution methods for products without a USP method | Web search | https://www.accessdata.fda.gov/scripts/cder/dissolution/index.cfm |
| European Medicines Agency (EMA) | Quality guidelines, nitrosamine Q&As, EPARs (assessment reports) | Free web/PDF | https://www.ema.europa.eu/en/ |
| EMA Medicines (EPARs) | European public assessment reports per product | Free web/PDF | https://www.ema.europa.eu/en/medicines |
| MHRA (UK) | UK guidance and Public Assessment Reports | Free to read; reuse requires permission | https://www.gov.uk/government/organisations/medicines-and-healthcare-products-regulatory-agency |
| WHO Publications | Technical report series, stability guidance | Free | https://www.who.int/publications |
| WHO Prequalification | Public assessment reports for prequalified medicines | Free | https://extranet.who.int/prequal/ |
| PMDA (Japan) | Japanese Pharmacopoeia (English), regulatory information | Free to read | https://www.pmda.go.jp/english/ |
| Health Canada | Canadian regulatory guidance and product information | Free | https://www.canada.ca/en/health-canada.html |

### Tier B – Scientific Literature

| Source | Content | Access | Link |
|---|---|---|---|
| PubMed | Citations and abstracts for biomedical literature | E-utilities API | https://pubmed.ncbi.nlm.nih.gov/ |
| NCBI E-utilities | Programmatic access to PubMed and other NCBI databases | API | https://www.ncbi.nlm.nih.gov/books/NBK25501/ |
| PubMed Central (PMC) | Full-text archive; Open Access Subset for reuse | Approved bulk services | https://pmc.ncbi.nlm.nih.gov/ |
| PMC Article Datasets | License groupings and retrieval services for text mining | Cloud Service, OAI-PMH, E-utilities, BioC API | https://pmc.ncbi.nlm.nih.gov/tools/textmining/ |
| Europe PMC | Search over abstracts and open full text, plus preprints | REST API | https://europepmc.org/ |
| OpenAlex | Scholarly metadata and citation graph for literature mapping | API (key required) and free snapshot | https://openalex.org/ |
| OpenAlex Developer Docs | API reference, authentication, pricing | Documentation | https://developers.openalex.org/ |
| Crossref | DOI metadata | Free API | https://www.crossref.org/ |
| Unpaywall | Legal open-access PDF locations by DOI | Free API | https://unpaywall.org/ |
| bioRxiv | Biology preprints (license varies per paper) | API | https://www.biorxiv.org/ |
| ChemRxiv | Chemistry preprints (license varies per paper) | API | https://chemrxiv.org/ |
| DOAJ | Directory of open-access journals | API | https://doaj.org/ |
| CORE | Aggregated open-access full text | API | https://core.ac.uk/ |

### Tier C – Compounds and Substances

| Source | Content | Access | Link |
|---|---|---|---|
| PubChem | Structures, physicochemical properties, bioassays, literature links | PUG REST API | https://pubchem.ncbi.nlm.nih.gov/ |
| ChEMBL | Bioactivity and drug mechanism data (CC BY-SA) | API and downloads | https://www.ebi.ac.uk/chembl/ |
| FDA Global Substance Registration System (GSRS) | Substance identifiers (UNII), including excipients | Web and API | https://gsrs.ncats.nih.gov/ |
| ChEBI | Chemical entities ontology | Downloads and API | https://www.ebi.ac.uk/chebi/ |
| NIST Chemistry WebBook | Thermophysical and spectral data | Web | https://webbook.nist.gov/chemistry/ |
| EPA CompTox Dashboard | Toxicity and property data | Web and API | https://comptox.epa.gov/dashboard/ |

### Tier D – Patents (supports generic development)

| Source | Content | Access | Link |
|---|---|---|---|
| Google Patents | Full-text patent search | Web | https://patents.google.com/ |
| USPTO Open Data Portal | US patent data | Downloads and API | https://data.uspto.gov/ |
| PatentsView | Disambiguated US patent data | API and bulk | https://patentsview.org/ |
| Espacenet (EPO) | European and worldwide patents | Web; OPS API with quotas | https://worldwide.espacenet.com/ |
| WIPO PATENTSCOPE | International (PCT) applications | Web | https://patentscope.wipo.int/ |
| The Lens | Patent and scholarly search | Free tier; bulk limited | https://www.lens.org/ |

Patent text is public, but bulk-use terms and quotas differ by provider. Use these sources to surface patents for scientist review, not to automate freedom-to-operate conclusions.

### Tier E – Clinical Context (lower priority)

| Source | Content | Access | Link |
|---|---|---|---|
| ClinicalTrials.gov | Registered clinical studies | API | https://clinicaltrials.gov/ |
| EU Clinical Trials Register | European trial registrations | Web | https://www.clinicaltrialsregister.eu/ |

---

## 4. Not Open – Plan Around These

These resources are important to formulation work but are paid or restricted. Treat them as client-supplied sources, and confirm licensing with the client before ingestion.

| Resource | Why it matters | Link |
|---|---|---|
| USP-NF | US pharmacopeial standards and monographs | https://www.uspnf.com/ |
| European Pharmacopoeia (Ph. Eur.) | European standards and monographs | https://www.edqm.eu/ |
| British Pharmacopoeia | UK standards and monographs | https://www.pharmacopoeia.com/ |
| Handbook of Pharmaceutical Excipients | Excipient reference | Publisher license |
| DrugBank | Drug knowledge base; commercial use requires a license | https://go.drugbank.com/ |
| Reaxys | Chemistry reference database | https://www.elsevier.com/products/reaxys |
| SciFinder | Chemistry and literature search | https://www.cas.org/ |
| Cortellis | Pharma intelligence | https://clarivate.com/cortellis/ |
| Lhasa Limited | Nitrosamine and toxicology knowledge bases | https://www.lhasalimited.org/ |
| Paywalled journals | Primary literature behind subscriptions | Client subscriptions |

Open substitutes: the FDA IID, PubChem, ChEMBL, and FDA GSRS cover much of what DrugBank and the excipient handbook provide. Supplier technical literature for excipients is often downloadable but is copyrighted, so obtain permission first.

---

## 5. Licensing and Access Notes

- **PubMed Central.** The Open Access Subset is divided into commercial-use-allowed (CC0, CC BY, CC BY-SA, CC BY-ND), non-commercial-only, and other license groups. Automated retrieval is permitted only through PMC's approved services. From August 2026, full-text data files must be retrieved through the PMC Cloud Service rather than the FTP service. For a client product, ingest only the commercial-use group.
- **OpenAlex.** The data is free and the snapshot is free to download in bulk. The hosted API requires a key and is freemium: a free key includes a modest daily allowance and heavier use is billed. For corpus building, use the snapshot rather than the API.
- **DrugBank.** Free for non-commercial use only. Internal use for commercial product development requires a separate license, and academic downloads are currently paused. Do not plan on it without a commercial agreement.
- **FDA Inactive Ingredient Database.** Updated quarterly. Build a scheduled job that re-pulls and versions each release.
- **FDA Dissolution Methods Database.** Covers products that lack a USP dissolution method.
- **FDA and ICH documents.** Generally permissive (FDA documents are US government works), but record the license for each source rather than assuming.
- **EMA.** The EMA legal notice sets out its reuse permissions and excludes third-party content. Record the exact terms in the source register. Legal notice: https://www.ema.europa.eu/en/about-us/legal-notice
- **MHRA.** Published guidance says permission must be requested before reproducing or re-using MHRA information. Treat MHRA content as "ask first" until the client's legal team confirms. Guidance: https://www.gov.uk/government/publications/reproduce-or-re-use-mhra-information/reproduce-or-re-use-mhra-information

Licensing positions in this section reflect information reviewed on the date above. Confirm current terms before ingestion and obtain client legal review for any source used in a regulated workflow.

---

## 6. Ingestion Guidelines

- Prefer official bulk downloads, snapshots, and documented APIs over scraping.
- Respect each provider's terms of service, rate limits, and robots rules.
- Preserve original files unchanged and store them with version and retrieval metadata.
- Keep page or section references so every answer can cite an exact passage.
- Version scheduled datasets (for example, the quarterly IID) and keep prior releases.
- Exclude non-commercial-only content from any corpus used for the client's product.

---

## 7. Recommended Pilot Corpus

1. The ICH quality guidelines listed in Tier A (Q8, Q9, Q10, Q11, Q1A–F, Q3A–D, Q6A, M7).
2. FDA's QbD for ANDAs example and FDA's nitrosamine guidance, plus EMA's nitrosamine Q&A.
3. For one chosen reference product: its Product-Specific Guidance, Orange Book entry, DailyMed label, IID entries, and Dissolution Methods Database entry.
4. PMC commercial-use articles and PubMed abstracts retrieved by queries on the chosen drug and dosage form, with OpenAlex used to expand citations from key papers.

---

## 8. Source Register Template

Record one entry per approved source before ingestion.

| Field | Description |
|---|---|
| Source name | Name of the resource |
| URL | Canonical link |
| Owner | Publisher or data owner |
| License / terms | License name and link, commercial-use status |
| Access method | Bulk download, API, manual retrieval |
| Retrieval date | Date the copy was obtained |
| Version | Release, edition, or snapshot identifier |
| Content hash | Checksum of the stored original file |
| Refresh cadence | Update frequency and next review date |
| Approver | Person who approved the source as trusted |
| Notes | Restrictions, attribution requirements, known issues |

---

## 9. Disclaimer

This catalog supports internal research tooling. It is not legal advice. Source availability, access methods, and licensing terms change over time, and all links and terms should be verified before use. Outputs derived from these sources require review by qualified scientific and quality personnel.
