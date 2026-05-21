import re

with open("bay_get_raw.html", "r", encoding="utf-8") as f:
    html = f.read()

# Look for Oxx objects
# In UniGUI, objects are initialized like O68=new Ext.button.Button({...}) or similar
matches = re.findall(r'(\bO\d+)\s*=\s*new\s*Ext\.[\w\.]+\(\{([\s\S]*?)\}\);', html)
print(f"Found {len(matches)} UniGUI object initializations:")

search_btn_id = ""
for obj_name, obj_def in matches:
    # Print the name and some details if it looks like a search button
    clean_def = " ".join(obj_def.split())
    if "search" in clean_def.lower() or "find" in clean_def.lower() or "btn" in clean_def.lower() or "button" in clean_def.lower():
        print(f"\n{obj_name}:")
        print(f"  Type: {obj_name}")
        print(f"  Def: {clean_def[:300]}")
        if "click" in clean_def.lower() or "submit" in clean_def.lower():
            print("  -> Candidate for click event!")
            
# Also check for direct Ajax event listeners or forms
print("\n--- AJAX / HandleEvent config ---")
ajax_refs = re.findall(r'(\bO\d+)\.ajaxRequest\b', html)
print(f"ajaxRequest calls on: {ajax_refs}")

event_refs = re.findall(r'hyb.dll/HandleEvent', html)
print(f"HandleEvent refs: {len(event_refs)}")
