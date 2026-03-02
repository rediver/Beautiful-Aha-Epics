# BeautifulEpics✨
A colorful CLI for keeping Aha! epics beautiful 

## Installation
- Python 3.10+
- Install dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration (bae.config.yaml)
Minimal example for DATALIN:
```yaml
account: bigblue
product_key: DATALIN
auth:
  token: "<YOUR_AHA_API_TOKEN>"   # or via env: BAE_AHA_TOKEN
filters:
  # Default releases — used by 'beauty' without flags
  release_ids:
    - "7515164732697196802"  # June 2026 - IKC 5.4 and DI 2.4
    - "7549195962065426819"  # Planned to remove — Q3 2026
    - "7549196114077775538"  # Dec 2026 - IKC 5.5 and DI 2.5
  # Tag filter on FEATURES (children):
  tags_include: ["scanners"]
  tags_one_of: []
  pm_owner: "wojciech.smajda@ibm.com, Yurii.Plakhtii@ibm.com"  # comma-separated list allowed
fields:
  solution_value_statement: client_value_statement
  risk_status: risk_status
  commitment: commitment
  master_epic: ibm_software_only_managed_tags_master_epics
  github_link: [github_link, integrations_to]
  product_management_owner: product_management_owner
  development_owner: development_owner
  ibm_software_gtm_themes: ibm_software_gtm_themes
  priority_data_ai: priority
```

## Environment (optional)
- BAE_AHA_ACCOUNT, BAE_AHA_TOKEN — authentication
- BAE_MAX_CONCURRENCY — parallelism for fetching features (default 15)

## Usage
- Easiest way (reads config and runs `check`):
```bash
./beauty
```
- Verification mode (show only the verification table with all validated fields):
```bash
./beauty --verify
# or
./beauty -v
```
- Sort output (works with verify and base results):
```bash
./beauty -v -s status      # by status
./beauty -v -s release     # by release name
./beauty -s ref            # by reference in base report
```
- Debug logs (verbose output for troubleshooting):
```bash
./beauty --debug
```
- Help (flags for the `check` command):
```bash
./beauty --help
```
- Other commands (examples):
```bash
./beauty list-features 7515164732697196802
./beauty show-feature DATALIN-457 --raw
./beauty find-epic "DATALIN-457"
```

## Exit codes
- 0 — everything is beautiful ✨
- 1 — issues found 💔
- 2 — configuration/authentication error ⚠️

## What we validate (feature‑level)
A feature (specific “epic”) is marked as NOT‑beautiful if any of the following is true:
- missing description (empty `description.body` after stripping HTML)
- current status (from `workflow_status_times`) == New
- empty Solution Value Statement (`client_value_statement`)
- empty Risk Status
- empty Commitment (`commitment/committed`)
- missing Release or missing `start_date`/`release_date`
- missing Master Epic (relation `epic/master_feature` or managed tag)
- no GitHub link/integration (including Enterprise)
- missing required `scanners` tag (on the feature)
- Product Management owner empty OR different from `pm_owner` (from config)
- Development owner empty
- IBM Software GTM Themes empty
- Priority (Data & AI) empty or outside 1..10

## Fast selection of items to check
- From releases in `filters.release_ids` we fetch features with the `scanners` tag (server‑side filter) and concurrently pull details.
- We include only those where PM owner email is empty OR equals "wojciech.smajda@ibm.com".

## Tips
- Best to keep the token in env (`BAE_AHA_TOKEN`) or set it interactively: `./bin/bae auth-set-token`.
- If you want to speed things up: `export BAE_MAX_CONCURRENCY=25`.
- Common failure reasons:
  - empty Risk Status
  - empty Commitment
  - empty Release
  - empty Master Epic
  - no GitHub integration/link present
  - missing `scanners` tag OR none of [`lineage`, `dev`, `commited`]
  - Product Management owner empty or not matching expected
  - Development owner empty
  - IBM Software GTM Themes empty
  - Priority (Data & AI) empty or not an integer 1–10

## Notes
- Token can be stored in `bae.config.yaml` (local only) or via env var `BAE_AHA_TOKEN`. Env var wins.
- We use Aha! API v1 with Bearer auth.
