import sys
import os
import glob
import importlib
import inspect

REPO_PATH = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads"
sys.path.insert(0, REPO_PATH)

from scrapers.base_scraper import BaseScraper
from core.models import ArrestRecord

# Find all county scraper files
scraper_files = glob.glob(os.path.join(REPO_PATH, "scrapers/counties/*.py"))
scraper_files = [f for f in scraper_files if not f.endswith("__init__.py")]

print(f"Found {len(scraper_files)} scraper files to audit.\n")

errors = []
warnings = []
successes = []

# Valid ArrestRecord fields
valid_fields = set(ArrestRecord.__dataclass_fields__.keys())

for fpath in sorted(scraper_files):
    fname = os.path.basename(fpath)
    module_name = f"scrapers.counties.{fname[:-3]}"
    
    try:
        # Import the module
        mod = importlib.import_module(module_name)
    except Exception as e:
        errors.append((fname, f"Import failed: {e}"))
        continue
        
    # Find any subclass of BaseScraper
    scraper_classes = []
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, BaseScraper) and obj is not BaseScraper:
            scraper_classes.append(obj)
            
    if not scraper_classes:
        warnings.append((fname, "No subclass of BaseScraper found in module"))
        continue
        
    for cls in scraper_classes:
        class_name = cls.__name__
        try:
            # Instantiate the class
            instance = cls()
            
            # Check 'county' property/attribute
            if not hasattr(instance, "county"):
                errors.append((fname, f"Class {class_name} has no 'county' property/attribute"))
                continue
                
            county_name = instance.county
            if not county_name or not isinstance(county_name, str):
                errors.append((fname, f"Class {class_name}.county returned invalid value: {county_name}"))
                continue
                
            # Check 'scrape' method
            if not hasattr(instance, "scrape") or not callable(getattr(instance, "scrape")):
                errors.append((fname, f"Class {class_name} has no callable 'scrape' method"))
                continue
                
            successes.append((fname, class_name, county_name))
        except Exception as e:
            errors.append((fname, f"Instantiating/inspecting {class_name} failed: {e}"))

print(f"--- SUCCESSES ({len(successes)}) ---")
for fname, cname, county in successes:
    print(f"  {fname}: {cname} (County: {county})")

print(f"\n--- WARNINGS ({len(warnings)}) ---")
for fname, msg in warnings:
    print(f"  {fname}: {msg}")

print(f"\n--- ERRORS ({len(errors)}) ---")
for fname, msg in errors:
    print(f"  {fname}: {msg}")
