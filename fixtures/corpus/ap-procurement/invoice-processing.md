# Invoice Processing Automation

## Overview

Invoice processing automation extracts header and line fields from PDF and EDI invoices, validates them against procurement master data, and routes approvals. Goals include faster cycle time, fewer manual keying errors, and earlier fraud detection.

## Typical workflow

Invoices arrive via email, supplier portal, or EDI. OCR or structured parsers populate vendor ID, invoice number, PO reference, line amounts, and tax. The system performs duplicate detection, three-way matching, and GL coding before posting to the ERP.

## Controls

Segregation of duties separates invoice entry, approval, and payment. Audit logs retain original images and field-level changes. Integration with vendor master and contract pricing reduces off-contract spend.
