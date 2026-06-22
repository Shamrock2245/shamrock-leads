import re

file_path = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/services/signnow_packet_service.py'
with open(file_path, 'r') as f:
    content = f.read()

new_method = """
    async def download_document_group(self, group_id: str) -> bytes:
        \"\"\"
        Download the completed document group as a single merged PDF.
        \"\"\"
        await self._get_token()
        url = f"{self.base_url}/document-group/{group_id}/download"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/pdf"
        }
        
        # We need type='merged' query parameter
        params = {"type": "merged"}
        
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            return r.content
"""

if "download_document_group" not in content:
    with open(file_path, 'a') as f:
        f.write(new_method)
    print("Method download_document_group added.")
else:
    print("Method already exists.")
