import httpx

try:
    print("Testing with proxy 172.18.0.1:1080")
    client = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        proxy="socks5://172.18.0.1:1080",
        timeout=10,
        verify=False
    )
    resp = client.get("https://cms.revize.com/revize/apps/sarasota/index.php")
    print(f"Status: {resp.status_code}")
    print(f"Content length: {len(resp.text)}")
    if "Just a moment" in resp.text:
        print("Blocked by Cloudflare via proxy.")
    else:
        print("Success! Bypassed CF via proxy!")
except Exception as e:
    print(f"Error testing proxy: {e}")

try:
    print("\nTesting without proxy")
    client = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=10,
        verify=False
    )
    resp = client.get("https://cms.revize.com/revize/apps/sarasota/index.php")
    print(f"Status: {resp.status_code}")
    print(f"Content length: {len(resp.text)}")
    if "Just a moment" in resp.text:
        print("Blocked by Cloudflare without proxy.")
    else:
        print("Success! Bypassed CF without proxy!")
except Exception as e:
    print(f"Error testing without proxy: {e}")
