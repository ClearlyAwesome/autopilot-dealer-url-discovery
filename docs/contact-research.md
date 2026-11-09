# Dealership Public Contact Research — Stage 1

This is a **research-only** utility. It extends the existing 19,169-row dealer website snapshot with published email evidence. It does **not** discover, verify, or authorize ADF/XML ingestion addresses. It does not send email, lead records, or consumer PII.

### Safely run locally

From repository root:

    PYTHONPATH=src python src/contact_discovery.py --state NV --batch-size 20

Inspect two local outputs: local_contact_research.csv and local_contact_audit.csv. They must remain private and must not be committed to this **public** repository. The job is manual (not scheduled) to avoid collecting and publishing staff contacts unintentionally.

### Output rules
- Only extract addresses explicitly visible on official dealership pages (or published mailto: links), and retain exact source URL.
- Do not create sales@<city>.com or any guessed name/domain address.
- Role Hint is an automated context classification, not a verified job title.
- CRM Address Confirmed and ADF Auto Send Authorized remain NO for all discovered contacts.
- Blocked/unreachable pages are logged but not bypassed. Requests run through shared robots-aware discover.fetch.
- Externally sourced or redistributed contact data may require a licensing/privacy review before import.

### Next engineering phase (not implemented by this PR)
1. Normalize rooftop identity: prefer OEM dealer IDs and addresses; retain original Source ID, website and historical aliases.
2. Store contacts in a private, access-controlled table with evidence URL, capture date, category, verification status, owner and retention/reverification policy.
3. Add a separate dealer_crm_routes table: rooftop_id, CRM vendor, dealer authorization, validated ingest endpoint/token, routing source key, last test, CRM record identifier, delivery status and revoked_at.
4. Use states: DISCOVERED_CONTACT -> DEALER_CONFIRMED -> CRM_CONFIGURED -> TEST_SENT -> CRM_LEAD_CONFIRMED -> LIVE, with failure and revocation states.
5. Validate CRM lead creation and assignment; SMTP acceptance alone is insufficient.
6. Restrict partner outreach to appropriate public business contacts, with compliance review, suppression lists and opt-out controls.
7. Never submit a consumer lead or send customer PII through a scraped staff mailbox.
