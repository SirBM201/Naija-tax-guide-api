import csv
import re

def format_tags_for_postgres(tags_str):
    """
    Convert tags like '["vat","value added tax","definition"]' 
    to PostgreSQL array literal: '{"vat","value added tax","definition"}'
    """
    # Remove outer brackets and quotes
    tags_str = tags_str.strip('[]')
    # Split by comma, but careful: commas inside quoted strings
    # Simple approach: use regex to find all quoted strings
    items = re.findall(r'"([^"]*)"', tags_str)
    # Escape double quotes inside items (if any)
    items = [item.replace('"', '\\"') for item in items]
    # Build PostgreSQL array literal: {item1,item2,item3}
    array_literal = '{' + ','.join(f'"{item}"' if ' ' in item else item for item in items) + '}'
    return f"'{array_literal}'"

# Read your original CSV (the one you shared)
input_file = 'tax_guide.csv'   # change to your file name
output_prefix = 'batch_'

rows = []
with open(input_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        # Escape single quotes in text fields
        for key in ['question', 'normalized_question', 'answer']:
            if row.get(key):
                row[key] = row[key].replace("'", "''")
        # Format tags for PostgreSQL text[]
        row['tags'] = format_tags_for_postgres(row.get('tags', '[]'))
        rows.append(row)

# Split into 5 batches
batch_size = (len(rows) + 4) // 5
batches = [rows[i:i+batch_size] for i in range(0, len(rows), batch_size)]

for idx, batch in enumerate(batches, start=1):
    with open(f'{output_prefix}{idx}.sql', 'w', encoding='utf-8') as f:
        f.write("INSERT INTO library (id, category, question, normalized_question, answer, tags, priority, enabled, source, created_at, updated_at, answer_en, answer_pcm, answer_yo, answer_ig, answer_ha, answer_pidgin, answer_yoruba, answer_igbo, answer_hausa, canonical_key) VALUES\n")
        for i, row in enumerate(batch):
            f.write("(")
            f.write(f"'{row['id']}', '{row['category']}', '{row['question']}', '{row['normalized_question']}', '{row['answer']}', {row['tags']}, {row['priority']}, {row['enabled']}, '{row['source']}', '{row['created_at']}', '{row['updated_at']}', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{row['canonical_key']}')")
            if i < len(batch)-1:
                f.write(",\n")
            else:
                f.write(";\n")
        print(f"Created {output_prefix}{idx}.sql")

print("Done. Run the SQL files in order after deleting existing rows.")
