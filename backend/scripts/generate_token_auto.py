import sys
import os
import urllib.parse

try:
    import requests
    import pyotp
    from kiteconnect import KiteConnect
except ImportError:
    print("Error: Required packages missing. Please install them using:")
    print("  venv/bin/pip install requests pyotp kiteconnect --break-system-packages")
    sys.exit(1)

def main():
    print("=== Automated Zerodha Kite Connect Daily Token Generator ===")
    
    # Load credentials from environment variables first, fallback to user inputs
    api_key = os.environ.get("KITE_API_KEY") or input("Enter KITE_API_KEY: ").strip()
    api_secret = os.environ.get("KITE_API_SECRET") or input("Enter KITE_API_SECRET: ").strip()
    username = os.environ.get("ZERODHA_USERNAME") or input("Enter Zerodha User ID: ").strip()
    password = os.environ.get("ZERODHA_PASSWORD") or input("Enter Zerodha Password: ").strip()
    
    print("\n*Note: To automate TOTP, retrieve the alphanumeric TOTP Secret Setup Key")
    print(" from Zerodha Profile Security > Enable External TOTP.")
    totp_secret = os.environ.get("ZERODHA_TOTP_SECRET") or input("Enter Zerodha TOTP Secret Key: ").strip()

    if not all([api_key, api_secret, username, password, totp_secret]):
        print("\nError: Missing required login credentials.")
        sys.exit(1)

    print("\nAttempting programmatic login to Zerodha...")
    session = requests.Session()
    
    try:
        # Step 1: Login request to get request_id
        login_url = "https://kite.zerodha.com/api/login"
        payload = {"user_id": username, "password": password}
        res = session.post(login_url, data=payload)
        res_data = res.json()
        
        if res_data.get("status") != "success":
            print(f"Login failed: {res_data}")
            sys.exit(1)
            
        request_id = res_data["data"]["request_id"]
        
        # Step 2: Generate current 2FA TOTP code and submit
        totp = pyotp.TOTP(totp_secret.replace(" ", ""))
        twofa_pin = totp.now()
        print(f"Generated 2FA PIN: {twofa_pin}")
        
        twofa_url = "https://kite.zerodha.com/api/twofa"
        twofa_payload = {
            "user_id": username,
            "request_id": request_id,
            "twofa_value": twofa_pin,
            "twofa_type": "totp"
        }
        res_2fa = session.post(twofa_url, data=twofa_payload)
        res_2fa_data = res_2fa.json()
        
        if res_2fa_data.get("status") != "success":
            print(f"2FA validation failed: {res_2fa_data}")
            sys.exit(1)
            
        # Step 3: Trigger login redirect chain to retrieve redirect URL containing request_token
        connect_url = f"https://kite.trade/connect/login?api_key={api_key}"
        res_connect = session.get(connect_url, allow_redirects=True)
        final_url = res_connect.url
        
        parsed_url = urllib.parse.urlparse(final_url)
        params = urllib.parse.parse_qs(parsed_url.query)
        
        request_token_list = params.get("request_token")
        if not request_token_list:
            print(f"Failed to fetch request_token. Redirect URL: {final_url}")
            sys.exit(1)
            
        request_token = request_token_list[0]
        print(f"Extracted request_token: {request_token}")
        
        # Step 4: Exchange request_token for daily access_token via Kite Connect SDK
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        
        print("\n=== SUCCESS ===")
        print("Copy and paste these commands into your terminal to go live:\n")
        print(f'export KITE_API_KEY="{api_key}"')
        print(f'export KITE_API_SECRET="{api_secret}"')
        print(f'export KITE_ACCESS_TOKEN="{access_token}"')
        print("\nThen run: python main.py --mode LIVE")
        
    except Exception as e:
        print(f"\nError during automated login: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
