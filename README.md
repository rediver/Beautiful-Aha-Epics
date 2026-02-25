# Beautiful Aha Epics (🦋✨)
Keep your Aha! epics beautiful, colorful and IBM‑PM compliant.

```
██████  ███████  █████  ██    ██ ███████ ████████ ██    ██ ██      
██   ██ ██      ██   ██ ██    ██ ██         ██     ██  ██  ██      
██████  █████   ███████ ██    ██ ███████    ██      ████   ██      
██   ██ ██      ██   ██  ██  ██       ██    ██       ██    ██      
██   ██ ███████ ██   ██   ████   ███████    ██       ██    ███████ 
```

## Installation
- Python 3.10+
- Install deps:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration
- Copy the example and edit it:
```bash
cp bae.config.example.yaml bae.config.yaml
```
- Set your Aha! account and token in `bae.config.yaml`:
```yaml
account: bigblue
auth:
  token: "<YOUR_AHA_API_TOKEN>"
product_name: "Data Lineage by Manta"
product_path: [
  "IBM",
  "IBM Software",
  "Data Platform",
  "Data Fabric",
  "Data Intelligence & Data Integration",
  "Data Intelligence",
  "Master Data Management Family",
  "Data Lineage by Manta"
]
filters:
  releases: ["June 2026 - IKC 5.4 and DI 2.4"]
  tags_include: ["scanners"]
  tags_one_of: ["lineage dev commited"]
  pm_owner: "wojtek smajda"
```
- Alternatively, you can keep the token in an env var instead of the file:
```bash
export BAE_AHA_TOKEN=<YOUR_AHA_API_TOKEN>
export BAE_AHA_ACCOUNT=bigblue
```

To save the token interactively into `bae.config.yaml`:
```bash
./bin/bae auth-set-token
```

## Usage
- The simplest way (reads `bae.config.yaml`):
```bash
./check
```
- Or explicitly (name):
```bash
./bin/bae check --product-name "Data Lineage by Manta"
```
- Or explicitly (full path, multiple flags in order):
```bash
./bin/bae check \
  --product-path "IBM" \
  --product-path "IBM Software" \
  --product-path "Data Platform" \
  --product-path "Data Fabric (17DSR)" \
  --product-path "Data Intelligence & Data Integration" \
  --product-path "Data Intelligence (20A11)" \
  --product-path "Master Data Management Family" \
  --product-path "Data Lineage by Manta"
```
- JSON for scripts/CI:
```bash
./bin/bae check --json
```

Exit codes:
- 0 — all checked epics are beautiful ✨
- 1 — some epics need love 💔
- 2 — configuration/auth error ⚠️

## Filters (easy to tweak)
- Releases: names must match Aha! release names in the product.
- Tags: must include all from `tags_include` and at least one from `tags_one_of`.
- Product Management owner expected value (`filters.pm_owner`).
- Field mappings for your tenant can be customized under `fields`.

## Business logic (what makes an epic NOT beautiful)
An epic is flagged if ANY of the below is true:
- missing description
- status equals "New"
- empty Solution Value Statement
- empty risk status
- empty Commitment
- empty Release
- empty Master Epic
- no GitHub integration/link present
- missing tag `scanners` OR none of [`lineage dev commited`]
- Product Management owner empty or not matching expected
- Development owner empty
- IBM Software GTM Themes empty
- Priority (Data & AI) empty or not an integer 1–10

## Notes
- Token can be stored in `bae.config.yaml` (local only) or via env var `BAE_AHA_TOKEN`. Env var wins.
- We use Aha! API v1 with Bearer auth.