import sys, os, asyncio
sys.path.append('.')
from dotenv import load_dotenv
load_dotenv()
from dashboard.services.signnow_packet_service import SignNowPacketService

async def main():
    svc = SignNowPacketService()
    try:
        results = await svc.validate_templates_exist()
        print(f"Validation results: {results}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
