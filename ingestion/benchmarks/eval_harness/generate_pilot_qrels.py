#!/usr/bin/env python3
"""Emit pilot qrels JSON (20 cases/corpus) from structured data."""

from __future__ import annotations

import json
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent


def _case(
    cid: str,
    *,
    category: str,
    query: str,
    gold: str,
    chunk: list[str],
    answer: str,
    alts: list[str] | None = None,
    traps: list[str] | None = None,
    forbidden: list[str] | None = None,
) -> dict:
    return {
        "id": cid,
        "category": category,
        "query": query,
        "topic": None,
        "expected_answer": answer,
        "gold_source_document": gold,
        "gold_source_chunk_contains": chunk,
        "relevant_seed_keys": [gold],
        "acceptable_alternate_terms": alts or [],
        "trap_documents": traps or [],
        "forbidden_top1_seed_keys": forbidden or traps or [],
    }


RETAIL = [
    _case("retail-direct-01", category="direct", query="How do you measure shoe size with a Brannock device?", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["Brannock device"], answer="Stand on Brannock device and read length/width.", traps=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"]),
    _case("retail-direct-02", category="direct", query="What is the furniture return window in 2024?", gold="eval-harness/retail-furniture/return-policy-2024.md", chunk=["45 days"], answer="45 days with receipt for unworn items.", traps=["eval-harness/retail-furniture/duplicate-return-policy-copy.md"]),
    _case("retail-direct-03", category="direct", query="restocking fee sectional sofa", gold="eval-harness/retail-furniture/return-policy-2024.md", chunk=["Restocking fee", "15%"], answer="15% restocking fee on sectional sofas."),
    _case("retail-direct-04", category="direct", query="running shoes half size guidance", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["half size"], answer="Often half size up for toe box clearance."),
    _case("retail-direct-05", category="direct", query="couch assembly hex key M6", gold="eval-harness/retail-furniture/couch-assembly-FINAL.pdf.txt", chunk=["hex key M6"], answer="Use M6 hex key from bag B for chaise bracket."),
    _case("retail-direct-06", category="direct", query="width codes narrow standard wide shoes", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["Width codes"], answer="B narrow, D standard, 2E wide."),
    _case("retail-direct-07", category="direct", query="final sale exclusions returns", gold="eval-harness/retail-furniture/return-policy-2024.md", chunk=["final sale"], answer="Final sale and customized upholstery excluded."),
    _case("retail-direct-08", category="direct", query="measure both feet larger size", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["Measure both feet"], answer="Measure both feet; order the larger size."),
    _case("retail-synonym-01", category="synonym", query="footwear exchange policy duration", gold="eval-harness/retail-furniture/return-policy-2024.md", chunk=["45 days"], answer="45-day return window.", alts=["return window", "exchange"]),
    _case("retail-synonym-02", category="synonym", query="sneaker toe box sizing recommendation", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["toe box"], answer="Half size up for running shoes.", alts=["running shoes", "athletic"]),
    _case("retail-synonym-03", category="synonym", query="sectional couch return surcharge", gold="eval-harness/retail-furniture/return-policy-2024.md", chunk=["15%"], answer="15% restocking fee.", alts=["restocking fee"]),
    _case("retail-synonym-04", category="synonym", query="Brannock foot measurement procedure", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["Brannock"], answer="Use Brannock device while standing.", alts=["measure foot length"]),
    _case("retail-acronym-01", category="acronym", query="RMA-RET-2024 online label", gold="eval-harness/retail-furniture/return-policy-2024.md", chunk=["RMA-RET-2024"], answer="Prepaid label RMA-RET-2024 for online orders."),
    _case("retail-acronym-02", category="acronym", query="SKU ATH-9912 sizing info", gold="eval-harness/retail-furniture/athletic-footwear-catalog.md", chunk=["ATH-9912"], answer="Catalog SKU has no sizing guidance — trap doc.", traps=["eval-harness/retail-furniture/athletic-footwear-catalog.md"], forbidden=[]),
    _case("retail-acronym-03", category="acronym", query="HX-4420 assembly hardware bag B", gold="eval-harness/retail-furniture/couch-assembly-FINAL.pdf.txt", chunk=["HX-4420", "bag B"], answer="HX-4420 sectional uses bag B hardware."),
    _case("retail-acronym-04", category="acronym", query="2E width code meaning", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["2E"], answer="2E is wide width code.", alts=["wide"]),
    _case("retail-version-01", category="version_trap", query="subtract 1.5 sizes EU to US running shoes", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["half size"], answer="Deprecated v1 advice — use v2 Brannock guidance.", forbidden=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"], traps=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"]),
    _case("retail-version-02", category="version_trap", query="add two full sizes for running shoes obsolete", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["half size up"], answer="Obsolete v1 rule; v2 recommends half size up.", forbidden=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"], traps=["eval-harness/retail-furniture/shoe-sizing-guide-v1-deprecated.md"]),
    _case("retail-cross-01", category="cross_doc", query="return policy and shoe sizing version reference", gold="eval-harness/retail-furniture/shoe-sizing-guide-v2.md", chunk=["return-policy-2024"], answer="Sizing v2 references return-policy-2024 for exchanges.", alts=["See return-policy"]),
    _case("retail-cross-02", category="cross_doc", query="assembly partial PDF missing pages warranty", gold="eval-harness/retail-furniture/couch-assembly-FINAL.pdf.txt", chunk=["Missing page"], answer="Partial extract missing pages 5-7; warranty link broken."),
]

TERRAFORM = [
    _case("tf-direct-01", category="direct", query="terraform vpc module outputs vpc_id", gold="eval-harness/terraform-cloud/vpc-module-readme.md", chunk=["vpc_id"], answer="Outputs include vpc_id and subnet ids."),
    _case("tf-direct-02", category="direct", query="IAM pipeline least privilege terraform apply", gold="eval-harness/terraform-cloud/iam-policy-patterns.md", chunk=["least-privilege"], answer="Scoped create/modify/delete on tagged resources."),
    _case("tf-direct-03", category="direct", query="terraform 1.5 s3 backend locking change", gold="eval-harness/terraform-cloud/terraform-1.5-upgrade-notes.md", chunk=["use_lockfile"], answer="S3 backend use_lockfile replaces DynamoDB lock table."),
    _case("tf-direct-04", category="direct", query="EKS module IRSA oidc output", gold="eval-harness/terraform-cloud/eks-cluster-module.md", chunk=["oidc_provider_arn"], answer="Outputs oidc_provider_arn for IRSA."),
    _case("tf-direct-05", category="direct", query="VPC module creates security groups?", gold="eval-harness/terraform-cloud/vpc-module-readme.md", chunk=["not"], answer="Security groups are not created by VPC module."),
    _case("tf-direct-06", category="direct", query="terraform test framework GA version", gold="eval-harness/terraform-cloud/terraform-1.5-upgrade-notes.md", chunk=["terraform test"], answer="terraform test is GA in 1.5 upgrade notes."),
    _case("tf-direct-07", category="direct", query="private subnet tag kubernetes internal elb", gold="eval-harness/terraform-cloud/eks-cluster-module.md", chunk=["kubernetes.io/role/internal-elb"], answer="Private subnets need internal-elb role tag."),
    _case("tf-direct-08", category="direct", query="avoid AdministratorAccess CI terraform", gold="eval-harness/terraform-cloud/iam-policy-patterns.md", chunk=["AdministratorAccess"], answer="Avoid AdministratorAccess; use scoped roles."),
    _case("tf-synonym-01", category="synonym", query="virtual private cloud module cidr input", gold="eval-harness/terraform-cloud/vpc-module-readme.md", chunk=["cidr_block"], answer="Primary input cidr_block default 10.0.0.0/16.", alts=["VPC CIDR"]),
    _case("tf-synonym-02", category="synonym", query="managed node groups service account roles", gold="eval-harness/terraform-cloud/eks-cluster-module.md", chunk=["IRSA"], answer="IRSA enabled on EKS module.", alts=["IRSA", "oidc"]),
    _case("tf-synonym-03", category="synonym", query="state lock dynamodb legacy terraform docs", gold="eval-harness/terraform-cloud/terraform-1.2-legacy-docs.md", chunk=["DynamoDB"], answer="Legacy docs mention DynamoDB locks — trap for version questions.", traps=["eval-harness/terraform-cloud/terraform-1.2-legacy-docs.md"]),
    _case("tf-synonym-04", category="synonym", query="identity access management CI read-only plan role", gold="eval-harness/terraform-cloud/iam-policy-patterns.md", chunk=["read-only"], answer="Plan role read-only on state and Describe* APIs.", alts=["IAM plan role"]),
    _case("tf-acronym-01", category="acronym", query="what does SG mean in cloud glossary", gold="eval-harness/terraform-cloud/acronym-glossary.md", chunk=["Security Group"], answer="SG = Security Group.", alts=["security group"]),
    _case("tf-acronym-02", category="acronym", query="IRSA acronym EKS", gold="eval-harness/terraform-cloud/acronym-glossary.md", chunk=["IAM Roles for Service Accounts"], answer="IRSA = IAM Roles for Service Accounts."),
    _case("tf-acronym-03", category="acronym", query="TF internal shorthand terraform", gold="eval-harness/terraform-cloud/acronym-glossary.md", chunk=["Terraform"], answer="TF shorthand for Terraform."),
    _case("tf-acronym-04", category="acronym", query="AZ availability zone abbreviation", gold="eval-harness/terraform-cloud/acronym-glossary.md", chunk=["Availability Zone"], answer="AZ = Availability Zone."),
    _case("tf-version-01", category="version_trap", query="terraform 0.12 migration dynamodb lock table only", gold="eval-harness/terraform-cloud/terraform-1.5-upgrade-notes.md", chunk=["use_lockfile"], answer="Legacy 1.2 doc trap; 1.5 uses S3 use_lockfile.", forbidden=["eval-harness/terraform-cloud/terraform-1.2-legacy-docs.md"], traps=["eval-harness/terraform-cloud/terraform-1.2-legacy-docs.md"]),
    _case("tf-version-02", category="version_trap", query="provider pessimistic constraint 3.0 archived doc", gold="eval-harness/terraform-cloud/terraform-1.5-upgrade-notes.md", chunk=["1.5"], answer="Archived 1.2 provider pin guidance is outdated.", forbidden=["eval-harness/terraform-cloud/terraform-1.2-legacy-docs.md"], traps=["eval-harness/terraform-cloud/terraform-1.2-legacy-docs.md"]),
    _case("tf-cross-01", category="cross_doc", query="EKS module VPC subnet dependency", gold="eval-harness/terraform-cloud/eks-cluster-module.md", chunk=["VPC module"], answer="EKS module requires existing VPC/subnets; see VPC module readme."),
    _case("tf-cross-02", category="cross_doc", query="IAM acronym glossary vs policy patterns OIDC", gold="eval-harness/terraform-cloud/iam-policy-patterns.md", chunk=["OIDC"], answer="Policy patterns prefer OIDC over long-lived keys; glossary defines IAM."),
]

LEGAL = [
    _case("legal-direct-01", category="direct", query="MSA v3 liability cap twelve months fees", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["twelve (12) months"], answer="Cap at fees paid in prior 12 months."),
    _case("legal-direct-02", category="direct", query="governing law MSA v3", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["Delaware"], answer="Delaware law; arbitration Wilmington."),
    _case("legal-direct-03", category="direct", query="DPA subprocessor notice period", gold="eval-harness/legal-contracts/data-processing-addendum.md", chunk=["30-day"], answer="30-day notice for subprocessor changes."),
    _case("legal-direct-04", category="direct", query="vendor checklist MSA version required", gold="eval-harness/legal-contracts/vendor-contract-checklist.md", chunk=["MSA version is v3"], answer="Verify MSA v3 not archived v1."),
    _case("legal-direct-05", category="direct", query="liability carve-out gross negligence", gold="eval-harness/legal-contracts/liability-cap-clause.md", chunk=["gross negligence"], answer="Carve-out for gross negligence or willful misconduct."),
    _case("legal-direct-06", category="direct", query="termination material breach cure period", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["30 days"], answer="30 days written notice after cure period."),
    _case("legal-direct-07", category="direct", query="DPA encryption at rest requirement", gold="eval-harness/legal-contracts/data-processing-addendum.md", chunk=["encryption at rest"], answer="Encryption at rest and access logging required."),
    _case("legal-direct-08", category="direct", query="insurance certificates vendor checklist", gold="eval-harness/legal-contracts/vendor-contract-checklist.md", chunk=["Insurance certificates"], answer="Insurance certificates must be on file."),
    _case("legal-synonym-01", category="synonym", query="aggregate liability limit recent fees", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["aggregate liability"], answer="Aggregate liability capped at 12-month fees.", alts=["liability cap"]),
    _case("legal-synonym-02", category="synonym", query="processor personal data annex A purpose limit", gold="eval-harness/legal-contracts/data-processing-addendum.md", chunk=["Annex A"], answer="Processing limited to Annex A purposes.", alts=["DPA"]),
    _case("legal-synonym-03", category="synonym", query="third party IP indemnification sow", gold="eval-harness/legal-contracts/liability-cap-clause.md", chunk=["third-party IP"], answer="Indemnification when listed in SOW.", alts=["IP claims"]),
    _case("legal-synonym-04", category="synonym", query="binding arbitration location vendor contract", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["Wilmington"], answer="Binding arbitration in Wilmington.", alts=["dispute resolution"]),
    _case("legal-acronym-01", category="acronym", query="DPA meaning vendor personal data", gold="eval-harness/legal-contracts/data-processing-addendum.md", chunk=["Data Processing Addendum"], answer="Data Processing Addendum for personal data."),
    _case("legal-acronym-02", category="acronym", query="MSA template latest version identifier", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["Template v3"], answer="MSA Template v3 effective June 2024."),
    _case("legal-acronym-03", category="acronym", query="SOW referenced indemnification carve-out", gold="eval-harness/legal-contracts/liability-cap-clause.md", chunk=["SOW"], answer="SOW must list IP indemnification carve-outs."),
    _case("legal-acronym-04", category="acronym", query="BAA not in legal corpus trap", gold="eval-harness/legal-contracts/vendor-contract-checklist.md", chunk=["DPA"], answer="Legal corpus uses DPA not BAA.", alts=["DPA attached"]),
    _case("legal-version-01", category="version_trap", query="10000 flat liability cap MSA", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["twelve (12) months"], answer="Archived v1 $10k cap is obsolete.", forbidden=["eval-harness/legal-contracts/msa-template-v1-archived.md"], traps=["eval-harness/legal-contracts/msa-template-v1-archived.md"]),
    _case("legal-version-02", category="version_trap", query="California governing law archived MSA", gold="eval-harness/legal-contracts/msa-template-v3.md", chunk=["Delaware"], answer="v1 California law superseded by v3 Delaware.", forbidden=["eval-harness/legal-contracts/msa-template-v1-archived.md"], traps=["eval-harness/legal-contracts/msa-template-v1-archived.md"]),
    _case("legal-cross-01", category="cross_doc", query="checklist DPA liability cap alignment", gold="eval-harness/legal-contracts/vendor-contract-checklist.md", chunk=["liability cap matches"], answer="Checklist requires cap matches liability-cap-clause library."),
    _case("legal-cross-02", category="cross_doc", query="DPA subprocessors liability library reference", gold="eval-harness/legal-contracts/liability-cap-clause.md", chunk=["Data Processing Addendum"], answer="Liability library cross-refs DPA for subprocessors."),
]

MEDICAL = [
    _case("med-direct-01", category="direct", query="HIPAA security officer requirement", gold="eval-harness/medical-admin/hipaa-admin-safeguards.md", chunk=["security officer"], answer="Designate a security officer."),
    _case("med-direct-02", category="direct", query="PHI minimum necessary access rule", gold="eval-harness/medical-admin/phi-handling-procedure.md", chunk=["Minimum necessary"], answer="Access only PHI needed for current task."),
    _case("med-direct-03", category="direct", query="BAA before vendor PHI access", gold="eval-harness/medical-admin/baa-requirements.md", chunk=["before access"], answer="Execute BAA before granting vendor access."),
    _case("med-direct-04", category="direct", query="audit log retention HIPAA years", gold="eval-harness/medical-admin/hipaa-admin-safeguards.md", chunk=["six years"], answer="Retain audit trails six years."),
    _case("med-direct-05", category="direct", query="influenza vaccination patient facing staff", gold="eval-harness/medical-admin/employee-health-policy.md", chunk=["influenza vaccination"], answer="Annual flu vaccine required for patient-facing roles."),
    _case("med-direct-06", category="direct", query="PHI transmission encrypted email", gold="eval-harness/medical-admin/phi-handling-procedure.md", chunk=["encrypted email"], answer="Use encrypted email or secure transfer."),
    _case("med-direct-07", category="direct", query="BAA breach notification days", gold="eval-harness/medical-admin/baa-requirements.md", chunk=["60 days"], answer="Breach notification within 60 days."),
    _case("med-direct-08", category="direct", query="TB screening hire policy", gold="eval-harness/medical-admin/employee-health-policy.md", chunk=["TB screening"], answer="TB screening at hire and after exposure."),
    _case("med-synonym-01", category="synonym", query="protected health information examples list", gold="eval-harness/medical-admin/phi-handling-procedure.md", chunk=["medical record numbers"], answer="PHI includes names, MRNs, billing identifiers.", alts=["PHI"]),
    _case("med-synonym-02", category="synonym", query="business associate agreement vendor disabled accounts", gold="eval-harness/medical-admin/baa-requirements.md", chunk=["remain disabled"], answer="Accounts disabled without signed BAA.", alts=["BAA"]),
    _case("med-synonym-03", category="synonym", query="automatic logoff access control safeguard", gold="eval-harness/medical-admin/hipaa-admin-safeguards.md", chunk=["automatic logoff"], answer="Automatic logoff is required control.", alts=["access control"]),
    _case("med-synonym-04", category="synonym", query="N95 fit testing high risk units", gold="eval-harness/medical-admin/employee-health-policy.md", chunk=["Fit testing"], answer="Fit testing for N95 in high-risk units.", alts=["respirators"]),
    _case("med-acronym-01", category="acronym", query="PHI acronym definition", gold="eval-harness/medical-admin/phi-handling-procedure.md", chunk=["Protected Health Information"], answer="Protected Health Information."),
    _case("med-acronym-02", category="acronym", query="BAA acronym HIPAA vendor", gold="eval-harness/medical-admin/baa-requirements.md", chunk=["Business Associate Agreement"], answer="Business Associate Agreement."),
    _case("med-acronym-03", category="acronym", query="ePHI audit controls HIPAA", gold="eval-harness/medical-admin/hipaa-admin-safeguards.md", chunk=["ePHI"], answer="Audit controls for systems with ePHI."),
    _case("med-acronym-04", category="acronym", query="CDC hand hygiene alcohol rub outdated doc", gold="eval-harness/medical-admin/employee-health-policy.md", chunk=["Fit testing"], answer="2019 infection doc outdated on hand hygiene.", traps=["eval-harness/medical-admin/outdated-infection-control-2019.md"]),
    _case("med-version-01", category="version_trap", query="24-hour influenza room quarantine 2019 policy", gold="eval-harness/medical-admin/employee-health-policy.md", chunk=["influenza"], answer="2019 isolation duration superseded.", forbidden=["eval-harness/medical-admin/outdated-infection-control-2019.md"], traps=["eval-harness/medical-admin/outdated-infection-control-2019.md"]),
    _case("med-version-02", category="version_trap", query="soap and water only hand hygiene archived", gold="eval-harness/medical-admin/hipaa-admin-safeguards.md", chunk=["Audit controls"], answer="Archived infection control soap-only guidance obsolete.", forbidden=["eval-harness/medical-admin/outdated-infection-control-2019.md"], traps=["eval-harness/medical-admin/outdated-infection-control-2019.md"]),
    _case("med-cross-01", category="cross_doc", query="employee health HIPAA logging coordination", gold="eval-harness/medical-admin/employee-health-policy.md", chunk=["HIPAA admin safeguards"], answer="Employee health coordinates with HIPAA admin safeguards logging."),
    _case("med-cross-02", category="cross_doc", query="BAA subprocessors PHI procedure alignment", gold="eval-harness/medical-admin/baa-requirements.md", chunk=["subcontractor"], answer="BAA requires subcontractor flow-down aligned with PHI procedure."),
]

GARBAGE = [
    _case("garb-direct-01", category="direct", query="45 day return receipt unworn policy B", gold="eval-harness/mixed-garbage/contradictory-refund-policy-b.md", chunk=["45-day"], answer="Policy B allows 45-day returns with receipt.", traps=["eval-harness/mixed-garbage/contradictory-refund-policy-a.md"]),
    _case("garb-direct-02", category="direct", query="all sales final 7 days footwear policy A", gold="eval-harness/mixed-garbage/contradictory-refund-policy-a.md", chunk=["All sales final"], answer="Policy A says all sales final after 7 days.", traps=["eval-harness/mixed-garbage/contradictory-refund-policy-b.md"]),
    _case("garb-direct-03", category="direct", query="duplicate CMS export return policy filename", gold="eval-harness/mixed-garbage/doc(1)-copy.md", chunk=["doc(1)-copy"], answer="Duplicate export with wrong metadata."),
    _case("garb-direct-04", category="direct", query="partial PDF page 3 HIPAA BAA excerpt", gold="eval-harness/mixed-garbage/partial-extract-page3-only.md", chunk=["BAA required"], answer="Partial extract mentions BAA but truncated table."),
    _case("garb-direct-05", category="direct", query="README.old.bak authoritative?", gold="eval-harness/mixed-garbage/README.old.bak.md", chunk=["Not authoritative"], answer="Explicitly not authoritative."),
    _case("garb-direct-06", category="direct", query="random meeting notes retrieval noise", gold="eval-harness/mixed-garbage/random-meeting-notes-shoes.md", chunk=["Do not use"], answer="Meeting notes marked do not use for benchmarks."),
    _case("garb-direct-07", category="direct", query="acronym soup memo policy document?", gold="eval-harness/mixed-garbage/acronym-soup-internal-memo.md", chunk=["Not a policy"], answer="Not a policy document."),
    _case("garb-direct-08", category="direct", query="restocking fee 15% furniture policy B", gold="eval-harness/mixed-garbage/contradictory-refund-policy-b.md", chunk=["15%"], answer="Policy B includes 15% restocking on furniture."),
    _case("garb-synonym-01", category="synonym", query="customer success refund window forty five days", gold="eval-harness/mixed-garbage/contradictory-refund-policy-b.md", chunk=["45-day"], answer="45-day returns per Policy B.", alts=["45 day"]),
    _case("garb-synonym-02", category="synonym", query="finance draft no returns footwear week", gold="eval-harness/mixed-garbage/contradictory-refund-policy-a.md", chunk=["7 days"], answer="Policy A 7-day limit.", alts=["all sales final"]),
    _case("garb-synonym-03", category="synonym", query="legacy export broken encoding refund", gold="eval-harness/mixed-garbage/README.old.bak.md", chunk=["Refund window 30 days"], answer="Broken README mentions 30 days inconsistently."),
    _case("garb-synonym-04", category="synonym", query="truncated compliance paragraph full documents", gold="eval-harness/mixed-garbage/partial-extract-page3-only.md", chunk=["Use full source"], answer="Partial extract advises using full sources."),
    _case("garb-acronym-01", category="acronym", query="PHI IAM VPC BAA same memo", gold="eval-harness/mixed-garbage/acronym-soup-internal-memo.md", chunk=["PHI"], answer="Acronym soup lists PHI/IAM/VPC/BAA without definitions."),
    _case("garb-acronym-02", category="acronym", query="TF vs Terraform vs tfvars training note", gold="eval-harness/mixed-garbage/acronym-soup-internal-memo.md", chunk=["TF vs Terraform"], answer="Training note on TF/Terraform/tfvars confusion."),
    _case("garb-acronym-03", category="acronym", query="RMA-RET-2024 mentioned broken readme", gold="eval-harness/mixed-garbage/README.old.bak.md", chunk=["RMA-RET-2024"], answer="Random SKU/RMA mentions in broken README."),
    _case("garb-acronym-04", category="acronym", query="VPC Virtual Private truncated table partial", gold="eval-harness/mixed-garbage/partial-extract-page3-only.md", chunk=["Virtual Pr"], answer="Partial table truncates VPC definition."),
    _case("garb-version-01", category="version_trap", query="30 day refund README legacy export", gold="eval-harness/mixed-garbage/contradictory-refund-policy-b.md", chunk=["45-day"], answer="README 30-day claim contradicts Policy B 45-day.", forbidden=["eval-harness/mixed-garbage/README.old.bak.md"], traps=["eval-harness/mixed-garbage/README.old.bak.md"]),
    _case("garb-version-02", category="version_trap", query="duplicate return policy copy authoritative", gold="eval-harness/mixed-garbage/contradictory-refund-policy-b.md", chunk=["45-day"], answer="doc(1)-copy is duplicate not authoritative source.", forbidden=["eval-harness/mixed-garbage/doc(1)-copy.md"], traps=["eval-harness/mixed-garbage/doc(1)-copy.md"]),
    _case("garb-cross-01", category="cross_doc", query="contradictory refund policies legal review 2022", gold="eval-harness/mixed-garbage/contradictory-refund-policy-a.md", chunk=["legal review pending 2022"], answer="Both policies note pending 2022 legal review."),
    _case("garb-cross-02", category="cross_doc", query="meeting notes invoice cafeteria unrelated noise", gold="eval-harness/mixed-garbage/random-meeting-notes-shoes.md", chunk=["three-way matching"], answer="Meeting mixes shoes, invoices, cafeteria — noise only."),
]

SHEETS = {
    "retail-furniture": (RETAIL, "eval-retail"),
    "terraform-cloud": (TERRAFORM, "eval-terraform"),
    "legal-contracts": (LEGAL, "eval-legal"),
    "medical-admin": (MEDICAL, "eval-medical"),
    "mixed-garbage": (GARBAGE, "eval-garbage"),
}


def write_qrels() -> None:
    for corpus_id, (cases, topic) in SHEETS.items():
        for case in cases:
            case["topic"] = topic
        out = HARNESS_ROOT / "corpora" / corpus_id / "qrels.json"
        sheet = {
            "version": 1,
            "description": f"Pilot qrels ({len(cases)}/100 target) — {corpus_id}",
            "corpus_id": corpus_id,
            "topic": topic,
            "top_k": 5,
            "pilot": True,
            "target_cases": 100,
            "category_mix_target": {
                "direct": 40,
                "synonym": 20,
                "acronym": 20,
                "version_trap": 10,
                "cross_doc": 10,
            },
            "cases": cases,
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sheet, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out} ({len(cases)} cases)")


if __name__ == "__main__":
    write_qrels()
