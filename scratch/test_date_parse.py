from datetime import datetime
import re

def parse_irc_date_time(raw_str):
    if not raw_str:
        return "", ""
    # remove "th", "rd", "st", "nd" from ordinal numbers to parse easily
    clean_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", raw_str)
    # E.g. "May 27, 2026 at 2:46 am"
    print(f"Cleaned string: '{clean_str}'")
    try:
        dt = datetime.strptime(clean_str, "%B %d, %Y at %I:%M %p")
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception as e:
        print(f"Error parsing: {e}")
        # Fallback to simple extractors
        date_match = re.search(r"([A-Za-z]+\s+\d+,\s+\d{4})", clean_str)
        time_match = re.search(r"(\d+:\d+\s+[ap]m)", clean_str, re.IGNORECASE)
        d_val = ""
        t_val = ""
        if date_match:
            try:
                d_val = datetime.strptime(date_match.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
            except Exception:
                pass
        if time_match:
            t_val = time_match.group(1)
        return d_val, t_val

if __name__ == "__main__":
    d, t = parse_irc_date_time("May 27th, 2026 at 2:46 am")
    print(f"Result: Date='{d}', Time='{t}'")
    d, t = parse_irc_date_time("May 3rd, 2026 at 11:15 pm")
    print(f"Result: Date='{d}', Time='{t}'")
