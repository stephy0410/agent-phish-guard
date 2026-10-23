# Security

Agent Phish Guard is a defense against indirect prompt injection. A bypass is a security issue, and we want to hear about it.

## Reporting a vulnerability

Please report privately through GitHub's **Report a vulnerability** button, under this repository's Security tab, rather than in a public issue. If the button isn't there, open an issue that asks for a private contact, without details. Include the text or page that gets through, the guardrail it gets past, and what the agent did. We aim to answer within a week.

These are in scope:
- An injection that reaches the model with the chunk scanner on.
- A sensitive tool call with the user's data that the tool-call guard lets run without the user's consent.
- A way to make either guardrail fail open.

## What the guardrails do not cover

The known limits are documented, and reports that only restate them are not vulnerabilities:
- **Payloads split across chunks.** Each chunk is judged alone; the tool-call guard is the defense for split payloads.
- **Encodings beyond the deobfuscator.** It reveals invisible characters, maps Greek and Cyrillic look-alikes and decodes base64. Other encodings, and text inside images, pass through.
- **Thresholds.** They are calibrated on a 120-chunk dataset written by the team ([docs/calibration.md](docs/calibration.md)), so some attacks score below them.
- **Tools outside `sensitive_tools`.** The tool-call guard only checks the tools listed there.

The full lists are in [docs/scanner.md](docs/scanner.md#limitations) and [docs/tool-guard.md](docs/tool-guard.md#limitations).

## Testing safely

Test against the sandbox pages in `eval/pages/` and `eval/demo-store/`, or against pages you own. Do not point the agent at real sites that host attacks. `send_data` never sends anything: it only records what it would have sent.
