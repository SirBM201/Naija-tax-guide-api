import csv
import re

def format_tags_for_postgres(tags_str):
    tags_str = tags_str.strip('[]')
    items = re.findall(r'"([^"]*)"', tags_str)
    items = [item.replace('"', '\\"') for item in items]
    array_literal = '{' + ','.join(f'"{item}"' if ' ' in item else item for item in items) + '}'
    return f"'{array_literal}'"

# Change this to your actual CSV filename
input_file = 'tax guide.csv'   # <-- note the space
output_prefix = 'batch_'

rows = []
with open(input_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        for key in ['question', 'normalized_question', 'answer']:
            if row.get(key):
                row[key] = row[key].replace("'", "''")
        row['tags'] = format_tags_for_postgres(row.get('tags', '[]'))
        rows.append(row)

batch_size = (len(rows) + 4) // 5
batches = [rows[i:i+batch_size] for i in range(0, len(rows), batch_size)]

for idx, batch in enumerate(batches, start=1):
    with open(f'{output_prefix}{idx}.sql', 'w', encoding='utf-8') as f:
        f.write("INSERT INTO qa_library (id, category, question, normalized_question, answer, tags, priority, enabled, source, created_at, updated_at, answer_en, answer_pcm, answer_yo, answer_ig, answer_ha, answer_pidgin, answer_yoruba, answer_igbo, answer_hausa, canonical_key) VALUES\n")
        for i, row in enumerate(batch):
            f.write("(")
            f.write(f"'{row['id']}', '{row['category']}', '{row['question']}', '{row['normalized_question']}', '{row['answer']}', {row['tags']}, {row['priority']}, {row['enabled']}, '{row['source']}', '{row['created_at']}', '{row['updated_at']}', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{row['canonical_key']}')")
            if i < len(batch)-1:
                f.write(",\n")
            else:
                f.write(";\n")
        print(f"Created {output_prefix}{idx}.sql")

print("Done. Run the SQL files in Supabase after DELETE FROM qa_library;")
