import asyncio
from dashboard.services.osint_service import get_osint_service

async def main():
    svc = get_osint_service()
    tools = svc.probe_tools()
    print("Tools probe:", tools)
    q = await svc.get_queue_info()
    print("Queue info:", q)

asyncio.run(main())
