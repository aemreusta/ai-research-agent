"""PII handling at the three boundaries: intake, telemetry, output (architecture v0.6 §12).

* **Intake** (`masking`) - the user's question is masked before it reaches the database, an LLM
  or a search provider.
* **Telemetry** - `observability.redaction`, one-way, applied to every log line and event.
* **Output** - output gate rule G9.

Sensitive identifiers (national id, IBAN, card, phone, email, IP) are always masked. Person and
company names in web content are not: public figures and companies are the subject of the
research, so masking them would defeat the question (D20).
"""
