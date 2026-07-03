# ground_truth.py
"""
Verified ground-truth claims and company metadata for the agent evaluation
framework. All entries drawn from publicly available primary sources
(company press releases, sustainability reports, and the public TPI and
NZIF frameworks) that underpin transition-enabler assessments produced for
an asset manager. Used in live tests and manual verification runs — not
imported by any pipeline module.

Each COMPANY_CLAIMS entry contains:
    allowlist: list[str] — known legitimate domains for this company
    bucket_a_claims: list[dict] — verifiable present-tense facts,
        each with "claim_text" and "expected_source_domain"
    bucket_b_notes: str — NZIF alignment and TPI classification
        summary for human reference
    bucket_c_claims: list[dict] — definitionally fuzzy claims,
        each with "claim_text" and "notes"
    bucket_d_claims: list[dict] — counterfactual/future claims,
        each with "claim_text" and "notes"
"""

COMPANY_CLAIMS = {
    "tsmc": {
        "allowlist": ["tsmc.com", "pr.tsmc.com", "sustainability.tsmc.com"],
        "bucket_a_claims": [
            {
                "claim_text": (
                    "TSMC is moving its target for 100 percent renewable "
                    "energy consumption for all global operations forward "
                    "to 2040 from 2050"
                ),
                "expected_source_domain": "pr.tsmc.com",
                "notes": "Verified against TSMC press release Sept 2023",
            },
            {
                "claim_text": ("TSMC's renewable energy ratio surpassed 14% in 2024"),
                "expected_source_domain": "tsmc.com",
                "notes": "From TSMC 2024 Sustainability Report",
            },
            {
                "claim_text": (
                    "TSMC committed to SBTi-aligned targets as of April 2025"
                ),
                "expected_source_domain": "tsmc.com",
                "notes": "Earth Day SBTi commitment announcement April 2025",
            },
        ],
        "bucket_b_notes": (
            "NZIF: Aligning to a net zero pathway. "
            "TPI MQ: Level 5, fails indicators 20, 21, 22, 23. "
            "TPI CP: Not assessable — no IEA benchmark for semiconductors."
        ),
        "bucket_c_claims": [
            {
                "claim_text": "TSMC has roughly 60% of the foundry market",
                "notes": (
                    "Definitionally contested — 'foundry market' boundary "
                    "varies by whether IDM in-house capacity is included"
                ),
            },
        ],
        "bucket_d_claims": [
            {
                "claim_text": (
                    "Without TSMC, the global climate transition would be "
                    "set back by a decade because advanced chips are "
                    "essential for clean energy technology."
                ),
                "notes": "Counterfactual — not verifiable against any source",
            },
        ],
    },
    "patagonia": {
        "allowlist": ["patagonia.com"],
        "bucket_a_claims": [
            {
                "claim_text": ("Patagonia repaired close to 175,000 products in FY25"),
                "expected_source_domain": "patagonia.com",
                "notes": "From Patagonia 2025 Impact Report",
            },
        ],
        "bucket_b_notes": (
            "NZIF: Aligning to a net zero pathway (SBTi 1.5C-classified "
            "targets, net zero by FY2040). "
            "TPI MQ: Not covered — privately held, structurally outside "
            "TPI universe. "
            "TPI CP: Not assessable — no benchmark for apparel/consumer goods."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [
            {
                "claim_text": (
                    "If Patagonia disappeared tomorrow, the climate "
                    "transition would be essentially unaffected."
                ),
                "notes": "Counterfactual verdict from the asset-manager assessment",
            },
        ],
    },
    "totalenergies": {
        "allowlist": ["totalenergies.com"],
        "bucket_a_claims": [
            {
                "claim_text": (
                    "TotalEnergies invested close to 5 billion dollars "
                    "in low-carbon energy in 2024"
                ),
                "expected_source_domain": "totalenergies.com",
                "notes": (
                    "From TotalEnergies Sustainability & Climate 2025 "
                    "Progress Report"
                ),
            },
            {
                "claim_text": (
                    "TotalEnergies grew net electricity production 23% " "in 2024"
                ),
                "expected_source_domain": "totalenergies.com",
                "notes": "From TotalEnergies 2025 Strategy and Outlook",
            },
        ],
        "bucket_b_notes": (
            "NZIF: Committed to aligning (fossil core, analyst judgement) / "
            "Aligning to a net zero pathway (Integrated Power, analyst "
            "judgement). These are analyst judgements, not official NZIF "
            "outputs — NZIF assesses TotalEnergies as a single entity. "
            "TPI MQ: Level 5, fails indicators 21 and 22 only. "
            "TPI CP: Aligned to National Pledges (2028, 2035) and "
            "Below 2 Degrees (2050) only — never 1.5C at any horizon."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [
            {
                "claim_text": (
                    "If TotalEnergies disappeared tomorrow, the effect "
                    "on the climate transition is genuinely ambiguous."
                ),
                "notes": (
                    "Counterfactual — renewables capacity would need "
                    "replacing but fossil production might be met by "
                    "worse-intensity producers"
                ),
            },
        ],
    },
    "antofagasta": {
        "allowlist": ["antofagasta.co.uk", "antofagasta.com"],
        "bucket_a_claims": [
            {
                "claim_text": (
                    "Antofagasta achieved its 30% Scope 1 and 2 reduction "
                    "target early through 100% renewable electricity "
                    "contracts from April 2022"
                ),
                "expected_source_domain": "antofagasta.co.uk",
                "notes": "From Antofagasta Sustainability Report 2024",
            },
        ],
        "bucket_b_notes": (
            "NZIF: Committed to aligning / possibly Aligning to a net zero "
            "pathway (analyst judgement). Climate solutions: qualifies — "
            "copper is essential transition infrastructure. "
            "TPI MQ: Level 3, confirmed December 2024 and December 2025. "
            "TPI CP: Not yet assessed — TPI diversified mining CP methodology "
            "published October 2024, individual assessment pending."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [],
    },
    "vestas": {
        "allowlist": ["vestas.com", "ir.vestas.com"],
        "bucket_a_claims": [],
        "bucket_b_notes": (
            "NZIF: Aligning to a net zero pathway (SBTi 1.5C validated, "
            "net zero by 2040). "
            "TPI MQ: Level 5, confirmed December 2024 and December 2025. "
            "Fails indicators 20, 22, 23 — capex phase-out commitment, "
            "capex-decarbonisation alignment, trade association consistency. "
            "TPI CP: Not assessed — no IEA benchmark for wind turbine "
            "manufacturers."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [],
    },
    "coal_india": {
        "allowlist": ["coalindia.in"],
        "bucket_a_claims": [],
        "bucket_b_notes": (
            "NZIF: Not aligning — no net zero goal, no SBTi commitment. "
            "TPI MQ: Level 1, December 2024 and December 2025. Oscillated "
            "between Level 1 and Level 2 across nine assessments since 2017 "
            "with no sustained improvement. "
            "TPI CP: In scope and methodology exists, but 'No alignment data "
            "available' — likely due to insufficient public disclosure."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [],
    },
    "cheniere": {
        "allowlist": ["cheniere.com", "lngir.com"],
        "bucket_a_claims": [],
        "bucket_b_notes": (
            "NZIF: Committed to aligning. "
            "TPI MQ: Level 3, December 2024 and December 2025 (climbed "
            "from Level 2). Fails indicators 8, 10, 12, 15, 17-23. "
            "TPI CP: No assessment published — coverage status under oil "
            "and gas CP methodology unclear (classified as distributor "
            "not producer)."
        ),
        "bucket_c_claims": [
            {
                "claim_text": (
                    "LNG from Cheniere displaces coal in Asian energy markets"
                ),
                "notes": (
                    "Definitionally contested — depends on whether LNG "
                    "substitutes for coal or expands total fossil supply"
                ),
            },
        ],
        "bucket_d_claims": [],
    },
    "microsoft": {
        "allowlist": ["microsoft.com", "blogs.microsoft.com"],
        "bucket_a_claims": [],
        "bucket_b_notes": (
            "NZIF: Aligned to a net zero pathway or beyond — carbon "
            "negative commitment by 2030, SBTi validated. "
            "TPI MQ: Level 4, December 2024 and December 2025. Dropped "
            "from Level 5 in November 2023 during period of rapid AI "
            "infrastructure expansion. Fails indicators 14, 15, 19-23. "
            "TPI CP: No benchmark — no IEA pathway for software/technology."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [
            {
                "claim_text": (
                    "Microsoft's AI data centre expansion is making it "
                    "harder for everything else to decarbonise."
                ),
                "notes": (
                    "Counterfactual about systemic effect — not answerable "
                    "by NZIF or TPI directly"
                ),
            },
        ],
    },
    "frontier_lithium": {
        "allowlist": ["frontierlithium.com", "paklithiumproject.com"],
        "bucket_a_claims": [
            {
                "claim_text": (
                    "Frontier Lithium has no stated net zero or interim "
                    "climate target"
                ),
                "expected_source_domain": "frontierlithium.com",
                "notes": (
                    "Confirmed absence — checked against company "
                    "sustainability page and press releases"
                ),
            },
        ],
        "bucket_b_notes": (
            "NZIF: Not aligning — no long-term decarbonisation goal found "
            "anywhere in public disclosure. Does not clear NZIF's lowest "
            "positive tier. "
            "TPI MQ: Not assessed — pre-production, structurally outside "
            "TPI's listed-equity universe. "
            "TPI CP: Not assessed — same reason."
        ),
        "bucket_c_claims": [],
        "bucket_d_claims": [],
    },
}
