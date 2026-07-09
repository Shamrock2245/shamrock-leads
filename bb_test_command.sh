#!/bin/bash
# BB Send Test — sends to Brendan's personal number
ssh root@178.156.179.237 "docker exec shamrock-dashboard python3 -c \"
import asyncio, os, sys
sys.path.insert(0, '/app')
os.environ['BLUEBUBBLES_URL_0178'] = 'https://difficulty-dean-equations-purchased.trycloudflare.com'
os.environ['BLUEBUBBLES_PASSWORD_0178'] = os.environ.get('BLUEBUBBLES_PASSWORD_0178') or os.environ.get('BB_PASSWORD', '')
from dashboard.extensions import init_bluebubbles
init_bluebubbles()
from dashboard.services.bb_client import get_default_bb_client
async def test():
    bb = get_default_bb_client()
    result = await bb.send_text('any;-;+12393197008', 'BB test - pipeline restored')
    print('SUCCESS' if result.get('success') else f'FAIL: {result}')
asyncio.run(test())
\""
