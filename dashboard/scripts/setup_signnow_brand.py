import os
import sys
import httpx
import argparse
import base64
from dotenv import load_dotenv

# Ensure we're in the right directory structure to load .env
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
load_dotenv()

def get_token():
    # Use SignNow auth
    client_id = os.getenv("SIGNNOW_CLIENT_ID")
    client_secret = os.getenv("SIGNNOW_CLIENT_SECRET")
    username = os.getenv("SIGNNOW_USERNAME")
    password = os.getenv("SIGNNOW_PASSWORD")
    basic_auth = os.getenv("SIGNNOW_BASIC_AUTH")

    if not basic_auth and client_id and client_secret:
        basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    if not basic_auth or not username or not password:
        print("Missing SignNow credentials in .env")
        sys.exit(1)

    resp = httpx.post(
        "https://api.signnow.com/oauth2/token",
        headers={"Authorization": f"Basic {basic_auth}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "password", "username": username, "password": password, "scope": "*"}
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def create_brand(token, name):
    resp = httpx.post(
        "https://api.signnow.com/v2/brands",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"brand_name": name}
    )
    if resp.status_code >= 400:
        print(resp.json())
        resp.raise_for_status()
    return resp.json()["id"]

def upload_logo(token, brand_id, logo_path):
    import mimetypes
    mime_type, _ = mimetypes.guess_type(logo_path)
    if not mime_type:
        mime_type = "image/png"
        
    with open(logo_path, "rb") as f:
        files = {"file": (os.path.basename(logo_path), f, mime_type)}
        resp = httpx.post(
            f"https://api.signnow.com/v2/brands/{brand_id}/resources/logo",
            headers={"Authorization": f"Bearer {token}"},
            files=files
        )
        if resp.status_code >= 400:
            print(resp.json())
            resp.raise_for_status()
        print("Logo uploaded successfully.")

def configure_email(token, brand_id, hex_color):
    resp = httpx.put(
        f"https://api.signnow.com/v2/brands/{brand_id}/resources/email-invite",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "from_name": "Shamrock Bail Bonds",
            "button_bg_color": hex_color,
            "button_text_color": "#FFFFFF",
            "footer_text": "Shamrock Bail Bonds - The Digital Bail Agency"
        }
    )
    if resp.status_code >= 400:
        print(resp.json())
        resp.raise_for_status()
    print("Email configured successfully.")

def main():
    parser = argparse.ArgumentParser(description="Configure SignNow Brand")
    parser.add_argument("--name", default="Shamrock Bail Bonds", help="Brand name")
    parser.add_argument("--logo", help="Path to local logo file (png, jpg, svg)", required=True)
    parser.add_argument("--hex", default="#228B22", help="Brand HEX color (e.g., #228B22 for Shamrock Green)")
    args = parser.parse_args()

    if not os.path.exists(args.logo):
        print(f"Error: Logo file not found at {args.logo}")
        sys.exit(1)

    print("Authenticating...")
    token = get_token()
    print("Successfully authenticated with SignNow.")

    print(f"Creating brand: '{args.name}'...")
    brand_id = create_brand(token, args.name)
    print(f"Created Brand ID: {brand_id}")

    print(f"Uploading logo: {args.logo}...")
    upload_logo(token, brand_id, args.logo)
    
    print(f"Configuring email with theme color: {args.hex}...")
    configure_email(token, brand_id, args.hex)

    print(f"\n✅ Setup Complete! Add the following to your .env file:\n\nSIGNNOW_BRAND_ID={brand_id}\n")

if __name__ == "__main__":
    main()
