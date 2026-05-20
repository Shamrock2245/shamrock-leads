import os

api_dir = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/api"
routers_dir = "/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers"

api_files = {f for f in os.listdir(api_dir) if f.endswith(".py") and f != "__init__.py"}
router_files = {f for f in os.listdir(routers_dir) if f.endswith(".py") and f != "__init__.py" and f != "helpers.py"}

only_in_api = api_files - router_files
only_in_routers = router_files - api_files

print(f"Total legacy API files: {len(api_files)}")
print(f"Total migrated router files: {len(router_files)}")

if only_in_api:
    print(f"\nFiles ONLY in legacy API directory (NOT MIGRATED!):")
    for f in sorted(only_in_api):
        print(f"  - {f}")
else:
    print("\n✅ All legacy API files have matching files in the new routers directory!")

if only_in_routers:
    print(f"\nNew files only in the routers directory:")
    for f in sorted(only_in_routers):
        print(f"  - {f}")
