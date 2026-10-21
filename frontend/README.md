# Researcher Workspace UI (Frontend)

React + Vite frontend application for the on-premises Pharma AI Assistant.

## Architecture & Layout

The UI implements a 3-panel researcher workspace designed for scientist-in-the-loop validation:

1. **Panel 1 — Sources & Verification Queue:**
   - Curated regulatory feeds (FDA guidance, EMA/MHRA guidelines) and scientific literature.
   - Ingestion status, metadata verification, version tracking, and source approval status.

2. **Panel 2 — Scoped Q&A Assistant:**
   - Grounded Q&A interface displaying response evidence alongside strict citations (document, section, page, date).
   - Clear separation of verified facts, model reasoning, and identified uncertainty/data gaps.

3. **Panel 3 — DOE Tables & Report Draft Studio:**
   - Design of Experiments (DOE) factor/response matrix visualization and parameter space exploration.
   - Formulation risk summaries (bioavailability, stability, nitrosamines, elemental impurities).
   - Product Development Report (PDR) section drafting with explicit human approval gates and digital sign-offs.

## Technology Stack

- **Framework:** React + Vite + TypeScript
- **Styling / Components:** Tailwind CSS / Headless UI
- **Auth:** OIDC / Keycloak SSO client integration
