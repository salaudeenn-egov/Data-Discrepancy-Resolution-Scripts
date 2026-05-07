import requests
import warnings

warnings.filterwarnings("ignore")


def get_token(api_base, username, password, tenant_id, client_id="health-campaign-collection-app", client_secret="secret"):
    """
    Fetch OAuth token from HCM.
    client_id / client_secret come from the campaign's TST Checklist.
    Alternatively, paste the token directly in the notebook widget.
    """
    url = f"{api_base}/user/oauth/token"
    data = {
        "username": username,
        "password": password,
        "grant_type": "password",
        "scope": "read",
        "tenantId": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(url, data=data, headers=headers, verify=False, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]
