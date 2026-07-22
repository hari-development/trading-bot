import sys
import os

try:
    from kiteconnect import KiteConnect
except ImportError:
    print("Error: kiteconnect is not installed. Please run:")
    print("  venv/bin/pip install kiteconnect --break-system-packages")
    sys.exit(1)

def main():
    print("=== Zerodha Kite Connect Daily Token Generator ===")
    api_key = input("Enter your KITE_API_KEY: ").strip()
    api_secret = input("Enter your KITE_API_SECRET: ").strip()
    
    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    
    print("\n1. Copy and paste this URL into your web browser:")
    print(f"   {login_url}")
    print("\n2. Log in with your regular Zerodha credentials and approve permissions.")
    print("3. You will be redirected to a blank page (or localhost). Look at the URL bar and copy the 'request_token' parameter.")
    print("   Example redirect URL: https://127.0.0.1/?status=success&request_token=ABC123XYZ456")
    
    request_token = input("\nEnter the request_token from the URL bar: ").strip()
    
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        print("\n=== SUCCESS ===")
        print("Copy and paste these commands into your terminal to go live:\n")
        print(f'export KITE_API_KEY="{api_key}"')
        print(f'export KITE_API_SECRET="{api_secret}"')
        print(f'export KITE_ACCESS_TOKEN="{access_token}"')
        print("\nThen run: python main.py --mode LIVE")
    except Exception as e:
        print(f"\nError generating session: {e}")

if __name__ == "__main__":
    main()
