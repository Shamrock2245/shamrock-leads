import sys
from bs4 import BeautifulSoup
import re

with open(sys.argv[1], 'r') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

text = soup.get_text(separator='\n')
# Remove excessive blank lines
text = re.sub(r'\n\s*\n', '\n\n', text)
print(text)
