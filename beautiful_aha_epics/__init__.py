# Beautiful Aha Epics
# CLI to validate Aha! epics against configurable rules.
#
# Usage (after installing deps from requirements.txt):
#   export BAE_AHA_ACCOUNT=bigblue
#   export BAE_AHA_TOKEN={{AHA_API_TOKEN}}
#   python -m beautiful_aha_epics check \
#     --product-name "DATALIN Data Lineage by Manta" \
#     --releases "June 2026 - IKC 5.4 and DI 2.4" --tags scanners \
#     --pm-owner "wojtek smajda"
#
# Or install an entrypoint script `bin/bae` provided in this repo.
