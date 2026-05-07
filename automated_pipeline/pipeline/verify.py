import time
import warnings

import requests
from tqdm import tqdm

warnings.filterwarnings("ignore")

_DELAY = 0.3


def _chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def verify_beneficiaries(api_base, tenant_id, auth_token, ids, batch_size=50, log=print):
    """
    Check which projectBeneficiaryClientReferenceIds don't exist in the system.
    Returns list of IDs that were NOT found (should be excluded from ingestion).
    """
    url = f"{api_base}/project/beneficiary/v1/_search"
    params = {"limit": batch_size, "offset": 0, "tenantId": tenant_id}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    not_found = []
    batches = list(_chunks(ids, batch_size))

    for batch in tqdm(batches, desc="  Verifying beneficiaries", unit="batch"):
        payload = {
            "RequestInfo": {"authToken": auth_token},
            "ProjectBeneficiary": {"clientReferenceId": batch},
        }
        try:
            r = requests.post(url, headers=headers, params=params, json=payload, timeout=60, verify=False)
            r.raise_for_status()
            returned = r.json().get("ProjectBeneficiaries", [])
            found_ids = {obj.get("clientReferenceId") for obj in returned if obj.get("clientReferenceId")}
            missing = [cid for cid in batch if cid not in found_ids]
            not_found.extend(missing)
        except Exception as e:
            log(f"  Batch failed: {e} — marking all as missing")
            not_found.extend(batch)
        time.sleep(_DELAY)

    log(f"  Verification done. Not found: {len(not_found):,} / {len(ids):,}")
    return not_found


def segregate(df, not_present_ids, id_col):
    """Remove rows whose id_col value is in not_present_ids."""
    remove_set = set(str(x) for x in not_present_ids)
    before = len(df)
    filtered = df[~df[id_col].astype(str).isin(remove_set)].copy()
    removed = before - len(filtered)
    return filtered, removed
