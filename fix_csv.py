import csv
import json
import re

def format_tags(tags_str):
    """Convert JSON array to PostgreSQL text[] format."""
    try:
        items = json.loads(tags_str)
    except:
        items = re.findall(r'"([^"]*)"', tags_str)
    items = [item for item in items if item and item.strip()]
    items = [item.replace('"', '\\"') for item in items]
    return '{' + ','.join(f'"{item}"' for item in items) + '}'

def normalize_question(q):
    if not q:
        return ""
    q = q.lower().strip()
    q = re.sub(r'\(ref\s*[\d\s,]+\)', '', q)  # remove (ref ₦250,000)
    q = re.sub(r'\(month\s+\w+\)', '', q)     # remove (month March)
    q = re.sub(r'\(state\s+[\w\s]+\)', '', q) # remove (state Lagos)
    q = re.sub(r'\s+', ' ', q).strip()
    return q

input_file = 'tax guide.csv'   # your original file
output_file = 'tax_guide_normalized_full.csv'

with open(input_file, 'r', encoding='utf-8') as infile, open(output_file, 'w', encoding='utf-8', newline='') as outfile:
    reader = csv.DictReader(infile)
    # Get all original column names
    fieldnames = reader.fieldnames
    # Ensure we have all required columns
    out_writer = csv.DictWriter(outfile, fieldnames=fieldnames)
    out_writer.writeheader()
    
    for row in reader:
        # Normalize question
        if row.get('normalized_question'):
            row['normalized_question'] = normalize_question(row['normalized_question'])
        # Format tags
        if row.get('tags'):
            row['tags'] = format_tags(row['tags'])
        # Keep other columns as is
        out_writer.writerow(row)

print(f"Done! Created {output_file} with {sum(1 for _ in open(output_file)) - 1} rows.")
