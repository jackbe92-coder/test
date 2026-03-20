import re

p = open('tassie_horses.txt', 'r', encoding='utf-8').read()

# Fix any slugs that got joined to the previous line (Windows echo quirk)
for fix in ['alaphilippe', 'colby-sanz', 'debt-till-we-part', 'rockandahardplace']:
    p = p.replace(fix, '\n' + fix)

# Clean up excessive blank lines
p = re.sub(r'\n{3,}', '\n\n', p).strip() + '\n'

# Also strip trailing whitespace from each slug line
lines = p.splitlines()
cleaned = []
for line in lines:
    if line.strip() and not line.strip().startswith('#'):
        cleaned.append(line.strip())  # remove trailing spaces
    else:
        cleaned.append(line)
p = '\n'.join(cleaned) + '\n'

open('tassie_horses.txt', 'w', encoding='utf-8').write(p)
print('Fixed tassie_horses.txt')

# Confirm the last few lines look right
lines = p.splitlines()
print('Last 6 lines:')
for l in lines[-6:]:
    print(f'  {repr(l)}')
